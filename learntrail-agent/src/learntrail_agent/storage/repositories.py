"""SQL persistence; every session operation checks the authenticated owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from learntrail_agent.errors import AppError
from learntrail_agent.schemas import ChatResponse, HistoryTurn, RunInfo, SessionDetail, SessionInfo, SessionList, Usage
from learntrail_agent.storage.pool import DatabasePool, transaction


@dataclass(frozen=True)
class StartedRun:
    id: UUID
    user_message_id: UUID


class SessionRepository:
    def __init__(self, pool: DatabasePool | None):
        self.pool = pool

    async def _owned(
        self, conn: AsyncConnection[dict[str, Any]], user_id: int, session_id: UUID, *, lock: bool = False,
    ) -> dict[str, Any]:
        query = "SELECT * FROM ai_sessions WHERE id = %s AND user_id = %s"
        if lock:
            query += " FOR UPDATE"
        row = await (await conn.execute(query, (session_id, user_id))).fetchone()
        if row is None:
            # Same result for an unknown UUID and a different owner's session.
            raise AppError("SESSION_NOT_FOUND", "会话不存在。", 404)
        return row

    async def create(self, user_id: int, title: str) -> SessionInfo:
        async with transaction(self.pool) as conn:
            row = await (await conn.execute(
                "INSERT INTO ai_sessions (id, user_id, title) VALUES (%s, %s, %s) RETURNING *",
                (uuid4(), user_id, title),
            )).fetchone()
            return SessionInfo.model_validate(row)

    async def list(self, user_id: int, limit: int, offset: int) -> SessionList:
        async with transaction(self.pool) as conn:
            count = await (await conn.execute(
                "SELECT count(*) AS total FROM ai_sessions WHERE user_id = %s", (user_id,),
            )).fetchone()
            rows = await (await conn.execute(
                "SELECT * FROM ai_sessions WHERE user_id = %s ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
                (user_id, limit, offset),
            )).fetchall()
            return SessionList(items=rows, total=count["total"], limit=limit, offset=offset)

    async def detail(self, user_id: int, session_id: UUID, limit: int, offset: int) -> SessionDetail:
        async with transaction(self.pool) as conn:
            session = await self._owned(conn, user_id, session_id)
            count = await (await conn.execute(
                "SELECT count(*) AS total FROM ai_messages WHERE session_id = %s", (session_id,),
            )).fetchone()
            messages = await (await conn.execute(
                """SELECT m.*,r.kind AS run_kind,coalesce(r.agent_data->'evidence','[]'::jsonb) AS run_evidence,
                          CASE WHEN m.role='assistant' THEN coalesce(r.agent_data->'citations','[]'::jsonb)
                               ELSE '[]'::jsonb END AS citations
                   FROM ai_messages m JOIN ai_runs r ON r.id=m.run_id AND r.session_id=m.session_id
                   WHERE m.session_id = %s ORDER BY m.created_at, m.sequence LIMIT %s OFFSET %s""",
                (session_id, limit, offset),
            )).fetchall()
            runs = await (await conn.execute(
                "SELECT * FROM ai_runs WHERE session_id = %s ORDER BY created_at DESC, id DESC LIMIT 20",
                (session_id,),
            )).fetchall()
            return SessionDetail(
                **SessionInfo.model_validate(session).model_dump(), messages=messages,
                total_messages=count["total"], message_limit=limit, message_offset=offset, recent_runs=runs,
            )

    async def start_run(
        self, user_id: int, session_id: UUID, content: str, model: str, lease_seconds: float,
        *, kind: Literal["chat", "agent"] = "chat",
    ) -> StartedRun:
        run = StartedRun(uuid4(), uuid4())
        async with transaction(self.pool) as conn:
            await self._owned(conn, user_id, session_id, lock=True)
            await conn.execute(
                """WITH expired AS (
                    UPDATE ai_runs SET status = 'failed', error_code = 'RUN_EXPIRED', finished_at = clock_timestamp()
                    WHERE session_id = %s AND status = 'running' AND expires_at <= clock_timestamp() RETURNING id
                ) UPDATE ai_messages SET status = 'failed'
                  WHERE run_id IN (SELECT id FROM expired) AND status = 'pending'""", (session_id,),
            )
            active = await (await conn.execute(
                "SELECT id FROM ai_runs WHERE session_id = %s AND status = 'running'", (session_id,),
            )).fetchone()
            if active is not None:
                raise AppError("SESSION_BUSY", "该会话正在生成回复，请稍后重试。", 409, True)
            await conn.execute(
                """INSERT INTO ai_runs (id, session_id, status, model, expires_at,kind,agent_data)
                   VALUES (%s, %s, 'running', %s, clock_timestamp() + %s * interval '1 second',%s,
                           '{"evidence":[]}'::jsonb)""",
                (run.id, session_id, model, lease_seconds,kind),
            )
            await conn.execute(
                """INSERT INTO ai_messages (id, session_id, run_id, role, content, status)
                   VALUES (%s, %s, %s, 'user', %s, 'pending')""",
                (run.user_message_id, session_id, run.id, content),
            )
            await conn.execute("UPDATE ai_sessions SET updated_at = clock_timestamp() WHERE id = %s", (session_id,))
        return run

    async def history(self, user_id: int, session_id: UUID, max_turns: int) -> list[tuple[str, str]]:
        async with transaction(self.pool) as conn:
            await self._owned(conn, user_id, session_id)
            rows = await (await conn.execute(
                """SELECT u.content AS question, a.content AS answer
                   FROM ai_runs r
                   JOIN ai_messages u ON u.run_id = r.id AND u.role = 'user' AND u.status = 'completed'
                   JOIN ai_messages a ON a.run_id = r.id AND a.role = 'assistant' AND a.status = 'completed'
                   WHERE r.session_id = %s AND r.status = 'succeeded' AND r.finish_reason = 'stop' AND r.kind='chat'
                   ORDER BY r.created_at DESC, r.id DESC LIMIT %s""", (session_id, max_turns),
            )).fetchall()
            return [(row["question"], row["answer"]) for row in reversed(rows)]

    async def agent_history(self, user_id: int, session_id: UUID, max_turns: int) -> list[HistoryTurn]:
        async with transaction(self.pool) as conn:
            await self._owned(conn,user_id,session_id)
            rows = await (await conn.execute(
                """SELECT u.content AS question,a.content AS answer,r.kind,
                          coalesce(r.agent_data->'evidence','[]'::jsonb) AS evidence,
                          coalesce(r.agent_data->'citations','[]'::jsonb) AS citations
                   FROM ai_runs r
                   JOIN ai_messages u ON u.run_id=r.id AND u.role='user' AND u.status='completed'
                   JOIN ai_messages a ON a.run_id=r.id AND a.role='assistant' AND a.status='completed'
                   WHERE r.session_id=%s AND r.status='succeeded' AND r.finish_reason='stop'
                   ORDER BY r.created_at DESC,r.id DESC LIMIT %s""",(session_id,max_turns),
            )).fetchall()
            return [HistoryTurn.model_validate(row) for row in reversed(rows)]

    async def complete_run(
        self, user_id: int, session_id: UUID, run_id: UUID, reply: ChatResponse, duration_ms: int,
    ) -> UUID:
        assistant_id = uuid4()
        async with transaction(self.pool) as conn:
            await self._owned(conn, user_id, session_id, lock=True)
            updated = await (await conn.execute(
                """UPDATE ai_runs SET status = 'succeeded', model = %s, provider_response_id = %s,
                   finish_reason = %s, usage = %s, duration_ms = %s, finished_at = clock_timestamp()
                   WHERE id = %s AND session_id = %s AND status = 'running' RETURNING id""",
                (reply.model, reply.id or None, reply.finish_reason,
                 Jsonb(reply.usage.model_dump()) if reply.usage else None, duration_ms, run_id, session_id),
            )).fetchone()
            if updated is None:
                raise AppError("RUN_NOT_ACTIVE", "生成任务已结束，请重新发起请求。", 409)
            await conn.execute(
                """INSERT INTO ai_messages (id, session_id, run_id, role, content, status)
                   VALUES (%s, %s, %s, 'assistant', %s, 'completed')""",
                (assistant_id, session_id, run_id, reply.content),
            )
            await conn.execute(
                "UPDATE ai_messages SET status = 'completed' WHERE run_id = %s AND role = 'user'",
                (run_id,),
            )
            await conn.execute("UPDATE ai_sessions SET updated_at = clock_timestamp() WHERE id = %s", (session_id,))
        return assistant_id

    async def fail_run(
        self, user_id: int, session_id: UUID, run_id: UUID, error_code: str,
        *, status: Literal["failed", "cancelled"] = "failed",
        duration_ms: int | None = None, usage: Usage | None = None,
    ) -> None:
        async with transaction(self.pool) as conn:
            await self._owned(conn, user_id, session_id, lock=True)
            updated = await (await conn.execute(
                """UPDATE ai_runs SET status = %s, error_code = %s, finished_at = clock_timestamp()
                   WHERE id = %s AND session_id = %s AND status = 'running' RETURNING id""",
                (status, error_code, run_id, session_id),
            )).fetchone()
            if updated is not None:
                await conn.execute(
                    "UPDATE ai_messages SET status = 'failed' WHERE run_id = %s AND status = 'pending'", (run_id,),
                )
            if duration_ms is not None or usage is not None:
                await conn.execute(
                    """UPDATE ai_runs SET duration_ms = COALESCE(%s, duration_ms), usage = COALESCE(%s, usage)
                       WHERE id = %s AND session_id = %s AND status IN ('failed', 'cancelled')""",
                    (duration_ms, Jsonb(usage.model_dump()) if usage else None, run_id, session_id),
                )

    async def get_run(self, user_id: int, session_id: UUID, run_id: UUID) -> RunInfo:
        async with transaction(self.pool) as conn:
            await self._owned(conn, user_id, session_id)
            row = await (await conn.execute(
                "SELECT * FROM ai_runs WHERE id = %s AND session_id = %s", (run_id, session_id),
            )).fetchone()
            if row is None:
                raise AppError("RUN_NOT_FOUND", "生成任务不存在。", 404)
            return RunInfo.model_validate(row)

    async def cancel_run(self, user_id: int, session_id: UUID, run_id: UUID) -> RunInfo:
        async with transaction(self.pool) as conn:
            await self._owned(conn, user_id, session_id, lock=True)
            row = await (await conn.execute(
                "SELECT * FROM ai_runs WHERE id = %s AND session_id = %s", (run_id, session_id),
            )).fetchone()
            if row is None:
                raise AppError("RUN_NOT_FOUND", "生成任务不存在。", 404)
            if row["status"] == "running":
                row = await (await conn.execute(
                    """UPDATE ai_runs SET status = 'cancelled', error_code = 'RUN_CANCELLED',
                       finished_at = clock_timestamp() WHERE id = %s AND session_id = %s RETURNING *""",
                    (run_id, session_id),
                )).fetchone()
                await conn.execute(
                    "UPDATE ai_messages SET status = 'failed' WHERE run_id = %s AND status = 'pending'", (run_id,),
                )
            return RunInfo.model_validate(row)
