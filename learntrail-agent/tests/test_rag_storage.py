"""Driver-boundary tests; pgvector SQL must still be verified on a real database."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import psycopg
import pytest

from learntrail_agent.clients.embedding import EmbeddingBatch
from learntrail_agent.clients.java import PublishedArticle
from learntrail_agent.errors import AppError
from learntrail_agent.rag.chunker import split_document
from learntrail_agent.rag.cleaner import clean_document
from learntrail_agent.storage.vector_repository import IndexClaim, VectorRepository
from test_storage import scripted_pool

pytestmark = pytest.mark.asyncio


def batch_data() -> tuple:
    article = PublishedArticle(id=42, slug="article-42", title="标题", body="正文。" * 70,
                               status="published", contentFormat="text", sourceType="original")
    document = clean_document(article.title, article.body, article.content_format)
    chunks = split_document(document, article.id, article.slug, 64, 8)
    batch = EmbeddingBatch(vectors=[[1, 2, 3] for _ in chunks], model="resolved-model", usage=None)
    claim = IndexClaim(41, 42, article.slug, document.content_hash, "b" * 64, uuid4())
    return article, chunks, batch, claim


@pytest.mark.parametrize("active,same", [(False, False), (True, False), (True, True)])
async def test_reserve_skips_only_matching_active_fingerprint(active: bool, same: bool) -> None:
    article, _, _, claim = batch_data()
    row = {"pending_live": False, "active": active, "fingerprint": claim.fingerprint if same else "old", "chunk_count": 4}
    pool, conn, _ = scripted_pool([None, row, None])
    saved = await VectorRepository(pool).reserve(41, article, claim.content_hash, claim.fingerprint, 240)
    assert saved.skipped == (active and same)
    assert conn.execute.await_args_list[1].args[1] == (41, 42)
    assert "FOR UPDATE" in conn.execute.await_args_list[1].args[0]
    assert not any("DELETE" in call.args[0] for call in conn.execute.await_args_list)


async def test_live_lease_blocks_duplicate_embedding_work() -> None:
    article, _, _, claim = batch_data()
    pool, conn, tx = scripted_pool([None, {"pending_live": True}])
    with pytest.raises(AppError) as caught:
        await VectorRepository(pool).reserve(41, article, claim.content_hash, claim.fingerprint, 240)
    assert caught.value.code == "ARTICLE_INDEX_BUSY"
    assert conn.execute.await_count == 2 and tx.__aexit__.await_args.args[0] is AppError


async def test_replacement_uses_one_transaction_and_bound_vector_parameters() -> None:
    article, chunks, batch, claim = batch_data()
    pool, conn, tx = scripted_pool([{"pending_token": claim.token, "pending_fingerprint": claim.fingerprint, "pending_live": True}, None, None])
    cursor = MagicMock(executemany=AsyncMock())
    conn.cursor.return_value = AsyncMock()
    conn.cursor.return_value.__aenter__.return_value = cursor
    await VectorRepository(pool).replace(claim, article, chunks, batch, 3, "requested-model", {"source_type": "original"})
    assert tx.__aenter__.await_count == 1 and tx.__aexit__.await_args.args == (None, None, None)
    assert conn.execute.await_args_list[1].args[0].startswith("DELETE FROM ai_article_chunks")
    assert "generation = %s" in conn.execute.await_args_list[2].args[0]
    sql, rows = cursor.executemany.await_args.args
    assert "%s::vector" in sql and len(rows) == len(chunks)
    assert all(row[0:3] == (41, 42, claim.token) and row[-2] == 3 for row in rows)
    assert all(chunk.text not in sql for chunk in chunks)


async def test_vector_insert_failure_rolls_back_whole_replacement() -> None:
    article, chunks, batch, claim = batch_data()
    pool, conn, tx = scripted_pool([{"pending_token": claim.token, "pending_fingerprint": claim.fingerprint, "pending_live": True}, None, None])
    cursor = MagicMock(executemany=AsyncMock(side_effect=psycopg.IntegrityError("PRIVATE-DB-DETAILS")))
    conn.cursor.return_value = AsyncMock()
    conn.cursor.return_value.__aenter__.return_value = cursor
    with pytest.raises(AppError) as caught:
        await VectorRepository(pool).replace(claim, article, chunks, batch, 3, "model", {})
    assert caught.value.code == "DATABASE_UNAVAILABLE" and "PRIVATE" not in str(caught.value)
    assert tx.__aexit__.await_args.args[0] is psycopg.IntegrityError


@pytest.mark.parametrize("state", [None, {"pending_token": uuid4(), "pending_fingerprint": "b"*64, "pending_live": True},
                                   {"pending_token": None, "pending_fingerprint": None, "pending_live": False}])
async def test_revoked_or_superseded_lease_cannot_publish_vectors(state: dict | None) -> None:
    article, chunks, batch, claim = batch_data()
    pool, conn, _ = scripted_pool([state])
    with pytest.raises(AppError) as caught:
        await VectorRepository(pool).replace(claim, article, chunks, batch, 3, "model", {})
    assert caught.value.code == "ARTICLE_INDEX_SUPERSEDED"
    assert conn.execute.await_count == 1


async def test_wrong_dimensions_are_rejected_before_any_storage_write() -> None:
    article, chunks, batch, claim = batch_data()
    pool, conn, _ = scripted_pool([])
    with pytest.raises(AppError) as caught:
        await VectorRepository(pool).replace(claim, article, chunks, batch, 4, "model", {})
    assert caught.value.code == "EMBEDDING_INVALID_RESPONSE"
    conn.execute.assert_not_called()


async def test_invalidation_revokes_lease_without_deleting_previous_chunks() -> None:
    pool, conn, _ = scripted_pool([None])
    await VectorRepository(pool).invalidate(41, "article-42")
    query, params = conn.execute.await_args.args
    assert params == (41, "article-42") and "active = FALSE" in query and "pending_token = NULL" in query
    assert "DELETE" not in query


async def test_reads_and_cleanup_are_scoped_to_owner_and_generation() -> None:
    _, _, _, claim = batch_data()
    pool, conn, _ = scripted_pool([None, [], None])
    repository = VectorRepository(pool)
    assert await repository.get_state(99, 42) is None
    assert await repository.get_chunks(99, 42) == []
    await repository.release(claim)
    assert conn.execute.await_args_list[0].args[1] == (99, 42)
    query = conn.execute.await_args_list[1].args[0]
    assert "i.active" in query and "i.generation = c.generation" in query and "c.user_id = %s" in query
    assert conn.execute.await_args_list[2].args[1] == (41, 42, claim.token)
