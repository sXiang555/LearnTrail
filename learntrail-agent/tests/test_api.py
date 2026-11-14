import asyncio
import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from pydantic import SecretStr

from learntrail_agent.settings import Settings

URL = "https://model.example.test/v1/chat/completions"
BODY = {"messages": [{"role": "user", "content": "你好"}]}
COMPLETION = {
    "id": "test-completion", "model": "test-model",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好，学途！"}, "finish_reason": "stop"}],
}
pytestmark = pytest.mark.asyncio


async def test_health_has_no_model_request(api_client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    response = await api_client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "app_name": "LearnTrail AI Test", "environment": "test", "llm_configured": True,
    }
    assert not respx_mock.calls


async def test_chat_success_and_client_reuse(
    api_client: httpx.AsyncClient, app: FastAPI, respx_mock: respx.MockRouter,
) -> None:
    usage = {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}
    route = respx_mock.post(URL).respond(200, json={**COMPLETION, "usage": usage})
    shared = app.state.http
    messages = [{"role": "system", "content": "用中文回答"}, *BODY["messages"]]
    for _ in range(2):
        response = await api_client.post("/v1/chat", json={"messages": messages, "temperature": 0, "max_tokens": 64})
        assert response.status_code == 200
        assert response.json()["content"] == "你好，学途！"
        assert response.json()["usage"] == usage
        assert app.state.http is shared
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"messages": messages, "model": "test-model", "temperature": 0, "max_tokens": 64, "stream": False, "n": 1}
    assert route.calls[0].request.headers["authorization"] == "Bearer test-only-model-key"
    assert route.call_count == 2


async def test_usage_absent_and_default_parameters(
    api_client: httpx.AsyncClient, settings: Settings, respx_mock: respx.MockRouter,
) -> None:
    settings.llm_temperature = 0.4
    settings.llm_max_tokens = 128
    route = respx_mock.post(URL).respond(200, json=COMPLETION)
    response = await api_client.post("/v1/chat", json=BODY)
    assert response.status_code == 200
    assert response.json()["usage"] is None
    sent = json.loads(route.calls[0].request.content)
    assert sent["temperature"] == 0.4 and sent["max_tokens"] == 128


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
async def test_unconfigured_model_is_not_fake_success(
    path: str, api_client: httpx.AsyncClient, settings: Settings, respx_mock: respx.MockRouter,
) -> None:
    settings.llm_api_key = SecretStr("")
    assert (await api_client.get("/v1/health")).json()["llm_configured"] is False
    response = await api_client.post(path, json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert not respx_mock.calls


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
@pytest.mark.parametrize("upstream,status,code", [
    (401, 502, "LLM_AUTH_ERROR"), (403, 502, "LLM_AUTH_ERROR"),
    (429, 429, "LLM_RATE_LIMITED"), (500, 503, "LLM_UNAVAILABLE"),
    (400, 502, "LLM_REQUEST_REJECTED"), (302, 502, "LLM_REQUEST_REJECTED"),
])
async def test_upstream_error_is_sanitized(
    path: str, upstream: int, status: int, code: str,
    api_client: httpx.AsyncClient, respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(URL).respond(upstream, text="test-only-model-key PRIVATE-UPSTREAM-BODY")
    response = await api_client.post(path, json=BODY)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "test-only-model-key" not in response.text and "PRIVATE-UPSTREAM-BODY" not in response.text


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream"])
@pytest.mark.parametrize("exception,code,status", [
    (httpx.ReadTimeout("private timeout"), "LLM_TIMEOUT", 504),
    (httpx.ConnectError("private connection"), "LLM_CONNECTION_ERROR", 502),
])
async def test_transport_error(
    path: str, exception: Exception, code: str, status: int,
    api_client: httpx.AsyncClient, respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(URL).mock(side_effect=exception)
    response = await api_client.post(path, json=BODY)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert "private" not in response.text


@pytest.mark.parametrize("payload", [
    b"not-json", b"{}", b'{"choices":[]}',
    json.dumps({**COMPLETION, "usage": {"prompt_tokens": -1}}).encode(),
    json.dumps({**COMPLETION, "choices": [{"message": {"content": None}, "finish_reason": "stop"}]}).encode(),
])
async def test_invalid_completion(
    payload: bytes, api_client: httpx.AsyncClient, respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(URL).respond(200, content=payload)
    response = await api_client.post("/v1/chat", json=BODY)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_INVALID_RESPONSE"


async def test_total_deadline(api_client: httpx.AsyncClient, settings: Settings, respx_mock: respx.MockRouter) -> None:
    settings.llm_timeout_seconds = 0.01

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=COMPLETION)

    respx_mock.post(URL).mock(side_effect=slow)
    response = await api_client.post("/v1/chat", json=BODY)
    assert response.status_code == 504 and response.json()["error"]["code"] == "LLM_TIMEOUT"


@pytest.mark.parametrize("body", [
    {"messages": []}, {"messages": [{"role": "tool", "content": "PRIVATE-MESSAGE"}]},
    {**BODY, "userId": "other-user"}, {**BODY, "max_tokens": -1},
    {"messages": [{"role": "user", "content": " "}]},
])
async def test_request_validation_does_not_echo_messages(body: dict, api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/v1/chat", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "PRIVATE-MESSAGE" not in response.text
