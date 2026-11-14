"""Agent HTTP boundary tests; SQL and Java/model traffic are explicit doubles."""

import json
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
async def test_agent_sse_model_failure_is_error_without_success_commit(api_client, authenticated, agent_api, repository, respx_mock):
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(429, json={"error": "PRIVATE-UPSTREAM"})
    response = await api_client.post(f"/v1/sessions/{uuid4()}/agent/stream", headers=authenticated, json={"content": "query"})
    assert response.status_code == 200 and "event: error" in response.text
    assert "LLM_RATE_LIMITED" in response.text
    assert "event: done" not in response.text and "PRIVATE-UPSTREAM" not in response.text
    repository.complete_run.assert_not_called()
    assert repository.fail_run.await_args.args[-1] == "LLM_RATE_LIMITED"


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
