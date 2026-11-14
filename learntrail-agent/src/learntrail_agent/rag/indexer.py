"""Published article -> canonical text -> chunks -> embeddings -> atomic index."""

import asyncio
import hashlib
import json
import logging
import time
import unicodedata
from typing import Literal

import anyio
from pydantic import BaseModel

from learntrail_agent.clients.embedding import EmbeddingClient, EmbeddingUsage
from learntrail_agent.clients.java import ArticleUnavailable, AuthorizedArticle, JavaArticleClient
from learntrail_agent.errors import AppError
from learntrail_agent.rag.chunker import split_document
from learntrail_agent.rag.cleaner import CleanedDocument, clean_document
from learntrail_agent.settings import Settings
from learntrail_agent.storage.vector_repository import IndexClaim, VectorRepository

logger = logging.getLogger(__name__)


class IndexResult(BaseModel):
    status: Literal["indexed", "skipped", "invalidated"]
    slug: str
    article_id: int | None = None
    content_hash: str | None = None
    chunk_count: int = 0
    usage: EmbeddingUsage | None = None
    duration_ms: int


class ArticleIndexer:
    def __init__(self, java: JavaArticleClient, embedding: EmbeddingClient,
                 repository: VectorRepository, settings: Settings):
        self.java, self.embedding, self.repository, self.settings = java, embedding, repository, settings

    def prepare_source(self, source: AuthorizedArticle) -> tuple[CleanedDocument, str, dict[str, object]]:
        article = source.article
        if article.status != "published":
            raise ArticleUnavailable(source.user_id, article.slug)
        document = clean_document(article.title, article.body, article.content_format)
        metadata = {
            "content_format": article.content_format, "source_type": article.source_type,
            "source_author": article.source_author, "source_url": article.source_url,
            "license_note": article.license_note, "summary": " ".join(article.summary.split()),
        }
        metadata = {key: unicodedata.normalize("NFC", value).replace("\x00", "").strip()
                    for key, value in metadata.items()}
        profile = {
            "pipeline": "learntrail-article-v1", "content_hash": document.content_hash, "source": metadata,
            "base_url": self.settings.embedding_base_url, "model": self.settings.embedding_model,
            "revision": self.settings.embedding_model_revision, "dimensions": self.settings.embedding_dimensions,
            "send_dimensions": self.settings.embedding_send_dimensions,
            "chunk_size": self.settings.rag_chunk_size, "chunk_overlap": self.settings.rag_chunk_overlap,
        }
        fingerprint = hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode("utf-8")).hexdigest()
        return document, fingerprint, metadata

    async def index_article(self, slug: str, token: str) -> IndexResult:
        """Internal service entry: identity is derived from this signed Java access token."""
        started = time.monotonic()
        claim: IndexClaim | None = None
        settled = False
        cancelled = False
        try:
            async with asyncio.timeout(self.settings.rag_index_timeout_seconds):
                source = await self.java.get_published_article(slug, token)
                try:
                    document, fingerprint, metadata = self.prepare_source(source)
                except ValueError:
                    await self.repository.invalidate(source.user_id, slug)
                    raise AppError("ARTICLE_NOT_INDEXABLE", "文章没有可索引的有效正文。", 422) from None
                self.embedding.check_configuration()
                claim = await self.repository.reserve(
                    source.user_id, source.article, document.content_hash, fingerprint,
                    self.settings.rag_index_timeout_seconds + 60,
                )
                if claim.skipped:
                    return IndexResult(status="skipped", slug=slug, article_id=source.article.id,
                                       content_hash=document.content_hash, chunk_count=claim.chunk_count,
                                       duration_ms=max(0, int((time.monotonic()-started)*1000)))
                try:
                    chunks = split_document(document, source.article.id, slug,
                                            self.settings.rag_chunk_size, self.settings.rag_chunk_overlap)
                except ValueError:
                    raise AppError("ARTICLE_CHUNK_LIMIT", "文章切分超出限制，请调整 Chunk 配置。", 422) from None
                batch = await self.embedding.embed([chunk.text for chunk in chunks])
                fresh = await self.java.get_published_article(slug, token)
                try:
                    _, fresh_fingerprint, _ = self.prepare_source(fresh)
                except ValueError:
                    fresh_fingerprint = ""
                if (fresh.user_id != source.user_id or fresh.article.id != source.article.id
                        or fresh_fingerprint != fingerprint):
                    await self.repository.invalidate(source.user_id, slug)
                    settled = True
                    raise AppError("ARTICLE_CHANGED_DURING_INDEX", "文章已变化，请重新索引。", 409, True)
                await self.repository.replace(claim, fresh.article, chunks, batch, self.settings.embedding_dimensions,
                                              self.settings.embedding_model, metadata)
                settled = True
                return IndexResult(status="indexed", slug=slug, article_id=source.article.id,
                                   content_hash=document.content_hash, chunk_count=len(chunks), usage=batch.usage,
                                   duration_ms=max(0, int((time.monotonic()-started)*1000)))
        except ArticleUnavailable as error:
            with anyio.CancelScope(shield=True):
                await self.repository.invalidate(error.user_id, error.slug)
            settled = True
            return IndexResult(status="invalidated", slug=slug,
                               duration_ms=max(0, int((time.monotonic()-started)*1000)))
        except TimeoutError:
            raise AppError("ARTICLE_INDEX_TIMEOUT", "文章索引超时，未发布不完整索引。", 504, True) from None
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if claim is not None and not claim.skipped and not settled:
                with anyio.CancelScope(shield=True):
                    try:
                        await self.repository.release(claim)
                    except Exception as error:
                        logger.error("Index lease cleanup failed: %s", type(error).__name__)
                        if not cancelled:
                            raise AppError("ARTICLE_INDEX_STATE_UNCERTAIN", "索引租约暂时无法释放，请稍后重试。", 503, True) from None
