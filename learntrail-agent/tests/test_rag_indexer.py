import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from learntrail_agent.clients.embedding import EmbeddingBatch, EmbeddingClient, EmbeddingUsage
from learntrail_agent.clients.java import ArticleUnavailable, AuthorizedArticle, JavaArticleClient, PublishedArticle
from learntrail_agent.errors import AppError
from learntrail_agent.rag.indexer import ArticleIndexer
from learntrail_agent.storage.vector_repository import IndexClaim, VectorRepository
from test_rag_embedding import config
from test_rag_java import payload

pytestmark = pytest.mark.asyncio


def setup_indexer() -> tuple:
    settings = config(rag_chunk_size=64, rag_chunk_overlap=8)
    article = PublishedArticle.model_validate(payload(body="## 指南\n\n" + "第一版正文。" * 40))
    java = AsyncMock(spec=JavaArticleClient)
    java.get_published_article.return_value = AuthorizedArticle(41, article)
    embedding = MagicMock(spec=EmbeddingClient)
    cache = {}
    async def embed(texts: list[str]) -> EmbeddingBatch:
        return EmbeddingBatch(vectors=[[1.0] * settings.embedding_dimensions for _ in texts],
                              model="resolved-model", usage=EmbeddingUsage(total_tokens=len(texts)))
    embedding.embed = AsyncMock(side_effect=embed)
    repository = AsyncMock(spec=VectorRepository)
    def reserve(user_id, article, content_hash, fingerprint, lease):
        return IndexClaim(user_id, article.id, article.slug, content_hash, fingerprint,
                          None if cache.get("fingerprint") == fingerprint and cache.get("active") else uuid4(),
                          len(cache.get("chunks", [])))
    async def replace(claim, article, chunks, batch, dimensions, model, metadata):
        cache.update(fingerprint=claim.fingerprint, chunks=chunks, active=True)
    async def invalidate(user_id, slug):
        cache["active"] = False
    repository.reserve.side_effect = reserve
    repository.replace.side_effect = replace
    repository.invalidate.side_effect = invalidate
    return ArticleIndexer(java, embedding, repository, settings), java, embedding, repository, cache, article


async def test_repeated_article_skips_embedding_but_rechecks_java() -> None:
    indexer, java, embedding, repository, cache, article = setup_indexer()
    first = await indexer.index_article(article.slug, "test-token")
    second = await indexer.index_article(article.slug, "test-token")
    assert first.status == "indexed" and second.status == "skipped"
    assert second.content_hash == first.content_hash and second.chunk_count == first.chunk_count
    assert second.usage is None
    assert java.get_published_article.await_count == 3 and embedding.embed.await_count == 1
    repository.release.assert_not_called()


async def test_updated_article_replaces_complete_chunk_generation() -> None:
    indexer, java, embedding, repository, cache, article = setup_indexer()
    first = await indexer.index_article(article.slug, "test-token")
    changed = article.model_copy(update={"body": "## 新版\n\n全新的内容。" * 10})
    java.get_published_article.return_value = AuthorizedArticle(41, changed)
    second = await indexer.index_article(article.slug, "test-token")
    assert first.content_hash != second.content_hash and repository.replace.await_count == 2
    assert all("第一版正文" not in chunk.text for chunk in cache["chunks"])
    assert all(chunk.article_id == article.id for chunk in cache["chunks"])


@pytest.mark.parametrize("setting,value", [("embedding_model", "new-model"), ("embedding_model_revision", "v2"),
                                          ("embedding_dimensions", 4), ("rag_chunk_size", 96)])
async def test_model_or_pipeline_change_rebuilds_same_content(setting: str, value: object) -> None:
    indexer, _, embedding, _, _, article = setup_indexer()
    first = await indexer.index_article(article.slug, "test-token")
    setattr(indexer.settings, setting, value)
    second = await indexer.index_article(article.slug, "test-token")
    assert first.content_hash == second.content_hash and second.status == "indexed"
    assert embedding.embed.await_count == 2


async def test_withdrawn_article_invalidates_only_current_users_old_index() -> None:
    indexer, java, embedding, repository, cache, article = setup_indexer()
    await indexer.index_article(article.slug, "test-token")
    java.get_published_article.side_effect = ArticleUnavailable(41, article.slug)
    result = await indexer.index_article(article.slug, "test-token")
    assert result.status == "invalidated" and cache["active"] is False
    repository.invalidate.assert_awaited_once_with(41, article.slug)
    assert embedding.embed.await_count == 1


@pytest.mark.parametrize("status", ["draft", "archived", "blocked"])
async def test_indexer_also_rejects_nonpublished_client_objects(status: str) -> None:
    indexer, java, embedding, repository, _, article = setup_indexer()
    java.get_published_article.return_value = AuthorizedArticle(41, article.model_copy(update={"status": status}))
    result = await indexer.index_article(article.slug, "test-token")
    assert result.status == "invalidated"
    repository.invalidate.assert_awaited_once_with(41, article.slug)
    repository.reserve.assert_not_called()
    embedding.embed.assert_not_called()


async def test_source_metadata_is_normalized_before_fingerprint_and_storage() -> None:
    indexer, java, embedding, repository, _, article = setup_indexer()
    article = article.model_copy(update={"source_type": "reprint", "source_author": " cafe\u0301\x00 ",
                                         "source_url": "https://example.test/source", "license_note": "开放许可"})
    java.get_published_article.return_value = AuthorizedArticle(41, article)
    await indexer.index_article(article.slug, "test-token")
    assert repository.replace.await_args.args[-1]["source_author"] == "café"
    java.get_published_article.return_value = AuthorizedArticle(41, article.model_copy(update={"source_author": "café"}))
    assert (await indexer.index_article(article.slug, "test-token")).status == "skipped"
    assert embedding.embed.await_count == 1


async def test_embedding_failure_preserves_existing_generation_and_releases_lease() -> None:
    indexer, java, embedding, repository, cache, article = setup_indexer()
    await indexer.index_article(article.slug, "test-token")
    old = cache.copy()
    java.get_published_article.return_value = AuthorizedArticle(41, article.model_copy(update={"body": "更新正文"}))
    embedding.embed.side_effect = AppError("EMBEDDING_RATE_LIMITED", "限流", 429)
    with pytest.raises(AppError) as caught:
        await indexer.index_article(article.slug, "test-token")
    assert caught.value.code == "EMBEDDING_RATE_LIMITED" and cache == old
    repository.release.assert_awaited_once()
    assert repository.replace.await_count == 1


async def test_article_changed_during_embedding_is_not_committed() -> None:
    indexer, java, _, repository, cache, article = setup_indexer()
    java.get_published_article.side_effect = [AuthorizedArticle(41, article),
        AuthorizedArticle(41, article.model_copy(update={"body": "生成向量期间发生修改"}))]
    with pytest.raises(AppError) as caught:
        await indexer.index_article(article.slug, "test-token")
    assert caught.value.code == "ARTICLE_CHANGED_DURING_INDEX" and cache["active"] is False
    repository.replace.assert_not_called()


async def test_cancel_and_timeout_release_unpublished_lease() -> None:
    for cancelled in (True, False):
        indexer, _, embedding, repository, _, article = setup_indexer()
        entered = asyncio.Event()
        async def blocked(texts):
            entered.set()
            await asyncio.Event().wait()
        embedding.embed.side_effect = blocked
        if not cancelled:
            indexer.settings.rag_index_timeout_seconds = 0.01
        task = asyncio.create_task(indexer.index_article(article.slug, "test-token"))
        await entered.wait()
        if cancelled:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AppError) as caught:
                await task
            assert caught.value.code == "ARTICLE_INDEX_TIMEOUT"
        repository.release.assert_awaited_once()
        repository.replace.assert_not_called()


async def test_http_pipeline_uses_actual_clients_cleaner_and_chunker(
    respx_mock: respx.MockRouter, java_key: RSAPrivateKey, token_factory,
) -> None:
    settings = config(java_gateway_base_url="https://java.example.test")
    respx_mock.get("https://java.example.test/us/users/41/enabled").respond(200, json={"code": 200, "data": True})
    respx_mock.get("https://java.example.test/ls/studio-articles/published/article-42").respond(
        200, json={"code": 200, "data": payload()},
    )
    def embedding_response(request):
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"model": "resolved-model", "data": [
            {"index": i, "embedding": [1, 2, 3]} for i in range(len(texts))]})
    respx_mock.post("https://embedding.example.test/v1/embeddings").mock(side_effect=embedding_response)
    repository = AsyncMock(spec=VectorRepository)
    repository.reserve.side_effect = lambda uid, article, digest, fp, lease: IndexClaim(uid, article.id, article.slug, digest, fp, uuid4())
    async with httpx.AsyncClient(follow_redirects=False) as http:
        indexer = ArticleIndexer(JavaArticleClient(http, settings, java_key.public_key()),
                                 EmbeddingClient(http, settings), repository, settings)
        result = await indexer.index_article("article-42", token_factory())
    assert result.status == "indexed" and result.chunk_count > 0
    repository.replace.assert_awaited_once()
    assert [call.request.url.path for call in respx_mock.calls] == [
        "/us/users/41/enabled", "/ls/studio-articles/published/article-42", "/v1/embeddings",
        "/us/users/41/enabled", "/ls/studio-articles/published/article-42",
    ]
