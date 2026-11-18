"""Graph reducers and real PostgreSQL checkpoints; model and Java are doubles."""

from pathlib import Path
import asyncio
import json
from types import SimpleNamespace
from typing import Annotated, TypedDict
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr
from cryptography.exceptions import InvalidTag
from psycopg.types.json import Jsonb
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from learntrail_agent.agent.loop import AgentLoop, AgentState
from learntrail_agent.agent.schemas import AgentChatRequest
from learntrail_agent.agent.tools import Tool, ToolContext, ToolExecutor
from learntrail_agent.agent.tools import ToolOutput
from learntrail_agent.errors import AppError
from learntrail_agent.storage.checkpoints import CheckpointCipher, SessionCheckpointer, thread_id, load_checkpoint_key
from test_tool_core import EchoArgs, call, context, echo, registry, reply
from test_agent_sessions import service
from test_postgres_integration import postgres_repository


def test_add_messages_replaces_same_id_and_appends_distinct_id() -> None:
    initial=[HumanMessage(id="one",content="old")]
    changed=add_messages(initial,[HumanMessage(id="one",content="edited"),AIMessage(id="two",content="reply")])
    assert len(changed)==2 and [m.content for m in changed]==["edited","reply"]
    assert initial[0].content=="old"


@pytest.mark.asyncio
async def test_parallel_graph_nodes_merge_message_updates() -> None:
    class State(TypedDict):
        messages: Annotated[list,add_messages]
    async def one(state):return {"messages":[AIMessage(id="a",content="a")]}
    async def two(state):return {"messages":[AIMessage(id="b",content="b")]}
    graph=StateGraph(State)
    graph.add_node("one",one);graph.add_node("two",two)
    graph.add_edge(START,"one");graph.add_edge(START,"two")
    graph.add_edge("one",END);graph.add_edge("two",END)
    result=await graph.compile().ainvoke({"messages":[HumanMessage(id="u",content="q")]})
    assert {m.id for m in result["messages"]}=={"u","a","b"}


@pytest_asyncio.fixture
async def graph_repository(postgres_repository):
    async with postgres_repository.pool.connection() as conn:
        await conn.execute((Path(__file__).parents[1]/"migrations/V005__graph_checkpoints.sql").read_text(),prepare=False)
    return postgres_repository


def saver(repo):
    return AsyncPostgresSaver(repo.pool,serde=CheckpointCipher(b"x"*32))


@pytest.mark.asyncio
async def test_real_postgres_pause_new_service_resume_and_encrypted_private_protocol(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"checkpoint")).id
    first,llm,_=service(repo,settings);first.checkpointer=saver(repo)
    thinking=reply(calls=[call()]);thinking.reasoning_content=SecretStr("PRIVATE-PROVIDER-REASONING")
    llm.complete_with_tools.side_effect=[thinking,reply(content="恢复后的回答")]
    prepared=await first.prepare(41,sid,AgentChatRequest(content="query",pause_before_tools=True),"token")
    paused=await first.execute(prepared)
    assert paused.status=="paused" and paused.assistant_message_id is None
    assert (await repo.get_run(41,sid,paused.run_id)).status=="paused"
    snapshot=await first.workflow_state(41,sid,paused.run_id,"token")
    assert snapshot["thread_id"]==thread_id(sid) and snapshot["checkpoint_ns"]==""
    assert snapshot["checkpoint_id"]!=snapshot["thread_id"] and snapshot["next"]==["gate"]
    assert "PRIVATE-PROVIDER-REASONING" not in str(snapshot)
    async with repo.pool.connection() as conn:
        rows=await (await conn.execute("SELECT blob FROM checkpoint_blobs WHERE blob IS NOT NULL")).fetchall()
        assert rows and all(b"PRIVATE-PROVIDER-REASONING" not in bytes(row["blob"]) for row in rows)
    # New service/saver object represents a restarted Python worker with the same key.
    second,llm2,_=service(repo,settings);second.checkpointer=saver(repo)
    llm2.complete_with_tools.side_effect=None;llm2.complete_with_tools.return_value=reply(content="恢复后的回答")
    resumed=await second.prepare_resume(41,sid,paused.run_id,"token")
    result=await second.execute(resumed)
    assert result.status=="succeeded" and result.run_id==paused.run_id
    assert result.model_calls==2 and result.tool_calls==1 and result.usage.total_tokens==4
    request=llm2.complete_with_tools.await_args.args[0]
    assert request.messages[2].reasoning_content.get_secret_value()=="PRIVATE-PROVIDER-REASONING"
    detail=await repo.detail(41,sid,100,0)
    assert len(detail.messages)==2 and detail.messages[-1].content=="恢复后的回答"


@pytest.mark.asyncio
async def test_postgres_resumes_collaboration_and_streams_only_synthesis(graph_repository, settings) -> None:
    repo = graph_repository
    session = await repo.create(41, "协作恢复")
    articles = Tool("search_articles", "Test article search", EchoArgs, echo, "read")
    operator_access = SimpleNamespace(can_operate=AsyncMock(return_value=True))
    first, initial_model, _ = service(repo, settings)
    first.tools.articles = operator_access
    first.checkpointer = saver(repo)
    first.loop.registry.register(articles)
    initial_model.complete_with_tools.side_effect = [reply(calls=[call("search_articles")])]
    prepared = await first.prepare(41, session.id, AgentChatRequest(
        content="先做内容盘点，再写大纲", assistant="collaborate", pause_before_tools=True,
    ), "token")
    paused = await first.execute(prepared)
    assert paused.status == "paused"

    restarted, model, _ = service(repo, settings)
    restarted.tools.articles = operator_access
    restarted.checkpointer = saver(repo)
    restarted.loop.registry.register(articles)
    model.complete_with_tools.side_effect = [
        reply(content="运营盘点报告"), reply(content="创作者内部草案"), reply(content="汇总后的最终答复"),
    ]
    operator_access.can_operate.return_value = False
    for action in (restarted.prepare_resume, restarted.workflow_state, restarted.trace):
        with pytest.raises(AppError) as denied:
            await action(41, session.id, paused.run_id, "token")
        assert denied.value.code == "OPERATIONS_FORBIDDEN"
    model.complete_with_tools.assert_not_awaited()
    operator_access.can_operate.return_value = True
    restored = await restarted.prepare_resume(41, session.id, paused.run_id, "token")
    events = [event async for event in restarted.stream(restored, AsyncMock(return_value=False))]

    assert "".join(data["content"] for kind, data in events if kind == "delta") == "汇总后的最终答复"
    assert events[-1][0] == "done"
    assert events[-1][1]["model_calls"] == 4
    assert events[-1][1]["tool_calls"] == 1
    assert events[-1][1]["usage"]["total_tokens"] == 8
    graph = await restarted.workflow_state(41, session.id, paused.run_id, "token")
    assert graph["specialists"] == ["operations", "creator"]
    detail = await repo.detail(41, session.id, 100, 0)
    assert len(detail.messages) == 2 and detail.messages[-1].content == "汇总后的最终答复"
    operator_access.can_operate.return_value = False
    visible = await restarted.sanitize_detail(41, detail, "token")
    assert visible.messages[-1].redacted
    assert "汇总后的最终答复" not in visible.messages[-1].content


@pytest.mark.asyncio
async def test_checkpoint_owner_thread_and_namespace_isolation(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"owner")).id;other=(await repo.create(41,"other thread")).id
    base=saver(repo);guard=SessionCheckpointer(base,repo,41,sid)
    with pytest.raises(AppError) as caught:
        await guard.aget_tuple({"configurable":{"thread_id":thread_id(other),"checkpoint_ns":""}})
    assert caught.value.code=="CHECKPOINT_FORBIDDEN"
    with pytest.raises(AppError) as caught:
        await guard.aget_tuple({"configurable":{"thread_id":thread_id(sid),"checkpoint_ns":"other"}})
    assert caught.value.code=="CHECKPOINT_FORBIDDEN"
    with pytest.raises(AppError) as caught:
        await SessionCheckpointer(base,repo,42,sid).aget_tuple({"configurable":{"thread_id":thread_id(sid),"checkpoint_ns":""}})
    assert caught.value.code=="SESSION_NOT_FOUND"
    assert await SessionCheckpointer(base,repo,41,other).aget_tuple({"configurable":{"thread_id":thread_id(other),"checkpoint_ns":""}}) is None


@pytest.mark.asyncio
async def test_cannot_resume_completed_superseded_or_other_users_run(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"resume guards")).id
    agent,llm,_=service(repo,settings);agent.checkpointer=saver(repo)
    first=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="q"),"token"))
    with pytest.raises(AppError) as caught:
        await agent.prepare_resume(42,sid,first.run_id,"token")
    assert caught.value.code=="SESSION_NOT_FOUND"
    with pytest.raises(AppError) as caught:
        await agent.prepare_resume(41,sid,first.run_id,"token")
    assert caught.value.code=="GRAPH_ALREADY_COMPLETED"
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="next")
    await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="next"),"token"))
    with pytest.raises(AppError) as caught:
        await agent.prepare_resume(41,sid,first.run_id,"token")
    assert caught.value.code=="GRAPH_RUN_SUPERSEDED"


@pytest.mark.asyncio
async def test_revoked_checkpoint_is_redacted_and_cannot_resume(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"revocation")).id
    agent,llm,flag=service(repo,settings,evidence=[{"kind":"fixture"}]);agent.checkpointer=saver(repo)
    # Pause the next tool round so the first round's evidence is already checkpointed.
    from learntrail_agent.storage.checkpoints import SessionCheckpointer
    initial=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="q"),"token"))
    flag["allowed"]=False
    projected=await agent.workflow_state(41,sid,initial.run_id,"token")
    assert projected["redacted"] and projected["messages"]==[]
    with pytest.raises(AppError) as caught:
        await agent.prepare_resume(41,sid,initial.run_id,"token")
    assert caught.value.code=="AGENT_SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_tool_cancellation_is_checkpointed_and_read_only_node_can_resume(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"cancel and resume")).id
    agent,llm,_=service(repo,settings);agent.checkpointer=saver(repo)
    entered=asyncio.Event();stopped=asyncio.Event();executions=0
    async def handler(args,ctx):
        nonlocal executions
        executions+=1
        if executions==1:
            entered.set()
            try:await asyncio.Event().wait()
            finally:stopped.set()
        return ToolOutput(data={"text":"resumed"})
    tools=registry(handler)
    agent.loop.registry=tools;agent.loop.executor=ToolExecutor(tools)
    prepared=await agent.prepare(41,sid,AgentChatRequest(content="query"),"token")
    task=asyncio.create_task(agent.execute(prepared));await asyncio.wait_for(entered.wait(),2)
    await repo.cancel_run(41,sid,prepared.run.id)
    with pytest.raises(AppError) as caught:await asyncio.wait_for(task,3)
    assert caught.value.code=="RUN_CANCELLED" and stopped.is_set()
    detail=await repo.detail(41,sid,100,0)
    assert len(detail.messages)==1
    resumed=await agent.prepare_resume(41,sid,prepared.run.id,"token")
    result=await agent.execute(resumed)
    assert result.status=="succeeded" and executions==2 and result.tool_calls==2
    assert len((await repo.detail(41,sid,100,0)).messages)==2


@pytest.mark.asyncio
async def test_resume_does_not_reset_model_round_budget(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"bounded resume")).id
    agent,llm,_=service(repo,settings);agent.checkpointer=saver(repo);agent.loop.max_rounds=1
    first=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="query",pause_before_tools=True),"token"))
    with pytest.raises(AppError) as caught:
        await agent.execute(await agent.prepare_resume(41,sid,first.run_id,"token"))
    assert caught.value.code=="AGENT_ROUND_LIMIT"
    assert llm.complete_with_tools.await_count==1


@pytest.mark.asyncio
async def test_thread_memory_is_bounded_and_distinct_sessions_do_not_share_messages(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"short memory")).id
    other=(await repo.create(41,"isolated memory")).id
    agent,llm,_=service(repo,settings);agent.checkpointer=saver(repo)
    settings.context_max_turns=1
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="reply")
    for text in ("old-1","recent-2","current-3"):
        result=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content=text),"token"))
    sent=llm.complete_with_tools.await_args.args[0].messages[-1].content
    assert json.loads(sent)["history"]==[{"question":"recent-2","answer":"reply"}]
    snap=await agent.workflow_state(41,sid,result.run_id,"token")
    assert len(snap["messages"])==2
    isolated=await agent.execute(await agent.prepare(41,other,AgentChatRequest(content="isolated-4"),"token"))
    assert llm.complete_with_tools.await_args.args[0].messages[-1].content=="isolated-4"
    assert (await agent.workflow_state(41,other,isolated.run_id,"token"))["checkpoint_id"]!=snap["checkpoint_id"]


@pytest.mark.asyncio
async def test_followup_inherits_verified_citations_and_revocation_clears_them(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"citation memory")).id
    agent,llm,flag=service(repo,settings,evidence=[{"kind":"fixture"}]);agent.checkpointer=saver(repo)
    first=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="first"),"token"))
    citation={"slug":"article-fixture","title":"Mock Java authorized source","quote":"quoted evidence"}
    # Explicit fixture metadata: the Java permission verifier is a double here.
    async with repo.pool.connection() as conn:
        await conn.execute("UPDATE ai_runs SET agent_data=jsonb_set(agent_data,'{citations}',%s) WHERE id=%s",
                           (Jsonb([citation]),first.run_id))
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="followup from history")
    second=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="followup"),"token"))
    assert second.tool_calls==0 and second.citations==[citation]
    detail=await repo.detail(41,sid,100,0)
    assert detail.messages[-1].citations==[citation]
    flag["allowed"]=False
    sanitized=await agent.sanitize_detail(41,detail,"token")
    assert all(not m.citations for m in sanitized.messages if m.role=="assistant")


def test_cipher_integrity_and_private_key_file_are_fail_closed(tmp_path) -> None:
    path=tmp_path/"private/checkpoint.key"
    key=load_checkpoint_key(path)
    assert key==load_checkpoint_key(path) and path.stat().st_mode & 0o077==0
    cipher=CheckpointCipher(key);label,blob=cipher.dumps_typed([HumanMessage(id="u",content="private content")])
    assert b"private content" not in blob
    with pytest.raises(InvalidTag):
        cipher.loads_typed((label,blob[:-1]+bytes([blob[-1]^1])))
    path.write_bytes(b"invalid")
    with pytest.raises(RuntimeError):load_checkpoint_key(path)
    assert path.read_bytes()==b"invalid"  # Never silently rotate a broken existing key.


@pytest.mark.asyncio
async def test_new_question_supersedes_a_paused_run_without_executing_old_tools(graph_repository,settings) -> None:
    repo=graph_repository;sid=(await repo.create(41,"replace paused workflow")).id
    agent,llm,_=service(repo,settings);agent.checkpointer=saver(repo)
    paused=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="old",pause_before_tools=True),"token"))
    llm.complete_with_tools.side_effect=None;llm.complete_with_tools.return_value=reply(content="new answer")
    new=await agent.execute(await agent.prepare(41,sid,AgentChatRequest(content="new question"),"token"))
    assert new.run_id!=paused.run_id and new.tool_calls==0 and new.content=="new answer"
    with pytest.raises(AppError) as caught:
        await agent.prepare_resume(41,sid,paused.run_id,"token")
    assert caught.value.code=="GRAPH_RUN_SUPERSEDED"
