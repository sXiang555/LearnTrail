"""Controlled model responses, real Graph/SQL/MCP. Explicitly not model-quality scores."""

import json
import sys
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

from learntrail_agent.agent.advanced_models import ApprovalDecision,MemoryCreate,MemoryUpdate
from learntrail_agent.agent.schemas import AgentChatRequest
from learntrail_agent.agent.tools import ToolRegistry,ToolExecutor
from learntrail_agent.clients.mcp import MCPClient,MCPProfile
from learntrail_agent.clients.tool_models import ToolChatResponse,ToolCall,FunctionCall
from learntrail_agent.errors import AppError
from learntrail_agent.services.agent import AgentService
from learntrail_agent.services.chat import ChatService
from learntrail_agent.storage.agent_repository import AgentRepository


class ControlledModel:
    """Only in the offline contract suite; no fabricated provider Usage."""
    def __init__(self,replies):self.replies=list(replies)
    async def complete_with_tools(self,request):
        if not self.replies:raise RuntimeError("Controlled trajectory exhausted")
        return self.replies.pop(0)


def response(name=None,args=None):
    return ToolChatResponse(id="controlled-"+uuid4().hex,model="controlled-contract-model",usage=None,
        content=None if name else "受控契约执行结束。",finish_reason="tool_calls" if name else "stop",
        tool_calls=[ToolCall(id="call-"+uuid4().hex,function=FunctionCall(name=name,arguments=json.dumps(args,ensure_ascii=False)))] if name else [])


async def run_contracts(state,user,token,dataset) -> list[dict]:
    advanced=state.advanced;sessions=state.sessions;rows=[];spec=dataset["controlled_contracts"]
    def service(replies):
        model=ControlledModel(replies)
        return AgentService(sessions,AgentRepository(sessions),state.agent_tools,model,
            ChatService(sessions,model,state.settings),state.settings,state.checkpointer,advanced)
    async def case(identifier,operation):
        start=time.monotonic()
        try:
            details=await operation()
            checks=details.pop("checks")
            rows.append({"id":identifier,"verification":"executed","source":"controlled_model_real_graph_sql_or_mcp",
                "duration_ms":int((time.monotonic()-start)*1000),"checks":checks,
                "metrics":{"trajectory_success":all(checks.values())},**details})
        except Exception as error:
            code=error.code if isinstance(error,AppError) else "CONTRACT_FAILED"
            rows.append({"id":identifier,"verification":"executed","source":"controlled_model_real_graph_sql_or_mcp",
                "duration_ms":int((time.monotonic()-start)*1000),"metrics":{"trajectory_success":False},"error":code})
    async def approval(action):
        sid=(await sessions.create(user.user_id,"评测 HITL "+action)).id
        demo={"title":spec["demo_title"],"outline":spec["demo_outline"]}
        agent=service([response("save_learning_plan_demo",demo)])
        paused=await agent.execute(await agent.prepare(user.user_id,sid,AgentChatRequest(content="请保存评测演示计划"),token))
        row=(await advanced.approvals.list(user.user_id,sid))[0]
        rejected_owner=False;tamper=False
        try:await advanced.approvals.get(user.user_id+1,sid,row["approval_id"])
        except AppError as e:rejected_owner=e.code=="SESSION_NOT_FOUND"
        try:await advanced.approvals.decide(user.user_id,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash="0"*64))
        except AppError as e:tamper=e.code=="APPROVAL_TAMPERED"
        if action=="expire":
            async with state.pool.connection() as conn:
                await conn.execute("UPDATE ai_approvals SET created_at=clock_timestamp()-interval '1 minute',expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(row["approval_id"],))
        elif action=="cancel":
            await sessions.cancel_run(user.user_id,sid,paused.run_id);await advanced.approvals.cancel_run(user.user_id,sid,paused.run_id)
        else:
            for _ in range(2):await advanced.approvals.decide(user.user_id,sid,row["approval_id"],ApprovalDecision(action=action,expected_hash=row["arguments_hash"]))
        restarted=service([response()])
        done=await restarted.execute(await restarted.prepare_resume(user.user_id,sid,paused.run_id,token))
        final=await advanced.approvals.get(user.user_id,sid,row["approval_id"])
        async with state.pool.connection() as conn:
            count=(await (await conn.execute("SELECT count(*) AS n FROM ai_demo_actions WHERE approval_id=%s",(row["approval_id"],))).fetchone())["n"]
        return {"checks":{"paused":paused.status=="paused","owner_isolation":rejected_owner,"parameter_tamper_rejected":tamper,
            "recovered":done.status=="succeeded","correct_approval_state":final["status"]=={"approve":"executed","reject":"rejected","expire":"expired","cancel":"cancelled"}[action],
            "effect_count":count==(1 if action=="approve" else 0)},"effect_count":count,"status":final["status"]}
    for action in ("approve","reject","expire","cancel"):
        await case("hitl-"+action,lambda a=action:approval(a))

    async def memory():
        quote=spec["memory_content"]
        sid=(await sessions.create(user.user_id,"评测记忆提取")).id
        args={"memory_type":"preference","key":"answer_style","content":quote,"source_quote":quote}
        agent=service([response("remember_learning_preference",args)])
        paused=await agent.execute(await agent.prepare(user.user_id,sid,AgentChatRequest(content=spec["memory_quote"]),token))
        before=await advanced.memories.list(user.user_id)
        row=(await advanced.approvals.list(user.user_id,sid))[0]
        await advanced.approvals.decide(user.user_id,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
        agent=service([response()]);await agent.execute(await agent.prepare_resume(user.user_id,sid,paused.run_id,token))
        stored=(await advanced.memories.list(user.user_id))[0]
        duplicate=await advanced.memories.create(user.user_id,MemoryCreate(memory_type="preference",key="answer_style",content=quote,confirmed=True))
        related=await advanced.memories.relevant(user.user_id,"请用代码解释异步",3,2000)
        conflict=False
        try:await advanced.memories.create(user.user_id,MemoryCreate(memory_type="preference",key="answer_style",content="另一种明确偏好",confirmed=True))
        except AppError as e:conflict=e.code=="MEMORY_CONFLICT"
        other=await advanced.memories.list(user.user_id+1)
        changed=await advanced.memories.update(user.user_id,stored.id,MemoryUpdate(memory_type="preference",key="answer_style",content="评测更新偏好",confirmed=True,version=stored.version))
        deleted=await advanced.memories.delete(user.user_id,changed.id,changed.version)
        return {"checks":{"no_unapproved_write":not before,"extraction_grounded":stored.content==quote and stored.source_kind=="user_quote",
            "deduplicated":duplicate.id==stored.id,"relevant_retrieval":len(related)==1 and related[0]["memory_id"]==str(stored.id),
            "conflict_rejected":conflict,"user_isolated":not other,"versioned_update":changed.version==stored.version+1,
            "deleted":deleted.status=="deleted"},"extraction_cases":1,"retrieval_cases":1}
    await case("memory-grounded-confirmed-crud",memory)

    async def skills():
        base=advanced.context(await state.agent_tools.context(token))
        registry=state.agent_tools.registry();advanced.extend(registry)
        bound=await advanced.bind(registry,base,"分析我的学习进度")
        automatic=await bound.select()
        explicit=await advanced.bind(registry,base,"解释文章知识","knowledge-learning")
        selected=await explicit.select()
        return {"checks":{"auto_correct":automatic["name"]=="learning-analysis","explicit_correct":selected["name"]=="knowledge-learning",
            "tool_scope_enforced":"get_learning_progress" not in explicit.context.allowed_tools},"selection_cases":2}
    await case("skills-auto-explicit",skills)

    async def mcp():
        profile=MCPProfile(id="evaldemo",trusted=True,transport="stdio",command=sys.executable,args=["-m","learntrail_agent.mcp_demo_server"],
            allow_tools={"preview_learning_outline":"read_only"})
        ctx=replace(await state.agent_tools.context(token),permissions=frozenset({"mcp:evaldemo"}))
        client=MCPClient([profile],state.settings.mcp_first_party_url);registry=ToolRegistry()
        start=time.monotonic();await client.register(registry,ctx);discovery_ms=int((time.monotonic()-start)*1000)
        call=ToolCall(id="controlled-mcp",function=FunctionCall(name="mcp_evaldemo_preview_learning_outline",arguments=json.dumps({"topic":spec["mcp_topic"]})))
        output=await ToolExecutor(registry).execute(call,ctx)
        return {"checks":{"allowlist_discovery":len(registry.tools())==1,"actual_call":output.ok and output.data["result"]["preview_only"]},
            "discovery_ms":discovery_ms,"call_ms":output.duration_ms,"transport":"stdio"}
    await case("mcp-local-discovery-call",mcp)
    return rows
