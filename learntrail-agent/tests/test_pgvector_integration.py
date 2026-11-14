"""Opt-in real pgvector tests; vectors are synthetic, not provider embeddings.

Requires LEARNTRAIL_TEST_DATABASE_URL and vector installed in public by an
administrator. Each test owns a fresh schema and never changes existing data.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from learntrail_agent.clients.embedding import EmbeddingBatch, EmbeddingClient
from learntrail_agent.clients.java import JavaArticleClient, PublishedArticle
from learntrail_agent.errors import AppError
from learntrail_agent.rag.chunker import ArticleChunk, split_document
from learntrail_agent.rag.cleaner import clean_document
from learntrail_agent.rag.indexer import ArticleIndexer
from learntrail_agent.security.auth import load_public_key
from learntrail_agent.settings import Settings
from learntrail_agent.storage.vector_repository import IndexClaim, VectorRepository
from learntrail_agent.storage.search_repository import SearchRepository
from learntrail_agent.rag.bm25 import TOKENIZER_VERSION, bm25_score, term_counts, tokenize
from learntrail_agent.rag.retriever import Retriever
from learntrail_agent.rag.schemas import SearchRequest
from learntrail_agent.clients.reranker import RerankerClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def vectors() -> AsyncIterator[VectorRepository]:
    dsn = os.getenv("LEARNTRAIL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("No LEARNTRAIL_TEST_DATABASE_URL; real pgvector integration was not run")
    schema = "test_vector_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, connect_timeout=3) as admin:
        installed = await (await admin.execute(
            "SELECT 1 FROM pg_extension e JOIN pg_namespace n ON e.extnamespace = n.oid "
            "WHERE e.extname = 'vector' AND n.nspname = 'public'",
        )).fetchone()
        if not installed:
            pytest.skip("vector is not installed in public; no extension is installed by this test")
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await admin.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            migration = Path(__file__).resolve().parents[1] / "migrations/V002__article_vector_index.sql"
            await admin.execute(migration.read_text(encoding="utf-8"), prepare=False)
            keywords = migration.with_name("V003__chunk_keywords.sql")
            await admin.execute(keywords.read_text(encoding="utf-8"), prepare=False)
            async with AsyncConnectionPool(
                dsn, min_size=1, max_size=3, open=False,
                kwargs={"autocommit": True, "row_factory": dict_row,
                        "options": f"-c search_path={schema},public -c statement_timeout=3000"},
            ) as pool:
                await pool.wait(timeout=5)
                yield VectorRepository(pool)
        finally:
            if admin.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                await admin.execute("ROLLBACK")
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def article(body: str = "第一版中文正文。" * 30) -> PublishedArticle:
    return PublishedArticle(id=42, slug="article-42", title="真实数据库测试", body=body,
                            status="published", contentFormat="text", sourceType="original")


async def save(
    repo: VectorRepository, source: PublishedArticle, fingerprint: str = "a" * 64,
) -> tuple[IndexClaim, list[ArticleChunk], EmbeddingBatch]:
    doc = clean_document(source.title, source.body, source.content_format)
    chunks = split_document(doc, source.id, source.slug, 64, 8)
    claim = await repo.reserve(41, source, doc.content_hash, fingerprint, 60)
    batch = EmbeddingBatch(vectors=[[1, 2, 3] for _ in chunks], model="synthetic-test", usage=None)
    await repo.replace(claim, source, chunks, batch, 3, "synthetic-test",
                       {"source_type": source.source_type, "test_only": True})
    return claim, chunks, batch


async def test_vector_round_trip_skip_update_and_user_isolation(vectors: VectorRepository) -> None:
    source = article()
    first, chunks, _ = await save(vectors, source)
    rows = await vectors.get_chunks(41, 42)
    assert [row.content for row in rows] == [chunk.text for chunk in chunks]
    assert all(row.embedding == [1, 2, 3] and row.dimensions == 3 for row in rows)
    assert await vectors.get_chunks(42, 42) == []
    assert await vectors.get_state(42, 42) is None
    skipped = await vectors.reserve(41, source, first.content_hash, first.fingerprint, 60)
    assert skipped.skipped and skipped.chunk_count == len(chunks)
    updated, new_chunks, _ = await save(vectors, article("新版正文。" * 12), "b" * 64)
    current = await vectors.get_chunks(41, 42)
    assert {row.generation for row in current} == {updated.token}
    assert updated.token != first.token and updated.content_hash != first.content_hash
    assert [row.content for row in current] == [chunk.text for chunk in new_chunks]
    async with vectors.pool.connection() as conn:
        result = await (await conn.execute(
            "SELECT vector_dims(embedding) AS dimensions, embedding <-> %s::vector AS distance "
            "FROM ai_article_chunks WHERE user_id = %s AND article_id = %s ORDER BY ordinal LIMIT 1",
            ("[1,2,4]", 41, 42),
        )).fetchone()
        assert result == {"dimensions": 3, "distance": 1.0}


async def test_insert_failure_rolls_back_deleted_chunks_and_parent_generation(vectors: VectorRepository) -> None:
    source = article()
    first, chunks, batch = await save(vectors, source)
    replacement = await vectors.reserve(41, source, first.content_hash, "c" * 64, 60)
    # Fail after DELETE, parent UPDATE and the first INSERT, exercising real SQL rollback.
    async with vectors.pool.connection() as conn:
        await conn.execute("""CREATE FUNCTION reject_second_chunk() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.ordinal = 1 THEN RAISE EXCEPTION 'controlled test failure'; END IF;
            RETURN NEW; END $$""")
        await conn.execute("""CREATE TRIGGER fail_test_insert BEFORE INSERT ON ai_article_chunks
            FOR EACH ROW EXECUTE FUNCTION reject_second_chunk()""")
    with pytest.raises(AppError) as caught:
        await vectors.replace(replacement, source, chunks, batch, 3, "synthetic-test", {})
    assert caught.value.code == "DATABASE_UNAVAILABLE"
    assert (await vectors.get_state(41, 42)).generation == first.token
    assert {row.generation for row in await vectors.get_chunks(41, 42)} == {first.token}
    assert len(await vectors.get_chunks(41, 42)) == len(chunks)
    await vectors.release(replacement)


async def test_live_lease_concurrency_expiry_and_stale_writer(vectors: VectorRepository) -> None:
    source = article()
    doc = clean_document(source.title, source.body, source.content_format)
    results = await asyncio.gather(
        vectors.reserve(41, source, doc.content_hash, "a" * 64, 60),
        vectors.reserve(41, source, doc.content_hash, "a" * 64, 60), return_exceptions=True,
    )
    assert sum(isinstance(r, AppError) and r.code == "ARTICLE_INDEX_BUSY" for r in results) == 1
    old = next(r for r in results if not isinstance(r, Exception))
    async with vectors.pool.connection() as conn:
        await conn.execute("UPDATE ai_article_indexes SET pending_expires_at = clock_timestamp() - interval '1 second'")
    newer = await vectors.reserve(41, source, doc.content_hash, "b" * 64, 60)
    chunks = split_document(doc, 42, source.slug, 64, 8)
    batch = EmbeddingBatch(vectors=[[1, 2, 3] for _ in chunks], model="synthetic-test", usage=None)
    with pytest.raises(AppError) as caught:
        await vectors.replace(old, source, chunks, batch, 3, "synthetic-test", {})
    assert caught.value.code == "ARTICLE_INDEX_SUPERSEDED"
    await vectors.release(old)  # Must not clear the newer writer's lease.
    await vectors.replace(newer, source, chunks, batch, 3, "synthetic-test", {})
    assert (await vectors.get_state(41, 42)).generation == newer.token


async def test_invalidation_hides_vectors_and_revokes_pending_writer(vectors: VectorRepository) -> None:
    source = article()
    first, chunks, batch = await save(vectors, source)
    pending = await vectors.reserve(41, source, first.content_hash, "d" * 64, 60)
    await vectors.invalidate(42, source.slug)
    assert (await vectors.get_state(41, 42)).active
    await vectors.invalidate(41, source.slug)
    assert await vectors.get_chunks(41, 42) == []
    assert not (await vectors.get_state(41, 42)).active
    with pytest.raises(AppError) as caught:
        await vectors.replace(pending, source, chunks, batch, 3, "synthetic-test", {})
    assert caught.value.code == "ARTICLE_INDEX_SUPERSEDED"
    async with vectors.pool.connection() as conn:
        row = await (await conn.execute("SELECT count(*) AS n FROM ai_article_chunks")).fetchone()
        assert row["n"] == len(chunks)  # Retained, but inaccessible through active reads.


async def test_database_rejects_dimension_mismatch_and_cross_owner_fk(vectors: VectorRepository) -> None:
    claim, _, _ = await save(vectors, article())
    async with vectors.pool.connection() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.transaction():
                await conn.execute("UPDATE ai_article_chunks SET embedding = '[1,2]'::vector")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with conn.transaction():
                await conn.execute("UPDATE ai_article_chunks SET user_id = 99")
        assert {row.generation for row in await vectors.get_chunks(41, 42)} == {claim.token}


async def test_real_cosine_threshold_scope_and_keyword_backfill(vectors: VectorRepository) -> None:
    await save(vectors, article("数据库事务与钱包行锁。"))
    state = await vectors.get_state(41, 42)
    repo = SearchRepository(vectors.pool)
    rows = await repo.vector(41, [state], [1, 2, 3], 3, .99, 5)
    assert len(rows) == 1 and rows[0].score == pytest.approx(1.0)
    assert await repo.vector(41, [state], [-1, -2, -3], 3, .5, 5) == []
    assert await repo.vector(99, [state], [1, 2, 3], 3, -1, 5) == []
    async with vectors.pool.connection() as conn:
        await conn.execute("UPDATE ai_article_chunks SET bm25_terms='{}',bm25_length=0,tokenizer_version=''")
    await repo.backfill_keywords(41, [state])
    ranked = await repo.bm25(41, [state], tokenize("钱包行锁"), 1.2, .75, 0, 5)
    assert len(ranked) == 1 and ranked[0].bm25_score > 0
    assert await repo.bm25(99, [state], tokenize("钱包行锁"), 1.2, .75, 0, 5) == []
    async with vectors.pool.connection() as conn:
        row = await (await conn.execute("SELECT tokenizer_version,bm25_length,bm25_terms FROM ai_article_chunks")).fetchone()
        assert row["tokenizer_version"] == TOKENIZER_VERSION
        assert row["bm25_length"] == sum(row["bm25_terms"].values())
        index = await (await conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND indexname='ai_article_chunks_keywords'")).fetchone()
        assert "gin" in index["indexdef"]


async def test_real_bm25_matches_reference_and_updates_atomically(vectors: VectorRepository) -> None:
    sources = [article("钱包行锁钱包行锁。"), article("队列消费失败，检查路由和交换机。").model_copy(
        update={"id":43, "slug":"article-43"})]
    for source in sources:
        await save(vectors, source)
    repo = SearchRepository(vectors.pool)
    states = [await vectors.get_state(41, s.id) for s in sources]
    result = await repo.bm25(41, states, tokenize("钱包行锁"), 1.2, .75, 0, 5)
    assert len(result) == 1 and result[0].article_id == 42
    terms = []
    for source in sources:
        chunk = split_document(clean_document(source.title, source.body, source.content_format),
                               source.id, source.slug, 64, 8)[0]
        terms.append(term_counts(chunk.title + "\n" + chunk.section + "\n" + chunk.text))
    query = tokenize("钱包行锁")
    frequencies = {t:sum(t in terms_ for terms_ in terms) for t in set(query)}
    expected = bm25_score(terms[0], query, frequencies, 2, sum(sum(t.values()) for t in terms)/2)
    assert result[0].score == pytest.approx(expected)
    await save(vectors, article("已更换为视频编码指南。"), "b"*64)
    states[0] = await vectors.get_state(41, 42)
    assert await repo.bm25(41, states, query, 1.2, .75, 0, 5) == []
    await vectors.invalidate(41, "article-43")
    assert await repo.bm25(41, states, tokenize("交换机"), 1.2, .75, 0, 5) == []


async def test_live_java_article_lifecycle_with_synthetic_embeddings(vectors: VectorRepository) -> None:
    """Real Java + PostgreSQL; ONLY the embedding generator is a test double.

    Explicit credentials authorize creating one new QA article and archiving it
    in finally. No existing article is changed; no reading reward API is called.
    """
    username = os.getenv("LEARNTRAIL_TEST_JAVA_USERNAME")
    password = os.getenv("LEARNTRAIL_TEST_JAVA_PASSWORD")
    if not username or not password:
        pytest.skip("No explicit Java QA credentials; live article lifecycle was not run")
    settings = Settings().model_copy(update={
        "embedding_base_url": "https://synthetic.invalid/v1", "embedding_model": "synthetic-test",
        "embedding_dimensions": 3, "rag_chunk_size": 64, "rag_chunk_overlap": 8,
    })
    key = load_public_key(settings)
    assert key is not None, "Configure the trusted Java public key before live tests"
    async with httpx.AsyncClient(
        base_url=settings.java_gateway_base_url, timeout=15, trust_env=False,
        headers={"Host": "api.learntrail.com", "Origin": "http://www.learntrail.com"},
    ) as http:
        login = await http.post("/as/accounts/login", json={
            "type": 1, "username": username, "password": password, "rememberMe": False,
        })
        assert login.status_code == 200 and login.json().get("code") == 200, "Java QA login failed"
        token = login.json()["data"]
        assert isinstance(token, str)
        headers = {"Authorization": token}
        edit = {"title": "RAG 联调验收 " + uuid4().hex[:8], "summary": "自动化专用验收，结束后归档。",
                "body": "## 第一版\n\n" + "这是新增验收文章的初始正文。" * 20,
                "contentFormat": "markdown", "sourceType": "original"}
        created = await http.post("/ls/studio-articles", headers=headers, json=edit)
        assert created.status_code == 200 and created.json().get("code") == 200, "QA article create failed"
        article_id = int(created.json()["data"])
        slug = "article-" + str(article_id)

        async def state(value: str) -> None:
            response = await http.put(f"/ls/studio-articles/{article_id}/state/{value}", headers=headers)
            assert response.status_code == 200 and response.json().get("code") == 200, "QA state change failed"

        embedding = Mock(spec=EmbeddingClient)

        async def synthetic(texts: list[str]) -> EmbeddingBatch:
            return EmbeddingBatch(vectors=[[1, 2, 3] for _ in texts], model="synthetic-test", usage=None)

        embedding.embed = AsyncMock(side_effect=synthetic)
        java = JavaArticleClient(http, settings, key)
        indexer = ArticleIndexer(java, embedding, vectors, settings)
        try:
            assert (await indexer.index_article(slug, token)).status == "invalidated"  # draft
            embedding.embed.assert_not_called()
            await state("published")
            source = await java.get_published_article(slug, token)
            first = await indexer.index_article(slug, token)
            assert first.status == "indexed"
            assert (await indexer.index_article(slug, token)).status == "skipped"
            assert embedding.embed.await_count == 1
            old_generation = (await vectors.get_state(source.user_id, article_id)).generation
            changed = {**edit, "body": "## 第二版\n\n" + "完全更新的正文。" * 15}
            response = await http.put(f"/ls/studio-articles/{article_id}", headers=headers, json=changed)
            assert response.status_code == 200 and response.json().get("code") == 200, "QA article edit failed"
            updated = await indexer.index_article(slug, token)
            assert updated.status == "indexed" and updated.content_hash != first.content_hash
            rows = await vectors.get_chunks(source.user_id, article_id)
            assert rows and old_generation not in {row.generation for row in rows}
            assert await vectors.get_chunks(source.user_id + 1, article_id) == []
            retriever = Retriever(SearchRepository(vectors.pool), vectors, java, indexer,
                                  embedding, Mock(spec=RerankerClient), settings)
            request = SearchRequest(query="完全更新", mode="bm25", article_slugs=[slug])
            assert (await retriever.search(source.user_id, request, token)).results
            await state("archived")
            # Intentionally do not notify the indexer: a stale active index must
            # still be rejected by the live Java authorization pass on retrieval.
            assert (await vectors.get_state(source.user_id, article_id)).active
            rejected = await retriever.search(source.user_id, request, token)
            assert rejected.results == rejected.bm25 == []
            assert (await indexer.index_article(slug, token)).status == "invalidated"
            assert await vectors.get_chunks(source.user_id, article_id) == []
            assert not (await vectors.get_state(source.user_id, article_id)).active
        finally:
            await state("archived")
