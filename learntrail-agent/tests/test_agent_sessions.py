"""Real PostgreSQL lifecycle tests; model and Java authorization are explicit doubles."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from learntrail_agent.agent.learntrail_tools import LearnTrailTools
from learntrail_agent.agent.schemas import AgentChatRequest
from learntrail_agent.agent.tools import ToolContext, ToolOutput
from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.errors import AppError
from learntrail_agent.services.agent import AgentService, REDACTED
from learntrail_agent.services.chat import ChatService
from learntrail_agent.settings import Settings
from learntrail_agent.storage.agent_repository import AgentRepository
from learntrail_agent.storage.repositories import SessionRepository
from test_postgres_integration import postgres_repository
from test_tool_core import registry, reply, call

pytestmark=pytest.mark.asyncio


def service(repo: SessionRepository, settings: Settings, *, user_id: int = 41,
            evidence: list[dict] | None = None) -> tuple:
    flag={"allowed":True}
    async def handler(args,ctx):return ToolOutput(data={"text":args.text},evidence=evidence or [])
    async def verify(evidence):
        if not flag["allowed"]:
            raise AppError("AGENT_SOURCE_CHANGED","source no longer readable",409)
    async def context(token,sid=None,run_id=None):
        return ToolContext(user_id,SecretStr("PRIVATE-TOKEN"),frozenset({"read"}),AsyncMock(),sid,run_id,verify)
    domain=Mock(spec=LearnTrailTools);domain.registry.return_value=registry(handler)
    domain.context=AsyncMock(side_effect=context)
    llm=AsyncMock(spec=LLMClient)
    llm.complete_with_tools.side_effect=[reply(calls=[call()]),reply(content="历史工具回答")]
    async def stream_with_tools(request):
        response=await llm.complete_with_tools(request)
        if response.content:
            yield "delta", {"content":response.content[:2]}
            await asyncio.sleep(.04)
            yield "delta", {"content":response.content[2:]}
            await asyncio.sleep(.04)
        yield "complete", response
    llm.stream_with_tools=stream_with_tools
    control=ChatService(repo,llm,settings)
    return AgentService(repo,AgentRepository(repo),domain,llm,control,settings),llm,flag


async def test_agent_run_persists_messages_usage_trace_and_excludes_plain_chat_history(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"Agent SQL test")).id
    agent,llm,_=service(repo,settings)
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="查询"),"test-token")
    result=await agent.execute(prepared)
    assert result.model_calls==2 and result.tool_calls==1 and result.usage.total_tokens==4
    detail=await repo.detail(41,sid,100,0)
    assert len(detail.messages)==2 and detail.recent_runs[0].kind=="agent"
    assert detail.recent_runs[0].status=="succeeded" and detail.recent_runs[0].usage.total_tokens==4
    assert await repo.history(41,sid,10)==[]
    assert len(await repo.agent_history(41,sid,10))==1
    trace=await agent.trace(41,sid,result.run_id,"test-token")
    assert not trace["redacted"] and trace["events"][-1]["kind"]=="run_end"
    assert [e["sequence"] for e in trace["events"]]==list(range(1,len(trace["events"])+1))
    assert "PRIVATE-TOKEN" not in json.dumps(trace,default=str)
    with pytest.raises(AppError) as caught:
        await agent.trace(42,sid,result.run_id,"test-token")
    assert caught.value.code=="SESSION_NOT_FOUND"


async def test_agent_sources_are_inherited_and_revoked_history_and_trace_are_redacted(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"source security")).id
    agent,llm,flag=service(repo,settings,evidence=[{"kind":"fixture","slug":"article-42"}])
    first=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="问题一"),"token"))
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="依赖历史的第二答")
    second_prepared=await agent.prepare(41,sid,AgentChatRequest(content="继续"),"token")
    assert second_prepared.history and second_prepared.state.evidence
    second=await agent.execute(second_prepared)
    flag["allowed"]=False
    detail=await agent.sanitize_detail(41,await repo.detail(41,sid,100,0),"token")
    assert all(m.redacted and m.content==REDACTED for m in detail.messages if m.role=="assistant")
    for result in (first,second):
        trace=await agent.trace(41,sid,result.run_id,"token")
        assert trace["redacted"] and all("payload" not in e for e in trace["events"])
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="新问题"),"token")
    assert prepared.history==[] and prepared.state.evidence==[]
    await agent.abandon(prepared)


async def test_agent_and_chat_share_busy_lock_and_never_started_stream_can_be_abandoned(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"busy")).id;agent,_,_=service(repo,settings)
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="query"),"token")
    with pytest.raises(AppError) as caught:
        await repo.start_run(41,sid,"normal chat","test",90)
    assert caught.value.code=="SESSION_BUSY"
    await agent.abandon(prepared)
    assert (await repo.get_run(41,sid,prepared.run.id)).status=="cancelled"


async def test_user_cancel_stops_model_and_does_not_commit_assistant(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"cancel")).id;agent,llm,_=service(repo,settings)
    entered=asyncio.Event();stopped=asyncio.Event()
    async def waiting(request):
        entered.set()
        try:await asyncio.Event().wait()
        finally:stopped.set()
    llm.complete_with_tools.side_effect=waiting
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="query"),"token")
    task=asyncio.create_task(agent.execute(prepared));await asyncio.wait_for(entered.wait(),2)
    await repo.cancel_run(41,sid,prepared.run.id)
    with pytest.raises(AppError) as caught:await asyncio.wait_for(task,2)
    assert caught.value.code=="RUN_CANCELLED" and stopped.is_set()
    detail=await repo.detail(41,sid,100,0)
    assert len(detail.messages)==1 and detail.messages[0].status=="failed"
    assert detail.recent_runs[0].status=="cancelled" and detail.recent_runs[0].duration_ms is not None


async def test_agent_sse_streams_tokens_before_commit_but_done_after_commit(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"SSE")).id;agent,_,_=service(repo,settings)
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="query"),"token")
    async def connected():return False
    events=[]
    async for kind,data in agent.stream(prepared,connected):
        events.append((kind,data))
        if kind=="delta":assert (await repo.get_run(41,sid,prepared.run.id)).status=="running"
        if kind=="done":assert (await repo.get_run(41,sid,prepared.run.id)).status=="succeeded"
    names=[e[0] for e in events]
    assert names[0]=="meta" and names[-1]=="done" and "tool_start" in names and "tool_end" in names
    assert names.count("delta")==2
    assert "PRIVATE-TOKEN" not in json.dumps(events,default=str)


async def test_sse_disconnect_before_worker_starts_does_not_leave_running_run(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"early close")).id;agent,_,_=service(repo,settings)
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="query"),"token")
    async def connected():return False
    stream=agent.stream(prepared,connected)
    assert (await anext(stream))[0]=="meta"
    await stream.aclose()
    assert (await repo.get_run(41,sid,prepared.run.id)).status=="cancelled"


async def test_trace_insert_failure_cannot_replace_old_evidence(postgres_repository,settings) -> None:
    repo=postgres_repository;sid=(await repo.create(41,"atomic trace")).id
    run=await repo.start_run(41,sid,"query","test",90,kind="agent");store=AgentRepository(repo)
    event={"sequence":1,"kind":"model_start","step":1,"duration_ms":0,"payload":{}}
    await store.append(41,sid,run.id,event,[{"kind":"old"}])
    with pytest.raises(AppError):await store.append(41,sid,run.id,event,[{"kind":"uncommitted"}])
    saved,events=await store.trace(41,sid,run.id)
    assert saved.agent_data["evidence"]==[{"kind":"old"}] and len(events)==1
