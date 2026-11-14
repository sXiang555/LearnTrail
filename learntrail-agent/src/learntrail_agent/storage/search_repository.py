"""Exact cosine and BM25 retrieval over server-authorized index generations."""

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from learntrail_agent.clients.embedding import validate_vector
from learntrail_agent.errors import AppError
from learntrail_agent.rag.bm25 import TOKENIZER_VERSION, term_counts
from learntrail_agent.rag.schemas import SearchHit
from learntrail_agent.storage.pool import DatabasePool, transaction
from learntrail_agent.storage.vector_repository import IndexState

# Both user and generation are joined. A concurrent replacement cannot expose a
# generation which was not included in the Java authorization pass.
CORPUS = """
 SELECT c.*, i.slug, i.content_hash, i.source_metadata->>'source_type' AS source_type,
        coalesce(i.source_metadata->>'source_url', '') AS source_url
 FROM ai_article_chunks c
 JOIN ai_article_indexes i ON i.user_id=c.user_id AND i.article_id=c.article_id
   AND i.generation=c.generation AND i.dimensions=c.dimensions
 JOIN unnest(%s::bigint[], %s::uuid[]) a(article_id,generation)
   ON a.article_id=c.article_id AND a.generation=c.generation
 WHERE c.user_id=%s AND i.active
"""


def scope(states: list[IndexState], user_id: int) -> tuple[list[int], list[UUID], int]:
    return [s.article_id for s in states], [s.generation for s in states], user_id


class SearchRepository:
    def __init__(self, pool: DatabasePool | None):
        self.pool = pool

    async def active_indexes(self, user_id: int, slugs: list[str] | None, maximum: int) -> list[IndexState]:
        async with transaction(self.pool) as conn:
            rows = await (await conn.execute(
                """SELECT * FROM ai_article_indexes WHERE user_id=%s AND active
                   AND (%s::text[] IS NULL OR slug=ANY(%s::text[])) ORDER BY article_id LIMIT %s""",
                (user_id, slugs, slugs, maximum + 1),
            )).fetchall()
        if len(rows) > maximum:
            raise AppError("RAG_SCOPE_TOO_LARGE", "知识范围过大，请指定文章范围。", 422)
        return [IndexState.model_validate(row) for row in rows]

    async def backfill_keywords(self, user_id: int, states: list[IndexState]) -> None:
        if not states:
            return
        async with transaction(self.pool) as conn:
            rows = await (await conn.execute(
                "SELECT * FROM (" + CORPUS + ") corpus WHERE tokenizer_version<>%s",
                (*scope(states, user_id), TOKENIZER_VERSION),
            )).fetchall()
            async with conn.cursor() as cursor:
                values = []
                for row in rows:
                    terms = term_counts(row["title"] + "\n" + row["section"] + "\n" + row["content"])
                    values.append((Jsonb(terms), sum(terms.values()), TOKENIZER_VERSION,
                                   user_id, row["article_id"], row["generation"], row["ordinal"]))
                if values:
                    await cursor.executemany(
                        """UPDATE ai_article_chunks SET bm25_terms=%s,bm25_length=%s,tokenizer_version=%s
                           WHERE user_id=%s AND article_id=%s AND generation=%s AND ordinal=%s""", values,
                    )

    async def vector(
        self, user_id: int, states: list[IndexState], embedding: list[float], dimensions: int,
        minimum: float, limit: int,
    ) -> list[SearchHit]:
        if not states:
            return []
        vector = json.dumps(validate_vector(embedding, dimensions), allow_nan=False)
        async with transaction(self.pool) as conn:
            rows = await (await conn.execute(
                "WITH corpus AS MATERIALIZED (" + CORPUS + " AND c.dimensions=%s), "
                "scored AS (SELECT *, 1-(embedding <=> %s::vector) AS score FROM corpus) "
                "SELECT * FROM scored WHERE score>=%s ORDER BY score DESC,article_id,ordinal LIMIT %s",
                (*scope(states, user_id), dimensions, vector, minimum, limit),
            )).fetchall()
        return [SearchHit.model_validate({**r, "vector_score": r["score"]}) for r in rows]

    async def bm25(
        self, user_id: int, states: list[IndexState], tokens: list[str], k1: float, b: float,
        minimum: float, limit: int,
    ) -> list[SearchHit]:
        if not states or not tokens:
            return []
        # Document frequencies and length normalization use the whole authorized
        # corpus, not merely the query's matching documents. Query terms are unique.
        async with transaction(self.pool) as conn:
            rows = await (await conn.execute(
                "WITH corpus AS MATERIALIZED (" + CORPUS + " AND c.tokenizer_version=%s), "
                "stats AS (SELECT count(*)::float8 AS n, avg(bm25_length)::float8 AS avgdl FROM corpus), "
                "df AS (SELECT q.term,count(*)::float8 AS n FROM corpus c "
                "CROSS JOIN unnest(%s::text[]) q(term) WHERE c.bm25_terms ? q.term GROUP BY q.term) "
                "SELECT c.*,ranking.score FROM corpus c CROSS JOIN LATERAL ("
                " SELECT sum(ln(1+(stats.n-df.n+0.5)/(df.n+0.5)) * "
                " (c.bm25_terms->>df.term)::float8 * (%s+1) / "
                " ((c.bm25_terms->>df.term)::float8 + %s*(1-%s+%s*c.bm25_length/nullif(stats.avgdl,0)))) AS score "
                " FROM df CROSS JOIN stats WHERE c.bm25_terms ? df.term) ranking "
                "WHERE c.bm25_terms ?| %s::text[] AND ranking.score>%s "
                "ORDER BY ranking.score DESC,c.article_id,c.ordinal LIMIT %s",
                (*scope(states, user_id), TOKENIZER_VERSION, sorted(set(tokens)), k1, k1, b, b,
                 sorted(set(tokens)), minimum, limit),
            )).fetchall()
        return [SearchHit.model_validate({**r, "bm25_score": r["score"]}) for r in rows]
