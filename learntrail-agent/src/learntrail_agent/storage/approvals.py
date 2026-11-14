"""Owner-bound approval state machine; no business effect happens at decision time."""

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from learntrail_agent.agent.advanced_models import ApprovalDecision, DemoPlan, canonical, digest
from learntrail_agent.agent.tools import Tool, ToolCall, ToolContext, ToolOutput
from learntrail_agent.errors import AppError
from learntrail_agent.storage.checkpoints import CheckpointCipher
from learntrail_agent.storage.pool import transaction
from learntrail_agent.storage.repositories import SessionRepository

DENIED={"pending":"APPROVAL_REQUIRED","rejected":"APPROVAL_REJECTED","expired":"APPROVAL_EXPIRED",
        "cancelled":"APPROVAL_CANCELLED","failed":"APPROVAL_EXECUTION_UNCERTAIN","unknown":"APPROVAL_EXECUTION_UNCERTAIN"}


class ApprovalRepository:
    def __init__(self, sessions: SessionRepository, cipher: CheckpointCipher, ttl_seconds: int = 900) -> None:
        self.sessions,self.pool,self.cipher,self.ttl=sessions,sessions.pool,cipher,ttl_seconds

    @staticmethod
    def binding(tool: Tool, arguments: dict, resource: dict) -> str:
        return digest({"tool":tool.name,"revision":tool.revision(),"arguments":arguments,"resource":resource})

    def decode(self, kind: str, blob: bytes) -> dict:
        try:
            value=self.cipher.loads_typed((kind,bytes(blob)))
            if not isinstance(value,dict):raise ValueError()
            return value
        except Exception:
            raise AppError("APPROVAL_TAMPERED","审批快照或密钥无法验证。",409) from None

    def result(self, row: dict) -> ToolOutput | None:
        return ToolOutput.model_validate(self.decode(row["result_type"],row["result_blob"])) if row["result_blob"] else None

    def public(self, row: dict) -> dict:
        arguments=self.decode(row["arguments_type"],row["arguments_blob"])
        if digest({"tool":row["tool_name"],"revision":row["tool_revision"],"arguments":arguments,
                   "resource":row["resource_snapshot"]})!=row["arguments_hash"]:
            raise AppError("APPROVAL_TAMPERED","审批资源快照已变化。",409)
        result=self.result(row)
        return {"approval_id":row["id"],"session_id":row["session_id"],"run_id":row["run_id"],
            "tool_name":row["tool_name"],"arguments":arguments,
            "arguments_hash":row["arguments_hash"],"resource":row["resource_snapshot"],"status":row["status"],
            "created_at":row["created_at"],"expires_at":row["expires_at"],"decided_at":row["decided_at"],
            "finished_at":row["finished_at"],"result":result.data if result else None,"error_code":row["error_code"]}

    async def expire(self, user_id: int, session_id: UUID) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id)
            await conn.execute("""UPDATE ai_approvals SET status='expired',finished_at=clock_timestamp()
                WHERE user_id=%s AND session_id=%s AND status IN ('pending','approved') AND expires_at<=clock_timestamp()""",
                (user_id,session_id))
            await conn.execute("""UPDATE ai_approvals a SET status='cancelled',finished_at=clock_timestamp()
                FROM ai_runs r WHERE a.run_id=r.id AND a.user_id=%s AND a.session_id=%s
                AND a.status IN ('pending','approved') AND r.status='cancelled'""",(user_id,session_id))

    async def _run(self, conn, context: ToolContext, *, active: bool = False) -> dict:
        await self.sessions._owned(conn,context.user_id,context.session_id,lock=True)
        row=await (await conn.execute("SELECT * FROM ai_runs WHERE id=%s AND session_id=%s AND kind='agent' FOR UPDATE",
                                     (context.run_id,context.session_id))).fetchone()
        if row is None:raise AppError("RUN_NOT_FOUND","运行不存在。",404)
        if active and row["status"]!="running":raise AppError("APPROVAL_CANCELLED","运行未处于执行状态。",409)
        return row

    async def find(self, context: ToolContext, call_id: str) -> dict | None:
        await self.expire(context.user_id,context.session_id)
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,context.user_id,context.session_id)
            return await (await conn.execute("SELECT * FROM ai_approvals WHERE user_id=%s AND session_id=%s AND run_id=%s AND call_id=%s",
                (context.user_id,context.session_id,context.run_id,call_id))).fetchone()

    def validate(self, row: dict, call: ToolCall, tool: Tool, arguments: dict, resource: dict) -> None:
        if (row["tool_name"]!=tool.name or row["call_id"]!=call.id or row["tool_revision"]!=tool.revision()
                or self.decode(row["arguments_type"],row["arguments_blob"])!=arguments
                or row["resource_snapshot"]!=resource
                or row["arguments_hash"]!=self.binding(tool,arguments,resource)):
            raise AppError("APPROVAL_TAMPERED","参数、策略或资源版本已变化。",409)

    async def ensure(self, context: ToolContext, call: ToolCall, tool: Tool, arguments: dict, resource: dict) -> dict:
        if len(canonical(arguments))>8000:
            raise AppError("INVALID_TOOL_ARGUMENTS","审批参数超出长度限制。",422)
        encoded=self.cipher.dumps_typed(arguments)
        async with transaction(self.pool) as conn:
            await self._run(conn,context,active=True)
            await conn.execute("""INSERT INTO ai_approvals(id,user_id,session_id,run_id,call_id,tool_name,tool_revision,
                arguments_hash,arguments_type,arguments_blob,resource_snapshot,status,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',clock_timestamp()+%s*interval '1 second')
                ON CONFLICT(run_id,call_id) DO NOTHING""",(uuid4(),context.user_id,context.session_id,context.run_id,
                call.id,tool.name,tool.revision(),self.binding(tool,arguments,resource),*encoded,Jsonb(resource),self.ttl))
            row=await (await conn.execute("SELECT * FROM ai_approvals WHERE run_id=%s AND call_id=%s FOR UPDATE",
                                        (context.run_id,call.id))).fetchone()
            self.validate(row,call,tool,arguments,resource)
            return row

    async def list(self, user_id: int, session_id: UUID, run_id: UUID | None = None) -> list[dict]:
        await self.expire(user_id,session_id)
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id)
            rows=await (await conn.execute("""SELECT * FROM ai_approvals WHERE user_id=%s AND session_id=%s
                AND (%s::uuid IS NULL OR run_id=%s) ORDER BY created_at DESC,id DESC LIMIT 50""",
                (user_id,session_id,run_id,run_id))).fetchall()
            return [self.public(row) for row in rows]

    async def get(self, user_id: int, session_id: UUID, approval_id: UUID) -> dict:
        await self.expire(user_id,session_id)
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id)
            row=await (await conn.execute("SELECT * FROM ai_approvals WHERE id=%s AND user_id=%s AND session_id=%s",
                                         (approval_id,user_id,session_id))).fetchone()
            if row is None:raise AppError("APPROVAL_NOT_FOUND","审批不存在。",404)
            return self.public(row)

    async def decide(self, user_id: int, session_id: UUID, approval_id: UUID, body: ApprovalDecision) -> dict:
        await self.expire(user_id,session_id)
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id,lock=True)
            row=await (await conn.execute("SELECT * FROM ai_approvals WHERE id=%s AND user_id=%s AND session_id=%s FOR UPDATE",
                                         (approval_id,user_id,session_id))).fetchone()
            if row is None:raise AppError("APPROVAL_NOT_FOUND","审批不存在。",404)
            if row["arguments_hash"]!=body.expected_hash:
                raise AppError("APPROVAL_TAMPERED","审批版本不一致，请刷新。",409)
            if row["status"]=="expired":raise AppError("APPROVAL_EXPIRED","审批已过期。",409)
            target={"approve":"approved","reject":"rejected","cancel":"cancelled"}[body.action]
            if row["status"]==target or body.action=="approve" and row["status"] in {"executing","executed"}:
                return self.public(row)
            if row["status"] not in {"pending","approved"}:
                raise AppError("APPROVAL_NOT_PENDING","此审批已处理。",409)
            latest=await (await conn.execute("SELECT id,status FROM ai_runs WHERE session_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                                             (session_id,))).fetchone()
            if latest["id"]!=row["run_id"] or latest["status"]!="paused":
                raise AppError("APPROVAL_NOT_PENDING","该任务已替代、取消或仍在执行。",409)
            row=await (await conn.execute("UPDATE ai_approvals SET status=%s,decided_at=clock_timestamp() WHERE id=%s RETURNING *",
                                        (target,approval_id))).fetchone()
            return self.public(row)

    async def claim(self, context: ToolContext, call: ToolCall, tool: Tool, arguments: dict, resource: dict) -> dict:
        await self.expire(context.user_id,context.session_id)
        async with transaction(self.pool) as conn:
            await self._run(conn,context,active=True)
            row=await (await conn.execute("SELECT * FROM ai_approvals WHERE user_id=%s AND run_id=%s AND call_id=%s FOR UPDATE",
                                         (context.user_id,context.run_id,call.id))).fetchone()
            if row is None:raise AppError("APPROVAL_REQUIRED","需要用户确认。",409)
            self.validate(row,call,tool,arguments,resource)
            if row["status"]=="executed":return row
            if row["status"]=="executing" and not tool.idempotent:
                raise AppError("APPROVAL_EXECUTION_UNCERTAIN","不能重复执行结果未知的操作。",409)
            if row["status"] not in {"approved","executing"}:
                code=DENIED.get(row["status"],"APPROVAL_REQUIRED")
                raise AppError(code,"没有有效的执行批准。",409)
            return await (await conn.execute("UPDATE ai_approvals SET status='executing' WHERE id=%s RETURNING *",(row["id"],))).fetchone()

    async def finish_on(self, conn, approval_id: UUID, output: ToolOutput) -> None:
        kind,blob=self.cipher.dumps_typed(output.model_dump())
        await conn.execute("""UPDATE ai_approvals SET status='executed',result_type=%s,result_blob=%s,
            finished_at=clock_timestamp(),error_code=NULL WHERE id=%s AND status IN ('executing','unknown')""",
            (kind,blob,approval_id))

    async def finish(self, context: ToolContext, approval_id: UUID, output: ToolOutput) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,context.user_id,context.session_id,lock=True)
            await self.finish_on(conn,approval_id,output)

    async def failed(self, context: ToolContext, approval_id: UUID, *, uncertain: bool) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,context.user_id,context.session_id,lock=True)
            await conn.execute("UPDATE ai_approvals SET status=%s,error_code=%s,finished_at=clock_timestamp() WHERE id=%s AND user_id=%s AND status='executing'",
                ("unknown" if uncertain else "failed","APPROVAL_EXECUTION_UNCERTAIN" if uncertain else "TOOL_EXECUTION_FAILED",approval_id,context.user_id))

    async def cancel_run(self, user_id: int, session_id: UUID, run_id: UUID) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id,lock=True)
            await conn.execute("""UPDATE ai_approvals SET status=CASE WHEN status='executing' THEN 'unknown' ELSE 'cancelled' END,
                finished_at=clock_timestamp() WHERE user_id=%s AND session_id=%s AND run_id=%s
                AND status IN ('pending','approved','executing')""",(user_id,session_id,run_id))

    async def cancel_superseded(self, user_id: int, session_id: UUID, current_run: UUID) -> None:
        async with transaction(self.pool) as conn:
            await self.sessions._owned(conn,user_id,session_id,lock=True)
            await conn.execute("""UPDATE ai_approvals SET status=CASE WHEN status='executing' THEN 'unknown' ELSE 'cancelled' END,
                finished_at=clock_timestamp() WHERE user_id=%s AND session_id=%s AND run_id<>%s
                AND status IN ('pending','approved','executing')""",(user_id,session_id,current_run))

    async def demo_plan(self, arguments: DemoPlan, context: ToolContext) -> ToolOutput:
        async with transaction(self.pool) as conn:
            await self._run(conn,context,active=True)
            row=await (await conn.execute("SELECT * FROM ai_approvals WHERE id=%s AND user_id=%s FOR UPDATE",
                                         (context.approval_id,context.user_id))).fetchone()
            if row is None or row["status"] not in {"executing","executed"}:
                raise AppError("APPROVAL_REQUIRED","没有有效的操作批准。",409)
            if row["status"]=="executed":return self.result(row)
            draft_id=uuid4()
            await conn.execute("INSERT INTO ai_demo_actions(approval_id,user_id,draft_id,payload) VALUES(%s,%s,%s,%s)",
                               (context.approval_id,context.user_id,draft_id,Jsonb(arguments.model_dump())))
            output=ToolOutput(data={"draft_id":str(draft_id),"title":arguments.title,"status":"ai_demo_draft",
                "notice":"仅创建 AI 演示草稿，没有修改学习记录、积分或订单。"})
            await self.finish_on(conn,context.approval_id,output)
            return output
