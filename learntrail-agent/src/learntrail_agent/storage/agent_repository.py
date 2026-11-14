"""Owner-checked Agent event persistence; evidence and payload commit together."""

from uuid import UUID

from psycopg.types.json import Jsonb

from learntrail_agent.errors import AppError
from learntrail_agent.schemas import RunInfo, Usage
from learntrail_agent.storage.pool import transaction
from learntrail_agent.storage.repositories import SessionRepository, StartedRun


class AgentRepository:
    def __init__(self, sessions: SessionRepository) -> None:
        self.sessions = sessions
        self.pool = sessions.pool

    async def append(
        self, user_id: int, session_id: UUID, run_id: UUID,
        event: dict[str, object], evidence: list[dict[str, object]],
    ) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id,lock=True)
            row = await (await conn.execute(
                "SELECT status FROM ai_runs WHERE id=%s AND session_id=%s AND kind='agent' FOR UPDATE",
                (run_id,session_id),
            )).fetchone()
            if row is None:
                raise AppError("RUN_NOT_FOUND","运行不存在。",404)
            if row["status"] != "running" and event["kind"] not in {"tool_end","run_end"}:
                raise AppError("RUN_CANCELLED","运行已停止。",409)
            await conn.execute(
                """INSERT INTO ai_agent_events (run_id,session_id,sequence,kind,step,duration_ms,payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (run_id,session_id,event["sequence"],event["kind"],event["step"],event["duration_ms"],Jsonb(event["payload"])),
            )
            await conn.execute(
                """UPDATE ai_runs SET agent_data=jsonb_set(agent_data,'{evidence}',%s)
                   WHERE id=%s AND session_id=%s""",(Jsonb(evidence),run_id,session_id),
            )
            if event["kind"] == "history_sources":
                await conn.execute("UPDATE ai_runs SET agent_data=jsonb_set(agent_data,'{citations}',%s) WHERE id=%s",
                    (Jsonb(event["payload"]["items"][:20]),run_id))
            if event["kind"] == "tool_result" and event["payload"].get("ok"):
                result = event["payload"]
                data = result.get("data") or {}
                name=result.get("name","")
                sources = data.get("matches",[]) if name=="search_knowledge_base" or name.endswith("_search_knowledge_base") else (
                    data.get("articles",[]) if name=="search_articles" or name.endswith("_search_articles") else [])
                if sources:
                    old = await (await conn.execute("SELECT agent_data FROM ai_runs WHERE id=%s",(run_id,))).fetchone()
                    citations = old["agent_data"].get("citations",[])
                    for source in sources:
                        item = {key:source[key] for key in ("article_id","slug","title","section","ordinal",
                            "quote","start_char","end_char","position_basis","content_hash","source_type") if key in source}
                        if item not in citations:
                            citations.append(item)
                    await conn.execute("UPDATE ai_runs SET agent_data=jsonb_set(agent_data,'{citations}',%s) WHERE id=%s",
                                       (Jsonb(citations[:20]),run_id))

    async def pause(self, user_id: int, session_id: UUID, run_id: UUID, duration_ms: int,
                    usage: Usage | None) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id,lock=True)
            row=await (await conn.execute("""UPDATE ai_runs SET status='paused',finish_reason='interrupt',
                duration_ms=%s,usage=%s,finished_at=clock_timestamp() WHERE id=%s AND session_id=%s
                AND kind='agent' AND status='running' RETURNING id""",
                (duration_ms,Jsonb(usage.model_dump()) if usage else None,run_id,session_id))).fetchone()
            if row is None:
                raise AppError("RUN_NOT_ACTIVE","生成任务已结束。",409)

    async def resume(self, user_id: int, session_id: UUID, run_id: UUID, lease: float) -> tuple[StartedRun,str]:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id,lock=True)
            row=await (await conn.execute("""SELECT *,expires_at<=clock_timestamp() AS expired FROM ai_runs
                WHERE id=%s AND session_id=%s AND kind='agent' FOR UPDATE""",(run_id,session_id))).fetchone()
            if row is None:
                raise AppError("RUN_NOT_FOUND","运行不存在。",404)
            latest=await (await conn.execute("SELECT id FROM ai_runs WHERE session_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                                             (session_id,))).fetchone()
            if latest["id"]!=run_id:
                raise AppError("GRAPH_RUN_SUPERSEDED","该工作流已被更新的会话执行替代。",409)
            if row["status"]=="succeeded":
                raise AppError("GRAPH_ALREADY_COMPLETED","此工作流已经完成。",409)
            if row["status"]=="running" and not row["expired"]:
                raise AppError("SESSION_BUSY","该会话仍在生成，请先停止或等待租约到期。",409,True)
            other=await (await conn.execute("SELECT id FROM ai_runs WHERE session_id=%s AND status='running' AND id<>%s",
                                            (session_id,run_id))).fetchone()
            if other:
                raise AppError("SESSION_BUSY","该会话正在生成。",409,True)
            count=int(row["agent_data"].get("resume_count",0))
            if count>=3:
                raise AppError("GRAPH_RESUME_LIMIT","此任务已达到三次恢复上限，请重新提问。",422)
            message=await (await conn.execute("SELECT id,content FROM ai_messages WHERE run_id=%s AND role='user'",(run_id,))).fetchone()
            assistant=await (await conn.execute("SELECT id FROM ai_messages WHERE run_id=%s AND role='assistant'",(run_id,))).fetchone()
            if not message or assistant:
                raise AppError("GRAPH_ALREADY_COMPLETED","此任务不能再次生成回复。",409)
            await conn.execute("""UPDATE ai_runs SET status='running',finished_at=NULL,error_code=NULL,
                finish_reason=NULL,expires_at=clock_timestamp()+%s*interval '1 second',
                agent_data=jsonb_set(agent_data,'{resume_count}',%s) WHERE id=%s""",
                (lease,Jsonb(count+1),run_id))
            await conn.execute("UPDATE ai_messages SET status='pending' WHERE id=%s",(message["id"],))
            return StartedRun(run_id,message["id"]),message["content"]

    async def trace(self, user_id: int, session_id: UUID, run_id: UUID) -> tuple[RunInfo, list[dict]]:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id)
            row = await (await conn.execute(
                "SELECT * FROM ai_runs WHERE id=%s AND session_id=%s AND kind='agent'",(run_id,session_id),
            )).fetchone()
            if row is None:
                raise AppError("RUN_NOT_FOUND","运行不存在。",404)
            events = await (await conn.execute(
                """SELECT sequence,kind,step,duration_ms,payload,created_at FROM ai_agent_events
                   WHERE run_id=%s AND session_id=%s ORDER BY sequence""",(run_id,session_id),
            )).fetchall()
            return RunInfo.model_validate(row), events
