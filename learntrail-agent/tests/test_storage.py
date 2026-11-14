"""Driver-boundary mocks verify SQL/transaction intent, not PostgreSQL semantics."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from learntrail_agent.errors import AppError
from learntrail_agent.schemas import ChatResponse, Usage
from learntrail_agent.settings import Settings
from learntrail_agent.storage.pool import open_pool, transaction
from learntrail_agent.storage.repositories import SessionRepository


def scripted_pool(results: list[object]) -> tuple[MagicMock, MagicMock, AsyncMock]:
    conn = MagicMock()
    tx = AsyncMock()
    conn.transaction.return_value = tx
    async def execute(query: str, params: object = None) -> MagicMock:
        assert results, "Unexpected SQL statement"
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        cursor = MagicMock()
        cursor.fetchone = AsyncMock(return_value=result)
        cursor.fetchall = AsyncMock(return_value=result)
        return cursor
    conn.execute = AsyncMock(side_effect=execute)
    pool = MagicMock()
    pool.connection.return_value = AsyncMock()
    pool.connection.return_value.__aenter__.return_value = conn
    return pool, conn, tx


def session_row() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {"id": uuid4(), "user_id": 41, "title": "测试", "created_at": now, "updated_at": now}


@pytest.mark.asyncio
async def test_create_and_list_use_authenticated_owner() -> None:
    row = session_row()
    pool, conn, tx = scripted_pool([row, {"total": 1}, [row]])
    repo = SessionRepository(pool)
    assert (await repo.create(41, "测试")).id == row["id"]
    result = await repo.list(41, 20, 0)
    assert result.total == 1 and result.items[0].id == row["id"]
    assert conn.execute.await_args_list[0].args[1][1] == 41
    assert conn.execute.await_args_list[1].args[1] == (41,)
    assert conn.execute.await_args_list[2].args[1] == (41, 20, 0)
    assert "WHERE user_id = %s" in conn.execute.await_args_list[2].args[0]
    assert tx.__aexit__.await_args.args == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["detail", "history", "start", "complete", "fail", "get", "cancel"])
async def test_every_existing_session_operation_checks_owner_first(operation: str) -> None:
    pool, conn, tx = scripted_pool([None])
    repo, session_id, run_id = SessionRepository(pool), uuid4(), uuid4()
    reply = ChatResponse(id="test", model="test", content="答", finish_reason="stop")
    calls = {
        "detail": lambda: repo.detail(99, session_id, 100, 0),
        "history": lambda: repo.history(99, session_id, 10),
        "start": lambda: repo.start_run(99, session_id, "问", "test", 60),
        "complete": lambda: repo.complete_run(99, session_id, run_id, reply, 10),
        "fail": lambda: repo.fail_run(99, session_id, run_id, "TEST_FAILURE"),
        "get": lambda: repo.get_run(99, session_id, run_id),
        "cancel": lambda: repo.cancel_run(99, session_id, run_id),
    }
    with pytest.raises(AppError) as caught:
        await calls[operation]()
    assert caught.value.code == "SESSION_NOT_FOUND"
    conn.execute.assert_awaited_once()
    assert conn.execute.await_args.args[1] == (session_id, 99)
    assert "AND user_id = %s" in conn.execute.await_args.args[0]
    assert tx.__aexit__.await_args.args[0] is AppError


@pytest.mark.asyncio
async def test_start_run_locks_session_and_inserts_pending_user_atomically() -> None:
    row = session_row()
    pool, conn, tx = scripted_pool([row, None, None, None, None, None])
    result = await SessionRepository(pool).start_run(41, row["id"], "问题", "test", 90)
    queries = [call.args[0] for call in conn.execute.await_args_list]
    assert "FOR UPDATE" in queries[0]
    assert "RUN_EXPIRED" in queries[1] and "status = 'failed'" in queries[1]
    assert "'running'" in queries[3] and "'pending'" in queries[4]
    assert conn.execute.await_args_list[4].args[1][0] == result.user_message_id
    assert tx.__aexit__.await_args.args == (None, None, None)


@pytest.mark.asyncio
async def test_busy_session_does_not_insert_another_run() -> None:
    row = session_row()
    pool, conn, tx = scripted_pool([row, None, {"id": uuid4()}])
    with pytest.raises(AppError) as caught:
        await SessionRepository(pool).start_run(41, row["id"], "问题", "test", 90)
    assert caught.value.code == "SESSION_BUSY"
    assert not any("INSERT" in call.args[0] for call in conn.execute.await_args_list)
    assert tx.__aexit__.await_args.args[0] is AppError


@pytest.mark.asyncio
async def test_history_filters_complete_successful_pairs_and_reverses_order() -> None:
    row = session_row()
    pool, conn, _ = scripted_pool([row, [{"question": "第二问", "answer": "第二答"}, {"question": "第一问", "answer": "第一答"}]])
    assert await SessionRepository(pool).history(41, row["id"], 2) == [("第一问", "第一答"), ("第二问", "第二答")]
    query, params = conn.execute.await_args.args
    assert "r.status = 'succeeded'" in query and "r.finish_reason = 'stop'" in query
    assert "u.status = 'completed'" in query and "a.status = 'completed'" in query
    assert params == (row["id"], 2)


@pytest.mark.asyncio
async def test_success_writes_run_and_both_messages_in_one_transaction() -> None:
    row = session_row()
    pool, conn, tx = scripted_pool([row, {"id": uuid4()}, None, None, None])
    reply = ChatResponse(id="provider-test", model="test-model", content="回答", finish_reason="stop")
    assistant_id = await SessionRepository(pool).complete_run(41, row["id"], uuid4(), reply, 25)
    assert conn.execute.await_args_list[2].args[1][0] == assistant_id
    assert "status = 'succeeded'" in conn.execute.await_args_list[1].args[0]
    assert "status = 'completed'" in conn.execute.await_args_list[3].args[0]
    assert tx.__aenter__.await_count == 1
    assert tx.__aexit__.await_args.args == (None, None, None)


@pytest.mark.asyncio
async def test_insert_failure_exits_transaction_with_error_and_does_not_leak_details() -> None:
    row = session_row()
    pool, conn, tx = scripted_pool([row, {"id": uuid4()}, psycopg.IntegrityError("test-only-sensitive-dsn")])
    reply = ChatResponse(id="test", model="test", content="回答", finish_reason="stop")
    with pytest.raises(AppError) as caught:
        await SessionRepository(pool).complete_run(41, row["id"], uuid4(), reply, 25)
    assert caught.value.code == "DATABASE_UNAVAILABLE"
    assert "test-only-sensitive-dsn" not in str(caught.value)
    assert tx.__aexit__.await_args.args[0] is psycopg.IntegrityError
    assert conn.execute.await_count == 3


@pytest.mark.asyncio
async def test_failure_only_marks_pending_user_and_active_run() -> None:
    row, run_id = session_row(), uuid4()
    pool, conn, _ = scripted_pool([row, {"id": run_id}, None])
    await SessionRepository(pool).fail_run(41, row["id"], run_id, "RUN_CANCELLED", status="cancelled")
    assert conn.execute.await_args_list[1].args[1] == ("cancelled", "RUN_CANCELLED", run_id, row["id"])
    assert "status = 'running'" in conn.execute.await_args_list[1].args[0]
    assert "status = 'pending'" in conn.execute.await_args_list[2].args[0]
    assert not any("INSERT" in call.args[0] for call in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_pool_configuration_lifecycle_and_failure() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://test:test@127.0.0.1/test_ai")
    pool = MagicMock(open=AsyncMock(), close=AsyncMock())
    with patch("learntrail_agent.storage.pool.AsyncConnectionPool", return_value=pool) as factory:
        async with open_pool(settings) as opened:
            assert opened is pool
        assert factory.call_args.kwargs["open"] is False
        pool.open.assert_awaited_once()
        pool.close.assert_awaited_once()
    pool.open.side_effect = PoolTimeout("test-only-sensitive-dsn")
    pool.close.reset_mock()
    with patch("learntrail_agent.storage.pool.AsyncConnectionPool", return_value=pool):
        with pytest.raises(RuntimeError) as caught:
            async with open_pool(settings):
                pytest.fail("Unreachable")
        assert "test-only-sensitive-dsn" not in str(caught.value)
        pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_unconfigured_pool_and_queue_timeout() -> None:
    with pytest.raises(AppError, match="尚未配置"):
        async with transaction(None):
            pytest.fail("Unreachable")
    pool = MagicMock()
    pool.connection.return_value = AsyncMock()
    pool.connection.return_value.__aenter__.side_effect = PoolTimeout("test-only-sensitive")
    with pytest.raises(AppError) as caught:
        async with transaction(pool):
            pytest.fail("Unreachable")
    assert caught.value.code == "DATABASE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "cancelled", "succeeded"])
async def test_cancel_does_not_rewrite_terminal_runs(status: str) -> None:
    row, run_id = session_row(), uuid4()
    now = datetime.now(timezone.utc)
    run = {"id": run_id, "status": status, "model": "test", "finish_reason": None, "usage": None,
           "duration_ms": None, "error_code": None, "created_at": now, "expires_at": now+timedelta(seconds=60),
           "finished_at": None if status == "running" else now}
    cancelled = {**run, "status": "cancelled", "error_code": "RUN_CANCELLED", "finished_at": now}
    pool, conn, tx = scripted_pool([row, run, cancelled, None] if status == "running" else [row, run])
    saved = await SessionRepository(pool).cancel_run(41, row["id"], run_id)
    assert saved.status == ("cancelled" if status == "running" else status)
    assert "FOR UPDATE" in conn.execute.await_args_list[0].args[0]
    assert conn.execute.await_count == (4 if status == "running" else 2)
    assert tx.__aexit__.await_args.args == (None, None, None)


@pytest.mark.asyncio
async def test_failure_statistics_only_update_failed_or_cancelled_runs() -> None:
    row, run_id = session_row(), uuid4()
    pool, conn, _ = scripted_pool([row, None, None])
    await SessionRepository(pool).fail_run(41, row["id"], run_id, "RUN_CANCELLED", status="cancelled",
                                         duration_ms=25, usage=Usage(total_tokens=9))
    query, params = conn.execute.await_args.args
    assert "status IN ('failed', 'cancelled')" in query
    assert params[0] == 25 and params[1].obj["total_tokens"] == 9
