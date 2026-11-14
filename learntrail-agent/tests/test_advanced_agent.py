"""Real isolated PostgreSQL, mocked model/Java; no paid calls."""

import json
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import ValidationError

from learntrail_agent.agent.advanced_models import ApprovalDecision, MemoryCreate, MemoryUpdate
from learntrail_agent.agent.schemas import AgentChatRequest
from learntrail_agent.errors import AppError
from learntrail_agent.services.capabilities import AdvancedRuntime
from learntrail_agent.storage.checkpoints import CheckpointCipher
from test_agent_sessions import service
from test_postgres_integration import postgres_repository
from test_tool_core import call, reply

pytestmark=pytest.mark.asyncio
ROOT=Path(__file__).parents[1]


@pytest_asyncio.fixture
async def advanced_repo(postgres_repository):
    async with postgres_repository.pool.connection() as conn:
        for name in ("V005__graph_checkpoints.sql","V006__advanced_agent.sql"):
            await conn.execute((ROOT/"migrations"/name).read_text(),prepare=False)
    return postgres_repository


def advanced(repo,settings):
    agent,llm,flag=service(repo,settings)
    runtime=AdvancedRuntime(repo,CheckpointCipher(b"x"*32),settings)
    runtime.extend(agent.loop.registry)
    agent.advanced=runtime
    agent.checkpointer=AsyncPostgresSaver(repo.pool,serde=CheckpointCipher(b"x"*32))
    return agent,llm,runtime


async def paused(repo,settings,name="save_learning_plan_demo",args=None,question="请保存演示计划"):
    agent,llm,runtime=advanced(repo,settings)
    sid=(await repo.create(41,"advanced test")).id
    args=args or {"title":"Python 学习计划","outline":"第一天学习异步接口，第二天练习测试。"}
    llm.complete_with_tools.side_effect=[reply(calls=[call(name,json.dumps(args,ensure_ascii=False))])]
    result=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content=question),"token"))
    assert result.status=="paused"
    rows=await runtime.approvals.list(41,sid)
    assert len(rows)==1 and rows[0]["status"]=="pending"
    return agent,llm,runtime,sid,result,rows[0]


async def count(repo,table):
    assert table in {"ai_demo_actions","ai_memories"}
    async with repo.pool.connection() as c:
        return (await (await c.execute("SELECT count(*) AS n FROM "+table)).fetchone())["n"]


async def test_approval_pause_owner_hash_repeat_and_restart(advanced_repo,settings):
    repo=advanced_repo
    agent,llm,runtime,sid,result,row=await paused(repo,settings)
    assert await count(repo,"ai_demo_actions")==0
    with pytest.raises(AppError) as e:
        await agent.prepare_resume(41,sid,result.run_id,"token")
    assert e.value.code=="APPROVAL_REQUIRED"
    with pytest.raises(AppError) as e:
        await runtime.approvals.decide(42,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    assert e.value.code=="SESSION_NOT_FOUND"
    with pytest.raises(AppError) as e:
        await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash="0"*64))
    assert e.value.code=="APPROVAL_TAMPERED"
    decision=ApprovalDecision(action="approve",expected_hash=row["arguments_hash"])
    for _ in range(2):
        assert (await runtime.approvals.decide(41,sid,row["approval_id"],decision))["status"]=="approved"
    assert await count(repo,"ai_demo_actions")==0
    restored,llm2,runtime2=advanced(repo,settings)
    llm2.complete_with_tools.side_effect=None
    llm2.complete_with_tools.return_value=reply(content="计划已在 AI 演示区保存。")
    done=await restored.execute(await restored.prepare_resume(41,sid,result.run_id,"token"))
    assert done.status=="succeeded" and await count(repo,"ai_demo_actions")==1
    assert (await runtime2.approvals.get(41,sid,row["approval_id"]))["status"]=="executed"
    await runtime2.approvals.decide(41,sid,row["approval_id"],decision)
    assert await count(repo,"ai_demo_actions")==1


@pytest.mark.parametrize("action,code",[("reject","APPROVAL_REJECTED"),("cancel","APPROVAL_CANCELLED"),("expire","APPROVAL_EXPIRED")])
async def test_approval_denied_never_executes(advanced_repo,settings,action,code):
    repo=advanced_repo
    agent,llm,runtime,sid,result,row=await paused(repo,settings)
    if action=="expire":
        async with repo.pool.connection() as c:
            await c.execute("UPDATE ai_approvals SET created_at=clock_timestamp()-interval '1 minute',expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(row["approval_id"],))
    else:
        await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action=action,expected_hash=row["arguments_hash"]))
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="未执行保存。")
    done=await agent.execute(await agent.prepare_resume(41,sid,result.run_id,"token"))
    assert done.status=="succeeded" and await count(repo,"ai_demo_actions")==0
    request=llm.complete_with_tools.await_args.args[0]
    assert any(code in str(m.content) for m in request.messages if m.role=="tool")


async def test_approval_snapshot_tamper_fails_closed(advanced_repo,settings):
    repo=advanced_repo
    agent,llm,runtime,sid,result,row=await paused(repo,settings)
    async with repo.pool.connection() as c:
        await c.execute("UPDATE ai_approvals SET resource_snapshot='{}'::jsonb WHERE id=%s",(row["approval_id"],))
    with pytest.raises(AppError) as e:
        await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    assert e.value.code=="APPROVAL_TAMPERED"
    assert await count(repo,"ai_demo_actions")==0


def memory(**kw):
    return MemoryCreate(memory_type="preference",key="answer_style",content="先给结论再给示例",confirmed=True,**kw)


async def test_memory_crud_conflict_owner_expiry_and_source(advanced_repo,settings):
    _,_,runtime=advanced(advanced_repo,settings);repo=runtime.memories
    sid=(await advanced_repo.create(41,"source")).id
    first=await repo.create(41,memory(source_session_id=sid))
    assert (await repo.create(41,memory())).id==first.id
    assert await repo.list(42)==[]
    with pytest.raises(AppError) as e:await repo.get(42,first.id)
    assert e.value.code=="MEMORY_NOT_FOUND"
    with pytest.raises(AppError) as e:await repo.create(41,MemoryCreate(**{**memory().model_dump(),"content":"多用代码说明"}))
    assert e.value.code=="MEMORY_CONFLICT"
    changed=await repo.update(41,first.id,MemoryUpdate(**{**memory().model_dump(),"content":"多用代码说明","version":1}))
    assert changed.version==2
    with pytest.raises(AppError):await repo.delete(41,first.id,1)
    assert (await repo.delete(41,first.id,2)).status=="deleted"
    fresh=await repo.create(41,memory(expires_at=datetime.now(timezone.utc)+timedelta(seconds=30)))
    async with advanced_repo.pool.connection() as c:
        await c.execute("UPDATE ai_memories SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(fresh.id,))
    assert (await repo.get(41,fresh.id)).status=="expired"
    assert await repo.relevant(41,"请举例讲解",3,2000)==[]


@pytest.mark.parametrize("value",[{"confirmed":1},{"confirmed":False},{"content":"我的邮箱是 user@example.test"},{"content":"我的密码 abc123"},{"user_id":42}])
async def test_memory_sensitive_or_unconfirmed_rejected(value):
    with pytest.raises(ValidationError):MemoryCreate(**{**memory().model_dump(),**value})


async def test_confirmed_quote_memory_cross_session_and_revocation(advanced_repo,settings):
    repo=advanced_repo
    quote="我喜欢先给结论再给示例"
    args={"memory_type":"preference","key":"answer_style","content":quote,"source_quote":quote}
    agent,llm,runtime,sid,result,row=await paused(repo,settings,"remember_learning_preference",args,"请记住："+quote)
    assert await count(repo,"ai_memories")==0
    await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="已记住你的学习偏好。")
    await agent.execute(await agent.prepare_resume(41,sid,result.run_id,"token"))
    saved=(await runtime.memories.list(41))[0]
    assert saved.source_quote==quote and saved.source_session_id==sid and saved.source_kind=="user_quote"
    other=(await repo.create(41,"new session")).id
    await agent.execute(await agent.prepare(41,other,AgentChatRequest(content="解释异步编程"),"token"))
    request=llm.complete_with_tools.await_args.args[0]
    assert any(quote in str(m.content) for m in request.messages)
    await runtime.memories.delete(41,saved.id,saved.version)
    details=await agent.sanitize_detail(41,await repo.detail(41,other,100,0),"token")
    assert details.messages[-1].redacted


async def test_skill_nodes_readonly_and_memory_boundary(advanced_repo,settings):
    repo=advanced_repo;agent,llm,runtime=advanced(repo,settings)
    sid=(await repo.create(41,"readonly")).id
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="直接回答")
    done=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="你好"),"token"))
    assert done.status=="succeeded" and await runtime.approvals.list(41,sid)==[]
    state=await agent.workflow_state(41,sid,done.run_id,"token")
    assert state["memory_count"]==0 and state["skill"] is None


async def test_actual_mcp_tool_hitl_checkpoint_and_resume(advanced_repo,settings,tmp_path):
    config=tmp_path/"servers.json"
    config.write_text(json.dumps([{"id":"demo","trusted":True,"transport":"stdio","command":sys.executable,
        "args":["-m","learntrail_agent.mcp_demo_server"],"allow_tools":{"publish_demo_note":"approval_required"}}]))
    settings=settings.model_copy(update={"mcp_servers_file":config})
    agent,llm,runtime,sid,result,row=await paused(advanced_repo,settings,"mcp_demo_publish_demo_note",{"note":"仅本地审批回执"},"请保存本地演示备注")
    assert row["tool_name"]=="mcp_demo_publish_demo_note" and row["status"]=="pending"
    await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    resumed,llm2,runtime2=advanced(advanced_repo,settings)
    llm2.complete_with_tools.side_effect=None;llm2.complete_with_tools.return_value=reply(content="已收到本地演示回执。")
    done=await resumed.execute(await resumed.prepare_resume(41,sid,result.run_id,"token"))
    assert done.status=="succeeded"
    receipt=(await runtime2.approvals.get(41,sid,row["approval_id"]))["result"]
    assert receipt["result"]["operation_id"]==str(row["approval_id"])
    assert receipt["result"]["real_content_published"] is False


async def test_memory_resource_changed_during_approval_cannot_overwrite(advanced_repo,settings):
    quote="我喜欢使用中文讲解"
    args={"memory_type":"preference","key":"answer_style","content":quote,"source_quote":quote}
    agent,llm,runtime,sid,result,row=await paused(advanced_repo,settings,"remember_learning_preference",args,"请记住："+quote)
    current=await runtime.memories.create(41,memory())
    await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    with pytest.raises(AppError) as e:
        await agent.execute(await agent.prepare_resume(41,sid,result.run_id,"token"))
    assert e.value.code=="APPROVAL_TAMPERED"
    assert (await runtime.memories.get(41,current.id)).content==current.content


async def test_fabricated_memory_quote_no_approval_or_write(advanced_repo,settings):
    agent,llm,runtime=advanced(advanced_repo,settings)
    sid=(await advanced_repo.create(41,"grounding")).id
    args={"memory_type":"preference","key":"answer_style","content":"我喜欢代码示例","source_quote":"我喜欢代码示例"}
    llm.complete_with_tools.side_effect=[reply(calls=[call("remember_learning_preference",json.dumps(args))]),reply(content="未保存未确认的信息。")]
    result=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="你好，请介绍异步"),"token"))
    assert result.status=="succeeded" and await runtime.approvals.list(41,sid)==[]
    assert await count(advanced_repo,"ai_memories")==0
    assert any("MEMORY_UNSAFE" in str(m.content) for m in llm.complete_with_tools.await_args.args[0].messages if m.role=="tool")


async def test_explicit_skill_runs_knowledge_tool_and_filters_schema(advanced_repo,settings):
    from learntrail_agent.agent.tools import Tool,ToolOutput
    from test_tool_core import EchoArgs
    agent,llm,runtime=advanced(advanced_repo,settings)
    async def read(args,ctx):return ToolOutput(data={"matches":[],"answerable":False})
    for name in ("search_knowledge_base","search_articles","get_learning_progress"):
        agent.loop.registry.register(Tool(name,"Test domain adapter",EchoArgs,read,"read"))
    sid=(await advanced_repo.create(41,"skill knowledge")).id
    llm.complete_with_tools.side_effect=[reply(calls=[call("search_knowledge_base")]),reply(content="现有知识库未找到依据。")]
    result=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="解释文章知识",skill="knowledge-learning"),"token"))
    assert result.tool_calls==1 and result.status=="succeeded"
    assert (await agent.workflow_state(41,sid,result.run_id,"token"))["skill"]["name"]=="knowledge-learning"
    definitions=llm.complete_with_tools.await_args_list[0].args[0].tools
    names={d.function.name for d in definitions}
    assert "search_knowledge_base" in names and "get_learning_progress" not in names and "echo" not in names


async def test_cancelled_and_superseded_approval_stays_denied(advanced_repo,settings):
    repo=advanced_repo;agent,llm,runtime,sid,result,row=await paused(repo,settings)
    await repo.cancel_run(41,sid,result.run_id)
    await runtime.approvals.cancel_run(41,sid,result.run_id)
    assert (await runtime.approvals.get(41,sid,row["approval_id"]))["status"]=="cancelled"
    with pytest.raises(AppError):
        await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    assert await count(repo,"ai_demo_actions")==0


async def test_capability_http_memory_confirmation_owner_and_source(advanced_repo,settings):
    import httpx
    from learntrail_agent.main import create_app
    from learntrail_agent.security.auth import AuthenticatedUser,require_user
    _,_,runtime=advanced(advanced_repo,settings)
    app=create_app(settings);app.state.advanced=runtime
    owner={"id":41}
    app.dependency_overrides[require_user]=lambda:AuthenticatedUser(owner["id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://localhost") as http:
        body=memory().model_dump(mode="json")
        assert (await http.post("/v1/memories",json={**body,"confirmed":1})).status_code==422
        assert (await http.post("/v1/memories",json={**body,"user_id":42})).status_code==422
        created=await http.post("/v1/memories",json=body)
        assert created.status_code==201
        mid=created.json()["id"]
        assert (await http.get(f"/v1/memories/{mid}/source")).json()["source_kind"]=="manual"
        owner["id"]=42
        assert (await http.get(f"/v1/memories/{mid}")).status_code==404
        assert (await http.get("/v1/memories")).json()["items"]==[]
        assert (await http.get("/v1/skills")).status_code==200


async def test_native_effect_and_receipt_rollback_together(advanced_repo,settings,monkeypatch):
    agent,llm,runtime,sid,result,row=await paused(advanced_repo,settings)
    await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    async def broken_receipt(*args):raise AppError("DATABASE_UNAVAILABLE","test receipt rollback",503)
    monkeypatch.setattr(runtime.approvals,"finish_on",broken_receipt)
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="保存失败，未形成演示草稿。")
    await agent.execute(await agent.prepare_resume(41,sid,result.run_id,"token"))
    assert await count(advanced_repo,"ai_demo_actions")==0
    assert (await runtime.approvals.get(41,sid,row["approval_id"]))["status"]=="failed"


async def test_cancel_nonidempotent_effect_is_unknown_and_never_repeated(advanced_repo,settings):
    from learntrail_agent.agent.tools import Tool,ToolOutput
    from test_tool_core import EchoArgs
    agent,llm,runtime=advanced(advanced_repo,settings)
    entered=asyncio.Event();effects=[]
    async def external(args,ctx):
        effects.append(ctx.approval_id);entered.set();await asyncio.Event().wait()
        return ToolOutput()
    agent.loop.registry.register(Tool("external_demo","Non-idempotent cancellation fixture",EchoArgs,external,"read",30,policy="approval_required"))
    sid=(await advanced_repo.create(41,"unknown result")).id
    llm.complete_with_tools.side_effect=[reply(calls=[call("external_demo")])]
    result=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="受控演示"),"token"))
    row=(await runtime.approvals.list(41,sid))[0]
    await runtime.approvals.decide(41,sid,row["approval_id"],ApprovalDecision(action="approve",expected_hash=row["arguments_hash"]))
    prepared=await agent.prepare_resume(41,sid,result.run_id,"token")
    task=asyncio.create_task(agent.execute(prepared));await asyncio.wait_for(entered.wait(),3)
    await advanced_repo.cancel_run(41,sid,result.run_id);await runtime.approvals.cancel_run(41,sid,result.run_id)
    with pytest.raises(AppError) as e:await asyncio.wait_for(task,3)
    assert e.value.code=="RUN_CANCELLED"
    assert (await runtime.approvals.get(41,sid,row["approval_id"]))["status"]=="unknown"
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="结果未知，不会重复执行。")
    await agent.execute(await agent.prepare_resume(41,sid,result.run_id,"token"))
    assert len(effects)==1
    assert any("APPROVAL_EXECUTION_UNCERTAIN" in str(m.content) for m in llm.complete_with_tools.await_args.args[0].messages if m.role=="tool")
