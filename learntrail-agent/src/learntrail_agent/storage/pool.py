"""Application-owned PostgreSQL pool and short, rollback-safe transactions."""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import anyio
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout, TooManyRequests

from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings

DatabasePool = AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]


@asynccontextmanager
async def open_pool(settings: Settings) -> AsyncIterator[DatabasePool | None]:
    if not settings.database_url.get_secret_value():
        yield None
        return
    pool: DatabasePool = AsyncConnectionPool(
        settings.database_url.get_secret_value(),
        min_size=settings.database_pool_min_size, max_size=settings.database_pool_max_size,
        timeout=settings.database_timeout_seconds, max_waiting=20, open=False,
        check=AsyncConnectionPool.check_connection,
        kwargs={
            "autocommit": True, "row_factory": dict_row,
            "connect_timeout": max(1, math.ceil(settings.database_timeout_seconds)),
            "options": f"-c statement_timeout={settings.database_statement_timeout_ms} "
                       f"-c lock_timeout={math.ceil(settings.database_timeout_seconds * 1000)} "
                       f"-c search_path={settings.database_schema},public",
        },
        name="learntrail-ai",
    )
    try:
        try:
            await pool.open(wait=True, timeout=settings.database_timeout_seconds)
        except (psycopg.Error, PoolTimeout, ValueError):
            raise RuntimeError("AI PostgreSQL pool could not be initialized") from None
        yield pool
    finally:
        with anyio.CancelScope(shield=True):
            await pool.close()


@asynccontextmanager
async def transaction(pool: DatabasePool | None) -> AsyncIterator[psycopg.AsyncConnection[dict[str, Any]]]:
    if pool is None:
        raise AppError("DATABASE_NOT_CONFIGURED", "尚未配置 AI PostgreSQL 数据库。", 503)
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                yield conn
    except (psycopg.Error, PoolTimeout, TooManyRequests):
        raise AppError("DATABASE_UNAVAILABLE", "会话存储暂不可用，请稍后重试。", 503, True) from None
