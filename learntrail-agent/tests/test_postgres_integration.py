"""Opt-in real PostgreSQL tests. Uses an isolated schema in a dedicated test DB."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from learntrail_agent.errors import AppError
from learntrail_agent.schemas import ChatResponse, Usage
from learntrail_agent.storage.repositories import SessionRepository


@pytest_asyncio.fixture
async def postgres_repository() -> AsyncIterator[SessionRepository]:
    dsn = os.getenv("LEARNTRAIL_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("No LEARNTRAIL_TEST_DATABASE_URL; real PostgreSQL integration was not run")
    schema = "test_ai_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, connect_timeout=3) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            migration = Path(__file__).resolve().parents[1] / "migrations/V001__sessions_messages_runs.sql"
            await admin.execute(migration.read_text(encoding="utf-8"), prepare=False)
            await admin.execute(migration.with_name("V004__agent_runs.sql").read_text(encoding="utf-8"), prepare=False)
            pool = AsyncConnectionPool(
                dsn, min_size=1, max_size=3, open=False,
                kwargs={"autocommit": True, "row_factory": dict_row,
                        "options": f"-c search_path={schema} -c statement_timeout=3000"},
            )
            try:
                await pool.open(wait=True, timeout=5)
                yield SessionRepository(pool)
            finally:
                await pool.close()
        finally:
            # Remove only this fixture's freshly created schema, never existing data.
            if admin.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                await admin.execute("ROLLBACK")
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def reply(content: str = "真实数据库中的测试回复") -> ChatResponse:
    return ChatResponse(id="mock-provider-id", model="test-model", content=content, finish_reason="stop")


@pytest.mark.asyncio
async def test_postgres_round_trip_and_new_repository_reads_history(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "数据库测试")
    run = await repo.start_run(41, session.id, "第一问", "test-model", 90)
    await repo.complete_run(41, session.id, run.id, reply(), 20)
    # A new repository reads persisted rows, not in-process conversation memory.
    reloaded = SessionRepository(repo.pool)
    detail = await reloaded.detail(41, session.id, 100, 0)
    assert [m.role for m in detail.messages] == ["user", "assistant"]
    assert all(m.status == "completed" for m in detail.messages)
    assert detail.recent_runs[0].status == "succeeded"
    assert await reloaded.history(41, session.id, 10) == [("第一问", reply().content)]
    assert (await reloaded.list(41, 20, 0)).total == 1


@pytest.mark.asyncio
async def test_postgres_owner_isolation(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "仅本人可见")
    assert (await repo.list(42, 20, 0)).total == 0
    for operation in (
        repo.detail(42, session.id, 100, 0), repo.start_run(42, session.id, "越权", "test", 90),
    ):
        with pytest.raises(AppError) as caught:
            await operation
        assert caught.value.code == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_postgres_failed_turn_is_excluded(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "失败测试")
    run = await repo.start_run(41, session.id, "失败问题", "test-model", 90)
    await repo.fail_run(41, session.id, run.id, "LLM_TIMEOUT")
    detail = await repo.detail(41, session.id, 100, 0)
    assert len(detail.messages) == 1 and detail.messages[0].status == "failed"
    assert detail.recent_runs[0].status == "failed"
    assert await repo.history(41, session.id, 10) == []


@pytest.mark.asyncio
async def test_postgres_constraint_failure_rolls_back_entire_success(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "回滚测试")
    run = await repo.start_run(41, session.id, "问题", "test-model", 90)
    # Empty content violates the SQL constraint after the run status UPDATE.
    with pytest.raises(AppError) as caught:
        await repo.complete_run(41, session.id, run.id, reply(""), 20)
    assert caught.value.code == "DATABASE_UNAVAILABLE"
    detail = await repo.detail(41, session.id, 100, 0)
    assert detail.recent_runs[0].status == "running"
    assert len(detail.messages) == 1 and detail.messages[0].status == "pending"


@pytest.mark.asyncio
async def test_postgres_concurrent_starts_allow_one_active_run(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "并发测试")
    results = await asyncio.gather(
        repo.start_run(41, session.id, "问题一", "test", 90),
        repo.start_run(41, session.id, "问题二", "test", 90), return_exceptions=True,
    )
    assert sum(isinstance(result, AppError) and result.code == "SESSION_BUSY" for result in results) == 1
    assert len((await repo.detail(41, session.id, 100, 0)).recent_runs) == 1


@pytest.mark.asyncio
async def test_postgres_expired_run_is_failed_before_next_turn(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "恢复测试")
    old = await repo.start_run(41, session.id, "中断的问题", "test", 0.001)
    await asyncio.sleep(0.02)
    await repo.start_run(41, session.id, "新问题", "test", 90)
    detail = await repo.detail(41, session.id, 100, 0)
    previous = next(run for run in detail.recent_runs if run.id == old.id)
    assert previous.status == "failed" and previous.error_code == "RUN_EXPIRED"
    assert detail.messages[0].status == "failed"


@pytest.mark.asyncio
async def test_postgres_cancel_is_owned_idempotent_and_records_usage(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    session = await repo.create(41, "停止测试")
    run = await repo.start_run(41, session.id, "问题", "test", 90)
    with pytest.raises(AppError) as denied:
        await repo.cancel_run(42, session.id, run.id)
    assert denied.value.code == "SESSION_NOT_FOUND"
    assert (await repo.cancel_run(41, session.id, run.id)).status == "cancelled"
    assert (await repo.cancel_run(41, session.id, run.id)).status == "cancelled"
    with pytest.raises(AppError) as stopped:
        await repo.complete_run(41, session.id, run.id, reply(), 20)
    assert stopped.value.code == "RUN_NOT_ACTIVE"
    await repo.fail_run(41, session.id, run.id, "RUN_CANCELLED", status="cancelled",
                        duration_ms=25, usage=Usage(total_tokens=9))
    saved = await repo.get_run(41, session.id, run.id)
    assert saved.duration_ms == 25 and saved.usage.total_tokens == 9
    assert await repo.history(41, session.id, 10) == []
    assert (await repo.detail(41, session.id, 100, 0)).messages[0].status == "failed"


@pytest.mark.asyncio
async def test_postgres_foreign_keys_and_partial_unique_index(postgres_repository: SessionRepository) -> None:
    repo = postgres_repository
    first = await repo.create(41, "约束一")
    second = await repo.create(41, "约束二")
    run = await repo.start_run(41, first.id, "问题", "test", 90)
    async with repo.pool.connection() as conn:
        indexes = await (await conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema()",
        )).fetchall()
        definitions = {row["indexname"]: row["indexdef"] for row in indexes}
        assert "UNIQUE INDEX" in definitions["ai_runs_one_active"]
        assert "WHERE" in definitions["ai_runs_one_active"] and "running" in definitions["ai_runs_one_active"]
        assert "ai_messages_session_created" in definitions and "ai_sessions_owner_updated" in definitions
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO ai_messages (id, session_id, run_id, role, content, status)
                       VALUES (%s, %s, %s, 'assistant', 'invalid cross-session reference', 'completed')""",
                    (uuid4(), second.id, run.id),
                )
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO ai_messages (id, session_id, run_id, role, content, status)
                       VALUES (%s, %s, %s, 'user', 'duplicate user message', 'pending')""",
                    (uuid4(), first.id, run.id),
                )
