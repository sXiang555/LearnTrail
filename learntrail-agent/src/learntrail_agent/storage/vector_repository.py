"""User-scoped index leases and atomic pgvector generation replacement."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from learntrail_agent.clients.embedding import EmbeddingBatch, validate_vector
from learntrail_agent.clients.java import PublishedArticle
from learntrail_agent.errors import AppError
from learntrail_agent.rag.chunker import ArticleChunk
from learntrail_agent.rag.bm25 import TOKENIZER_VERSION, term_counts
from learntrail_agent.storage.pool import DatabasePool, transaction


class IndexState(BaseModel):
    user_id: int
    article_id: int
    slug: str
    active: bool
    content_hash: str | None
    fingerprint: str | None
    generation: UUID | None
    dimensions: int | None
    chunk_count: int
    source_metadata: dict[str, object] = Field(default_factory=dict)
    embedding_model: str | None = None


class StoredChunk(BaseModel):
    article_id: int
    slug: str
    generation: UUID
    ordinal: int
    title: str
    section: str
    start_char: int
    end_char: int
    content: str
    dimensions: int
    embedding: list[float]


@dataclass(frozen=True)
class IndexClaim:
    user_id: int
    article_id: int
    slug: str
    content_hash: str
    fingerprint: str
    token: UUID | None
    chunk_count: int = 0

    @property
    def skipped(self) -> bool:
        return self.token is None


class VectorRepository:
    def __init__(self, pool: DatabasePool | None):
        self.pool = pool

    async def get_state(self, user_id: int, article_id: int) -> IndexState | None:
        async with transaction(self.pool) as conn:
            row = await (await conn.execute(
                "SELECT * FROM ai_article_indexes WHERE user_id = %s AND article_id = %s", (user_id, article_id),
            )).fetchone()
            return IndexState.model_validate(row) if row is not None else None

    async def reserve(
        self, user_id: int, article: PublishedArticle, content_hash: str, fingerprint: str, lease_seconds: float,
    ) -> IndexClaim:
        async with transaction(self.pool) as conn:
            await conn.execute(
                """INSERT INTO ai_article_indexes (user_id, article_id, slug, title) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, article_id) DO NOTHING""",
                (user_id, article.id, article.slug, article.title.replace("\x00", "")),
            )
            row = await (await conn.execute(
                """SELECT *, pending_expires_at > clock_timestamp() AS pending_live FROM ai_article_indexes
                   WHERE user_id = %s AND article_id = %s FOR UPDATE""", (user_id, article.id),
            )).fetchone()
            if row["pending_live"]:
                raise AppError("ARTICLE_INDEX_BUSY", "文章正在索引，请稍后重试。", 409, True)
            if row["active"] and row["fingerprint"] == fingerprint:
                await conn.execute(
                    "UPDATE ai_article_indexes SET verified_at = clock_timestamp() WHERE user_id = %s AND article_id = %s",
                    (user_id, article.id),
                )
                return IndexClaim(user_id, article.id, article.slug, content_hash, fingerprint, None, row["chunk_count"])
            token = uuid4()
            await conn.execute(
                """UPDATE ai_article_indexes SET pending_token = %s, pending_fingerprint = %s,
                   pending_expires_at = clock_timestamp() + %s * interval '1 second'
                   WHERE user_id = %s AND article_id = %s""", (token, fingerprint, lease_seconds, user_id, article.id),
            )
            return IndexClaim(user_id, article.id, article.slug, content_hash, fingerprint, token)

    async def release(self, claim: IndexClaim) -> None:
        if claim.skipped:
            return
        async with transaction(self.pool) as conn:
            await conn.execute(
                """UPDATE ai_article_indexes SET pending_token = NULL, pending_fingerprint = NULL,
                   pending_expires_at = NULL WHERE user_id = %s AND article_id = %s AND pending_token = %s""",
                (claim.user_id, claim.article_id, claim.token),
            )

    async def invalidate(self, user_id: int, slug: str) -> None:
        async with transaction(self.pool) as conn:
            await conn.execute(
                """UPDATE ai_article_indexes SET active = FALSE, pending_token = NULL, pending_fingerprint = NULL,
                   pending_expires_at = NULL, verified_at = clock_timestamp() WHERE user_id = %s AND slug = %s""",
                (user_id, slug),
            )

    async def invalidate_generation(self, user_id: int, article_id: int, generation: UUID) -> None:
        async with transaction(self.pool) as conn:
            await conn.execute(
                """UPDATE ai_article_indexes SET active = FALSE, pending_token = NULL,
                   pending_fingerprint = NULL, pending_expires_at = NULL, verified_at = clock_timestamp()
                   WHERE user_id = %s AND article_id = %s AND generation = %s""",
                (user_id, article_id, generation),
            )

    async def replace(
        self, claim: IndexClaim, article: PublishedArticle, chunks: list[ArticleChunk], batch: EmbeddingBatch,
        dimensions: int, embedding_model: str, metadata: dict[str, object],
    ) -> None:
        if (claim.skipped or not chunks or len(chunks) != len(batch.vectors)
                or article.id != claim.article_id or article.slug != claim.slug):
            raise AppError("INVALID_INDEX_BATCH", "索引批次不完整。", 422)
        rows = []
        for ordinal, (chunk, vector) in enumerate(zip(chunks, batch.vectors, strict=True)):
            if (chunk.ordinal != ordinal or chunk.article_id != article.id or chunk.slug != article.slug
                    or chunk.end - chunk.start != len(chunk.text) or not 0 < len(chunk.text) <= 8000):
                raise AppError("INVALID_INDEX_BATCH", "索引块元数据不一致。", 422)
            values = validate_vector(vector, dimensions)
            terms = term_counts(chunk.title + "\n" + chunk.section + "\n" + chunk.text)
            rows.append((claim.user_id, article.id, claim.token, ordinal, chunk.title, chunk.section,
                         chunk.start, chunk.end, chunk.text, Jsonb(terms), sum(terms.values()), TOKENIZER_VERSION,
                         dimensions, json.dumps(values, allow_nan=False)))
        async with transaction(self.pool) as conn:
            current = await (await conn.execute(
                """SELECT pending_token, pending_fingerprint, pending_expires_at > clock_timestamp() AS pending_live
                   FROM ai_article_indexes WHERE user_id = %s AND article_id = %s FOR UPDATE""",
                (claim.user_id, claim.article_id),
            )).fetchone()
            if (current is None or current["pending_token"] != claim.token
                    or current["pending_fingerprint"] != claim.fingerprint or not current["pending_live"]):
                raise AppError("ARTICLE_INDEX_SUPERSEDED", "索引任务已失效，请重新检查文章。", 409, True)
            await conn.execute("DELETE FROM ai_article_chunks WHERE user_id = %s AND article_id = %s",
                               (claim.user_id, article.id))
            await conn.execute(
                """UPDATE ai_article_indexes SET slug = %s, title = %s, content_hash = %s, fingerprint = %s,
                   generation = %s, dimensions = %s, embedding_model = %s, provider_model = %s,
                   source_metadata = %s, embedding_usage = %s, chunk_count = %s, active = TRUE,
                   pending_token = NULL, pending_fingerprint = NULL, pending_expires_at = NULL,
                   indexed_at = clock_timestamp(), verified_at = clock_timestamp()
                   WHERE user_id = %s AND article_id = %s""",
                (article.slug, chunks[0].title, claim.content_hash, claim.fingerprint, claim.token, dimensions,
                 embedding_model, batch.model, Jsonb(metadata), Jsonb(batch.usage.model_dump()) if batch.usage else None,
                 len(chunks), claim.user_id, article.id),
            )
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    """INSERT INTO ai_article_chunks (user_id, article_id, generation, ordinal, title, section,
                       start_char, end_char, content, bm25_terms, bm25_length, tokenizer_version, dimensions, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)""", rows,
                )

    async def get_chunks(self, user_id: int, article_id: int) -> list[StoredChunk]:
        """Internal storage read; callers must still reauthorize with Java before future retrieval."""
        async with transaction(self.pool) as conn:
            rows = await (await conn.execute(
                """SELECT c.*, i.slug, c.embedding::text AS vector_text FROM ai_article_chunks c
                   JOIN ai_article_indexes i ON i.user_id = c.user_id AND i.article_id = c.article_id
                     AND i.generation = c.generation AND i.dimensions = c.dimensions
                   WHERE c.user_id = %s AND c.article_id = %s AND i.active ORDER BY c.ordinal""", (user_id, article_id),
            )).fetchall()
            return [StoredChunk.model_validate({**row, "embedding": json.loads(row["vector_text"])}) for row in rows]
