"""Retrieval/QA boundary tests; network and storage doubles are explicit."""

import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from learntrail_agent.clients.embedding import EmbeddingBatch, EmbeddingClient
from learntrail_agent.clients.java import ArticleUnavailable, AuthorizedArticle, JavaArticleClient, PublishedArticle
from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.clients.reranker import RerankerClient, RerankScore
from learntrail_agent.errors import AppError
from learntrail_agent.rag.bm25 import bm25_score, tokenize
from learntrail_agent.rag.indexer import ArticleIndexer
from learntrail_agent.rag.evaluation import metrics
from learntrail_agent.rag.qa import NO_ANSWER, RagService, assemble_context
from learntrail_agent.rag.retriever import Retriever, reciprocal_rank_fusion
from learntrail_agent.rag.schemas import SearchHit, SearchRequest, SearchResponse
from learntrail_agent.schemas import ChatResponse
from learntrail_agent.settings import Settings
from learntrail_agent.storage.repositories import SessionRepository
from learntrail_agent.storage.search_repository import SearchRepository
from learntrail_agent.storage.vector_repository import IndexState, VectorRepository


def hit(**changes: object) -> SearchHit:
    values = dict(article_id=42, slug="article-42", generation=uuid4(), ordinal=0, title="钱包事务",
                  section="行锁", start_char=0, end_char=7, content="钱包使用行锁。", content_hash="a"*64,
                  source_type="original", score=0.8, vector_score=0.8)
    return SearchHit(**{**values, **changes})


def test_chinese_bigrams_and_mixed_identifier_tokenization() -> None:
    assert tokenize("学习权益") == ["学习", "习权", "权益"]
    assert tokenize("Redis中文JWT １２３") == ["redis", "中文", "jwt", "123"]
    assert tokenize("我") == ["我"]
    assert tokenize("   !!!") == []


def test_bm25_uses_tf_idf_and_length_normalization() -> None:
    args = dict(query=["事务"], frequencies={"事务": 2}, documents=10, average_length=10)
    assert bm25_score({"事务": 3}, **args) > bm25_score({"事务": 1}, **args) > 0
    assert bm25_score({"事务": 1}, **args) > bm25_score({"事务": 1, "其他": 50}, **args)
    assert bm25_score({"其他": 5}, **args) == 0
    assert bm25_score({"事务": 1}, **{**args, "frequencies": {"事务": 9}}) < bm25_score({"事务": 1}, **args)


def test_rrf_keeps_independent_scores_and_deduplicates() -> None:
    first = hit()
    second = hit(article_id=43, slug="article-43")
    lexical = second.model_copy(update={"vector_score": None, "bm25_score": 12.0})
    result = reciprocal_rank_fusion([[first, second], [lexical, first]], 60)
    assert result[0].score == pytest.approx(1/61 + 1/62)
    assert result[1].bm25_score == 12.0
    assert first.score == .8 and first.rrf_score is None
    assert len(reciprocal_rank_fusion([[first, first]], 60)) == 1


def setup(settings: Settings) -> tuple:
    java = AsyncMock(spec=JavaArticleClient)
    embedding = Mock(spec=EmbeddingClient)
    embedding.embed = AsyncMock(return_value=EmbeddingBatch(vectors=[[1, 0, 0]], model="test", usage=None))
    reranker = Mock(spec=RerankerClient)
    repo = AsyncMock(spec=SearchRepository)
    vectors = AsyncMock(spec=VectorRepository)
    indexer = ArticleIndexer(java, embedding, vectors, settings)
    source = AuthorizedArticle(41, PublishedArticle(id=42, slug="article-42", title="钱包事务", body="钱包使用行锁。",
                                                    status="published", contentFormat="text", sourceType="original"))
    doc, fingerprint, metadata = indexer.prepare_source(source)
    item = hit(content_hash=doc.content_hash)
    state = IndexState(user_id=41, article_id=42, slug=item.slug, active=True, content_hash=doc.content_hash,
        fingerprint=fingerprint, generation=item.generation, dimensions=3, chunk_count=1,
        embedding_model="test", source_metadata=metadata)
    java.get_published_article.return_value = source
    repo.active_indexes.return_value = [state]
    vectors.get_state.return_value = state
    repo.vector.return_value = [item]
    repo.bm25.return_value = [item.model_copy(update={"vector_score": None, "bm25_score": 2.0})]
    return Retriever(repo, vectors, java, indexer, embedding, reranker, settings), item, state, source


@pytest.mark.asyncio
async def test_bm25_is_independent_of_embedding_configuration(settings: Settings) -> None:
    retriever, item, _, _ = setup(settings)
    result = await retriever.search(41, SearchRequest(query="钱包行锁", mode="bm25"), "test-token")
    assert result.results[0].key == item.key and result.vector == [] and result.fused == []
    retriever.embedding.embed.assert_not_called()
    retriever.repository.backfill_keywords.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["archived", "changed_body", "private"])
async def test_invalid_source_is_rejected_before_any_provider(settings: Settings, change: str) -> None:
    r, _, state, source = setup(settings)
    if change == "changed_body":
        r.java.get_published_article.return_value = AuthorizedArticle(41, source.article.model_copy(update={"body":"新版内容"}))
    else:
        r.java.get_published_article.side_effect = ArticleUnavailable(41, state.slug)
    result = await r.search(41, SearchRequest(query="行锁"), "test-token")
    assert result.results == result.vector == result.bm25 == []
    r.embedding.embed.assert_not_called()
    r.vectors.invalidate_generation.assert_awaited_once_with(41, 42, state.generation)


@pytest.mark.asyncio
async def test_java_failure_is_closed_not_cached_as_permission(settings: Settings) -> None:
    r, _, _, _ = setup(settings)
    r.java.get_published_article.side_effect = AppError("JAVA_ARTICLE_UNAVAILABLE", "不可用", 503)
    with pytest.raises(AppError):
        await r.search(41, SearchRequest(query="行锁"), "test-token")
    r.embedding.embed.assert_not_called()


@pytest.mark.asyncio
async def test_withdrawal_during_embedding_cannot_reach_reranker_or_diagnostics(settings: Settings) -> None:
    r, _, _, _ = setup(settings)
    async def embedding(query: str) -> EmbeddingBatch:
        r.java.get_published_article.side_effect = ArticleUnavailable(41, "article-42")
        return EmbeddingBatch(vectors=[[1, 0, 0]], model="test", usage=None)
    r.embedding.embed.side_effect = embedding
    result = await r.search(41, SearchRequest(query="行锁", rerank=True), "test-token")
    assert result.vector == result.bm25 == result.fused == result.results == []
    r.reranker.rerank.assert_not_called()


@pytest.mark.asyncio
async def test_rerank_changes_final_order_but_preserves_independent_stages(settings: Settings) -> None:
    r, first, _, _ = setup(settings)
    second = first.model_copy(update={"ordinal":1,"content":"第二块","score":.6})
    r.repository.vector.return_value = [first, second]
    r.repository.bm25.return_value = [first, second]
    r.reranker.rerank = AsyncMock(return_value=[RerankScore(index=1,relevance_score=.9),
                                               RerankScore(index=0,relevance_score=.1)])
    result = await r.search(41, SearchRequest(query="行锁", rerank=True), "test-token")
    assert result.reranked and result.results[0].ordinal == 1
    assert result.vector[0].ordinal == 0 and result.fused[0].ordinal == 0
    assert result.results[0].reranker_score == .9


def test_metrics_exclude_empty_qrels_and_do_not_count_duplicates_as_recall() -> None:
    rows = [{"relevant":["a","b"],"ranked":["a","a"]},
            {"relevant":["c"],"ranked":["x","c"]}, {"relevant":[],"ranked":["x"]}]
    result = metrics(rows, 1)
    assert result["positive_cases"] == 2 and result["negative_cases"] == 1
    assert result["recall"] == .25 and result["mrr"] == .5
    assert result["negative_nonempty_rate"] == 1
    assert metrics(rows, 2)["recall"] == .75


def response(item: SearchHit | None = None) -> SearchResponse:
    hits = [item] if item else []
    return SearchResponse(request_id=uuid4(), mode="vector", vector=hits, bm25=[], fused=[],
                          results=hits, reranked=False, duration_ms=1)


def qa(settings: Settings) -> tuple:
    item = hit()
    retriever = AsyncMock(spec=Retriever)
    retriever.search.return_value = response(item)
    retriever.revalidate.return_value = [item]
    llm = AsyncMock(spec=LLMClient)
    llm.complete.return_value = ChatResponse(id="test", model="test", content=json.dumps({
        "status":"answered", "answer":"钱包使用行锁。[S1]", "citation_ids":["S1"]}), finish_reason="stop")
    sessions = AsyncMock(spec=SessionRepository)
    return RagService(retriever, llm, sessions, settings), item


@pytest.mark.asyncio
async def test_answer_returns_exact_source_and_does_not_pollute_chat_history(settings: Settings) -> None:
    service, item = qa(settings)
    result = await service.answer(41, uuid4(), SearchRequest(query="如何加锁？"), "test-token")
    assert result.status == "answered" and result.citations[0].quote == item.content
    assert result.citations[0].start_char == item.start_char and result.citations[0].generation == item.generation
    assert result.citations[0].end_char - result.citations[0].start_char == len(result.citations[0].quote)
    assert result.citations[0].position_basis == "normalized_unicode_codepoints"
    service.sessions.history.assert_not_called()
    service.sessions.complete_run.assert_not_called()
    request = service.llm.complete.await_args.args[0]
    assert "不可信" in request.messages[0].content


@pytest.mark.asyncio
async def test_empty_retrieval_does_not_call_llm(settings: Settings) -> None:
    service, _ = qa(settings)
    service.retriever.search.return_value = response()
    result = await service.answer(41, uuid4(), SearchRequest(query="没有答案"), "test-token")
    assert result.status == "insufficient_context" and result.answer == NO_ANSWER and result.citations == []
    service.llm.complete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["plain text", '{"status":"answered","answer":"编造","citation_ids":[]}',
    '{"status":"answered","answer":"伪造[S9]","citation_ids":["S9"]}',
    '{"status":"answered","answer":"无标记","citation_ids":["S1"]}'])
async def test_invalid_or_hallucinated_citations_are_errors(settings: Settings, content: str) -> None:
    service, _ = qa(settings)
    service.llm.complete.return_value.content = content
    with pytest.raises(AppError) as caught:
        await service.answer(41, uuid4(), SearchRequest(query="问题"), "test-token")
    assert caught.value.code == "RAG_INVALID_ANSWER"


@pytest.mark.asyncio
async def test_source_withdrawn_while_llm_runs_suppresses_answer(settings: Settings) -> None:
    service, item = qa(settings)
    service.retriever.revalidate.side_effect = [[item], []]
    with pytest.raises(AppError) as caught:
        await service.answer(41, uuid4(), SearchRequest(query="问题"), "test-token")
    assert caught.value.code == "RAG_SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_other_users_session_is_rejected_before_retrieval(settings: Settings) -> None:
    service, _ = qa(settings)
    service.sessions.detail.side_effect = AppError("SESSION_NOT_FOUND", "不存在", 404)
    with pytest.raises(AppError):
        await service.answer(99, uuid4(), SearchRequest(query="问题"), "test-token")
    service.retriever.search.assert_not_called()


def test_context_budget_and_external_guide_source() -> None:
    item = hit(source_type="link", source_url="https://example.test/original")
    context, citations = assemble_context([item], 500)
    assert len(context) <= 500 and citations[0].source_type == "link"
    assert citations[0].original_url == "https://example.test/original"
    assert assemble_context([item.model_copy(update={"content":"字"*1000})], 500) == ("[]", [])
