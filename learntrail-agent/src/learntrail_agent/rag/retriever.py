"""Permission-first vector/BM25 recall, reciprocal-rank fusion and optional rerank."""

import asyncio
import time
from uuid import uuid4

from learntrail_agent.clients.embedding import EmbeddingClient
from learntrail_agent.clients.java import ArticleUnavailable, JavaArticleClient
from learntrail_agent.clients.reranker import RerankerClient
from learntrail_agent.errors import AppError
from learntrail_agent.rag.bm25 import tokenize
from learntrail_agent.rag.indexer import ArticleIndexer
from learntrail_agent.rag.schemas import SearchHit, SearchRequest, SearchResponse
from learntrail_agent.settings import Settings
from learntrail_agent.storage.search_repository import SearchRepository
from learntrail_agent.storage.vector_repository import IndexState, VectorRepository
from learntrail_agent.observability import observed


def reciprocal_rank_fusion(lists: list[list[SearchHit]], k: int) -> list[SearchHit]:
    fused: dict[tuple, SearchHit] = {}
    for hits in lists:
        seen: set[tuple] = set()
        for rank, hit in enumerate(hits, 1):
            if hit.key in seen:
                continue
            seen.add(hit.key)
            current = fused.get(hit.key, hit.model_copy(update={"score": 0.0, "rrf_score": 0.0}))
            score = current.score + 1 / (k + rank)
            fused[hit.key] = current.model_copy(update={
                "score": score, "rrf_score": score,
                "vector_score": hit.vector_score if hit.vector_score is not None else current.vector_score,
                "bm25_score": hit.bm25_score if hit.bm25_score is not None else current.bm25_score,
            })
    return sorted(fused.values(), key=lambda h: (-h.score, h.article_id, h.ordinal))


class Retriever:
    def __init__(
        self, repository: SearchRepository, vectors: VectorRepository, java: JavaArticleClient,
        indexer: ArticleIndexer, embedding: EmbeddingClient, reranker: RerankerClient, settings: Settings,
    ):
        self.repository, self.vectors, self.java, self.indexer = repository, vectors, java, indexer
        self.embedding, self.reranker, self.settings = embedding, reranker, settings

    async def _validate(
        self, user_id: int, states: list[IndexState], token: str,
    ) -> list[tuple[IndexState, str]]:
        semaphore = asyncio.Semaphore(4)

        async def check(state: IndexState) -> tuple[IndexState, str] | None:
            async with semaphore:
                if state.user_id != user_id:
                    raise AppError("RAG_INVALID_SCOPE", "知识库归属不一致。", 403)
                try:
                    source = await self.java.get_published_article(state.slug, token)
                    if source.user_id != user_id:
                        raise AppError("INVALID_TOKEN", "登录身份不一致。", 401)
                    doc, fingerprint, metadata = self.indexer.prepare_source(source)
                    valid = (source.article.id == state.article_id and doc.content_hash == state.content_hash
                             and metadata == state.source_metadata)
                except (ArticleUnavailable, ValueError):
                    valid = False
                if not valid:
                    await self.vectors.invalidate_generation(user_id, state.article_id, state.generation)
                    return None
                current = await self.vectors.get_state(user_id, state.article_id)
                if current is None or not current.active or current.generation != state.generation:
                    return None
                return state, fingerprint

        return [item for item in await asyncio.gather(*(check(s) for s in states)) if item is not None]

    async def revalidate(self, user_id: int, hits: list[SearchHit], token: str) -> list[SearchHit]:
        unique = {h.article_id: h for h in hits}
        states = []
        for article_id, hit in unique.items():
            state = await self.vectors.get_state(user_id, article_id)
            if (state is not None and state.active and state.generation == hit.generation
                    and state.content_hash == hit.content_hash):
                states.append(state)
        valid = {(s.article_id, s.generation) for s, _ in await self._validate(user_id, states, token)}
        return [h for h in hits if (h.article_id, h.generation) in valid]

    @observed("rag.search","retriever")
    async def search(self, user_id: int, request: SearchRequest, token: str) -> SearchResponse:
        started = time.monotonic()
        s = self.settings
        if request.rerank:
            self.reranker.check_configuration()
        tokens = tokenize(request.query)
        if request.mode != "vector" and len(set(tokens)) > 256:
            raise AppError("RAG_QUERY_TOO_LONG", "关键词查询过长，请缩短问题。", 422)
        states = await self.repository.active_indexes(user_id, request.article_slugs, s.rag_max_articles)
        checked = await self._validate(user_id, states, token)
        all_states = [state for state, _ in checked]
        if not all_states:
            return SearchResponse(request_id=uuid4(), mode=request.mode, vector=[], bm25=[], fused=[], results=[],
                reranked=False, duration_ms=max(0, int((time.monotonic() - started) * 1000)))
        vector, bm25, fused = [], [], []
        warnings: list[str] = []
        candidate_k = max(s.rag_candidate_k, request.top_k or s.rag_top_k)
        reranked = False
        usage = None
        if request.mode in {"vector", "hybrid"}:
            self.embedding.check_configuration()
            # A vector profile includes URL/model/revision/dimensions/chunk settings.
            # BM25 deliberately remains usable without any embedding configuration.
            compatible = [state for state, fingerprint in checked if state.fingerprint == fingerprint]
            if len(compatible) != len(checked):
                warnings.append("EMBEDDING_PROFILE_MISMATCH_REINDEX_REQUIRED")
            if compatible:
                batch = await self.embedding.embed(request.query)
                usage = batch.usage
                vector = await self.repository.vector(user_id, compatible, batch.vectors[0],
                    s.embedding_dimensions, s.rag_min_similarity, candidate_k)
        if request.mode in {"bm25", "hybrid"}:
            await self.repository.backfill_keywords(user_id, all_states)
            bm25 = await self.repository.bm25(user_id, all_states, tokens, s.rag_bm25_k1,
                                             s.rag_bm25_b, s.rag_bm25_min_score, candidate_k)
        if request.mode == "hybrid":
            fused = reciprocal_rank_fusion([vector, bm25], s.rag_rrf_k)
        ranked = (fused if request.mode == "hybrid" else vector if request.mode == "vector" else bm25)
        # Recheck before sending any snippets to an external reranker.
        permitted = await self.revalidate(user_id, [*vector, *bm25], token)
        keys = {h.key for h in permitted}
        vector, bm25, fused, ranked = ([h for h in items if h.key in keys]
                                      for items in (vector, bm25, fused, ranked))
        ranked = ranked[:candidate_k]
        if request.rerank and ranked:
            scores = await self.reranker.rerank(request.query, [h.content for h in ranked])
            reranked = True
            ranked = [ranked[r.index].model_copy(update={"score": r.relevance_score,
                      "reranker_score": r.relevance_score}) for r in scores]
        # Includes all returned stage lists: diagnostics cannot leak a rejected source.
        permitted = await self.revalidate(user_id, [*vector, *bm25], token)
        keys = {h.key for h in permitted}
        vector, bm25, fused, ranked = ([h for h in items if h.key in keys]
                                      for items in (vector, bm25, fused, ranked))
        return SearchResponse(request_id=uuid4(), mode=request.mode, vector=vector, bm25=bm25, fused=fused,
            results=ranked[:request.top_k or s.rag_top_k], reranked=reranked, warnings=warnings,
            embedding_usage=usage, duration_ms=max(0, int((time.monotonic() - started) * 1000)))
