"""Agent HTTP boundary tests; SQL and Java/model traffic are explicit doubles."""

import asyncio
import json
from contextlib import aclosing
from unittest.mock import AsyncMock
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from learntrail_agent.errors import AppError
from learntrail_agent.services.agent import AgentService
from learntrail_agent.storage.agent_repository import AgentRepository
from test_sessions import authenticated, repository, completion


@pytest.fixture
def agent_api(app, authenticated, repository, java_key):
    repository.agent_history.return_value = []
    repository.get_run.return_value = SimpleNamespace(status="running")
    tools = app.state.agent_tools
    tools.articles.key = java_key.public_key()
    tools.learning.key = java_key.public_key()
    traces = AsyncMock(spec=AgentRepository)
    app.state.agent_service = AgentService(
        repository, traces, tools, app.state.llm, app.state.chat_service, app.state.settings,
    )
    return traces


@pytest.mark.asyncio
async def test_agent_api_direct_answer_and_signed_identity(api_client, authenticated, agent_api, repository, respx_mock):
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(200, json=completion("直接回答"))
    sid = uuid4()
    response = await api_client.post(f"/v1/sessions/{sid}/agent", headers=authenticated, json={"content": "你好"})
    assert response.status_code == 200
    assert response.json()["tool_calls"] == 0 and response.json()["model_calls"] == 1
    assert repository.start_run.await_args.args[:3] == (41, sid, "你好")
    assert repository.start_run.await_args.kwargs == {"kind": "agent"}
    assert agent_api.append.await_args.args[-2]["kind"] == "run_end"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"user_id": 99}, {"role": "admin"}, {"tools": []}, {"max_rounds": 999}])
async def test_agent_api_rejects_caller_identity_and_controls(api_client, authenticated, agent_api, repository, extra):
    response = await api_client.post(f"/v1/sessions/{uuid4()}/agent", headers=authenticated,
                                     json={"content": "query", **extra})
    assert response.status_code == 422
    repository.start_run.assert_not_called()


@pytest.mark.asyncio
async def test_agent_api_requires_authentication_and_owner(api_client, authenticated, agent_api, repository):
    sid = uuid4()
    assert (await api_client.post(f"/v1/sessions/{sid}/agent", json={"content": "query"})).status_code == 401
    repository.start_run.side_effect = AppError("SESSION_NOT_FOUND", "会话不存在。", 404)
    response = await api_client.post(f"/v1/sessions/{sid}/agent", headers=authenticated, json={"content": "query"})
    assert response.status_code == 404
    assert repository.start_run.await_args.args[0] == 41
    repository.agent_history.assert_not_called()


@pytest.mark.asyncio
async def test_agent_api_passes_controlled_tool_error_back_to_model(api_client, authenticated, agent_api, respx_mock):
    first = completion("")
    first["choices"][0]["finish_reason"] = "tool_calls"
    first["choices"][0]["message"]["tool_calls"] = [{"id": "category-call", "type": "function",
        "function": {"name": "search_articles", "arguments": '{"category":"Python"}'}}]
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=first if len(requests) == 1 else completion("当前文章接口不支持分类。"))
    respx_mock.post("https://model.example.test/v1/chat/completions").mock(side_effect=respond)
    response = await api_client.post(f"/v1/sessions/{uuid4()}/agent", headers=authenticated, json={"content": "按分类查文章"})
    assert response.status_code == 200 and response.json()["tool_calls"] == 1
    tool = requests[1]["messages"][-1]
    assert tool["role"] == "tool" and tool["tool_call_id"] == "category-call"
    assert json.loads(tool["content"])["error"]["code"] == "UNSUPPORTED_CATEGORY"


@pytest.mark.asyncio
async def test_auto_delegates_article_ranking_to_operations_and_outline_to_creator(
    api_client, authenticated, agent_api, repository, respx_mock,
):
    access = respx_mock.get("https://java.example.test/ls/studio-articles/access").respond(
        200, json={"code": 200, "data": {"operator": True, "creator": False}},
    )
    first = completion("")
    first["choices"][0]["finish_reason"] = "tool_calls"
    first["choices"][0]["message"]["tool_calls"] = [{
        "id": "ranking", "type": "function",
        "function": {"name": "search_articles", "arguments": '{"sort":"views","keyword":"事务","page_size":1}'},
    }]
    article = {"id": "42", "slug": "transactions", "title": "事务基础", "summary": "理解事务",
               "status": "published", "likedTimes": "12", "viewTimes": "123"}
    rank = respx_mock.get("https://java.example.test/ls/studio-articles/published").respond(
        200, json={"code": 200, "data": {"list": [article], "total": "1", "pages": "1"}},
    )
    respx_mock.get("https://java.example.test/ls/studio-articles/published/transactions").respond(
        200, json={"code": 200, "data": {**article, "body": "事务基础资料", "contentFormat": "text",
                                       "sourceType": "original"}},
    )
    model = respx_mock.post("https://model.example.test/v1/chat/completions").mock(side_effect=[
        httpx.Response(200, json=first),
        httpx.Response(200, json=completion("事务基础有 123 次阅读和 12 次点赞。")),
        httpx.Response(200, json=completion("大纲包含隔离级别和应用示例。")),
        httpx.Response(200, json=completion("参考《事务基础》的公开数据，建议先讲隔离级别。")),
    ])

    response = await api_client.post(
        f"/v1/sessions/{uuid4()}/agent", headers=authenticated,
        json={"assistant": "auto", "content": "查事务文章的阅读量排行，再写一份大纲"},
    )

    assert response.status_code == 200, response.text
    assert access.call_count == 1
    assert response.json()["model_calls"] == 4
    assert response.json()["citations"][0]["slug"] == "transactions"
    assert dict(rank.calls.last.request.url.params) == {
        "pageNo": "1", "pageSize": "1", "q": "事务", "sort": "views",
    }
    operations, ranked, creator, synthesis = [json.loads(item.request.content) for item in model.calls]
    assert "运营助手" in operations["messages"][0]["content"]
    assert {tool["function"]["name"] for tool in operations["tools"]} == {
        "search_articles", "search_knowledge_base",
    }
    result = json.loads(ranked["messages"][-1]["content"])["data"]
    assert (result["articles"][0]["views"], result["articles"][0]["likes"]) == (123, 12)
    assert "as_of" in result
    assert "创作者助手" in creator["messages"][0]["content"]
    assert "123 次阅读" in creator["messages"][1]["content"]
    assert "大纲包含隔离级别" in synthesis["messages"][1]["content"]
    assert "tools" not in synthesis and "tool_choice" not in synthesis
    repository.complete_run.assert_awaited_once()
    events = [item.args[-2] for item in agent_api.append.await_args_list]
    assert next(item["payload"]["specialists"] for item in events if item["kind"] == "delegation") == [
        "operations", "creator",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("assistant", "endpoint"), [
    ("operations", "/agent"), ("collaborate", "/agent/stream"), ("auto", "/agent/stream"),
])
async def test_agent_rejects_nonstaff_operations_paths_before_starting_run(
    api_client, authenticated, agent_api, repository, respx_mock, assistant, endpoint,
):
    access = respx_mock.get("https://java.example.test/ls/studio-articles/access").respond(
        200, json={"code": 200, "data": {"operator": False, "creator": True}},
    )
    model = respx_mock.post("https://model.example.test/v1/chat/completions")

    response = await api_client.post(f"/v1/sessions/{uuid4()}{endpoint}", headers=authenticated,
                                     json={"assistant": assistant, "content": "查文章的阅读量排行，再写一份大纲"})

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "OPERATIONS_FORBIDDEN"
    assert access.call_count == 1
    repository.start_run.assert_not_awaited()
    assert not model.called


@pytest.mark.asyncio
async def test_creator_cannot_execute_a_learning_tool_even_if_model_requests_it(
    api_client, authenticated, agent_api, respx_mock,
):
    first = completion("")
    first["choices"][0]["finish_reason"] = "tool_calls"
    first["choices"][0]["message"]["tool_calls"] = [{
        "id": "wrong-tool", "type": "function",
        "function": {"name": "get_learning_progress", "arguments": "{}"},
    }]
    model = respx_mock.post("https://model.example.test/v1/chat/completions").mock(side_effect=[
        httpx.Response(200, json=first), httpx.Response(200, json=completion("这是润色后的段落。")),
    ])
    no_progress = respx_mock.get("https://java.example.test/ls/lessons/page")

    response = await api_client.post(f"/v1/sessions/{uuid4()}/agent", headers=authenticated,
                                     json={"assistant": "creator", "content": "润色这段话：我在学习事务。"})

    assert response.status_code == 200, response.text
    assert not no_progress.called
    result = json.loads(model.calls.last.request.content)["messages"][-1]["content"]
    assert json.loads(result)["error"]["code"] == "TOOL_FORBIDDEN"


@pytest.mark.asyncio
async def test_agent_sse_model_failure_is_error_without_success_commit(api_client, authenticated, agent_api, repository, respx_mock):
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(429, json={"error": "PRIVATE-UPSTREAM"})
    response = await api_client.post(f"/v1/sessions/{uuid4()}/agent/stream", headers=authenticated, json={"content": "query"})
    assert response.status_code == 200 and "event: error" in response.text
    assert "LLM_RATE_LIMITED" in response.text
    assert "event: done" not in response.text and "PRIVATE-UPSTREAM" not in response.text
    repository.complete_run.assert_not_called()
    assert repository.fail_run.await_args.args[-1] == "LLM_RATE_LIMITED"


@pytest.mark.asyncio
async def test_agent_sse_delivers_and_stores_entire_buffered_model_reply(
    api_client, authenticated, agent_api, repository, respx_mock,
):
    chunks = [str(index) for index in range(320)]
    choices = [{"delta": {"content": chunk}, "finish_reason": None} for chunk in chunks]
    choices.append({"delta": {}, "finish_reason": "stop"})
    wire = "".join(
        "data: " + json.dumps({"id": "burst", "model": "test-model", "choices": [
            {"index": 0, **choice},
        ]}) + "\n\n"
        for choice in choices
    ) + "data: [DONE]\n\n"
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(
        200, text=wire, headers={"content-type": "text/event-stream"},
    )

    response = await api_client.post(
        f"/v1/sessions/{uuid4()}/agent/stream", headers=authenticated, json={"content": "query"},
    )

    frames = response.text.strip().split("\n\n")
    deltas = [json.loads(frame.split("\ndata: ", 1)[1])["content"] for frame in frames if frame.startswith("event: delta\n")]
    assert response.status_code == 200 and deltas == chunks
    assert frames[-1].startswith("event: done\n")
    repository.complete_run.assert_awaited_once()
    assert repository.complete_run.await_args.args[3].content == "".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_full_agent_sse_queue_waits_and_can_resume_or_close(disconnect):
    capacity_reached = asyncio.Event()
    finished = asyncio.Event()
    sent = 0
    chunks = [str(index) for index in range(320)]
    run = SimpleNamespace(id=uuid4(), user_message_id=uuid4())
    prepared = SimpleNamespace(run=run, context=SimpleNamespace(session_id=uuid4()), resume=False)

    async def execute(prepared, emit, disconnected):
        nonlocal sent
        try:
            for chunk in chunks:
                if sent == 256:
                    capacity_reached.set()
                await emit("delta", {"content": chunk})
                sent += 1
            return SimpleNamespace(run_id=run.id, assistant_message_id=uuid4(), status="succeeded",
                model="test-model", usage=None, usage_complete=False, model_calls=1, tool_calls=0,
                citations=[], checkpoint=None, duration_ms=1)
        finally:
            finished.set()

    service = SimpleNamespace(execute=execute, abandon=AsyncMock())
    async with asyncio.timeout(3):
        async with aclosing(AgentService.stream(service, prepared, AsyncMock(return_value=False))) as stream:
            assert (await anext(stream))[0] == "meta"
            await capacity_reached.wait()
            assert sent == 256 and not finished.is_set()
            if not disconnect:
                events = [event async for event in stream]
                assert [data["content"] for kind, data in events if kind == "delta"] == chunks
                assert events[-1][0] == "done"
    assert sent == (256 if disconnect else len(chunks))
    assert finished.is_set()
    service.abandon.assert_awaited_once_with(prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"thread_id":"other-user"},{"checkpoint_id":"old-version"},
    {"checkpoint_ns":"other-graph"},{"user_id":99}])
async def test_resume_rejects_client_graph_identifiers(api_client,authenticated,agent_api,body):
    response=await api_client.post(f"/v1/sessions/{uuid4()}/agent/runs/{uuid4()}/resume",
                                    headers=authenticated,json=body)
    assert response.status_code==422
    agent_api.trace.assert_not_called()


@pytest.mark.asyncio
async def test_graph_state_and_resume_keep_signed_user_at_owner_boundary(api_client,authenticated,agent_api,
                                                                       token_factory,respx_mock):
    respx_mock.get("https://java.example.test/us/users/42/enabled").respond(200,json={"code":200,"data":True})
    agent_api.trace.side_effect=AppError("SESSION_NOT_FOUND","会话不存在。",404)
    headers={"Authorization":"Bearer "+token_factory(42),"userId":"41","role":"admin"}
    path=f"/v1/sessions/{uuid4()}/agent/runs/{uuid4()}"
    assert (await api_client.get(path+"/state",headers=headers)).status_code==404
    assert (await api_client.post(path+"/resume",headers=headers,json={})).status_code==404
    assert all(call.args[0]==42 for call in agent_api.trace.await_args_list)


@pytest.mark.asyncio
async def test_truncated_agent_tokens_are_provisional_and_cannot_be_committed(api_client,authenticated,agent_api,repository,respx_mock):
    wire='data: '+json.dumps({"id":"partial","model":"test-model","choices":[{
        "index":0,"delta":{"content":"不能当作完成答案"},"finish_reason":None}]},ensure_ascii=False)+'\n\n'
    wire+='data: '+json.dumps({"id":"partial","model":"test-model","choices":[{
        "index":0,"delta":{},"finish_reason":"length"}]})+'\n\ndata: [DONE]\n\n'
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(200,text=wire,headers={"content-type":"text/event-stream"})
    response=await api_client.post(f"/v1/sessions/{uuid4()}/agent/stream",headers=authenticated,json={"content":"query"})
    assert "event: delta" in response.text and "AGENT_INCOMPLETE_ANSWER" in response.text
    assert '"discard_answer": true' in response.text and "event: done" not in response.text
    repository.complete_run.assert_not_called()
