"""API/service tests with mocked storage and model HTTP, not SQL integration."""

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from learntrail_agent.errors import AppError
from learntrail_agent.schemas import SessionChatRequest, SessionDetail, SessionInfo, SessionList, Usage
from learntrail_agent.services.chat import ChatService, build_context
from learntrail_agent.settings import Settings
from learntrail_agent.storage.repositories import SessionRepository, StartedRun


@pytest.fixture
def repository() -> AsyncMock:
    repository = AsyncMock(spec=SessionRepository)
    repository.start_run.return_value = StartedRun(uuid4(), uuid4())
    repository.history.return_value = []
    repository.complete_run.return_value = uuid4()
    return repository


@pytest.fixture
def authenticated(
    api_client: httpx.AsyncClient, app: FastAPI, java_key: rsa.RSAPrivateKey,
    token_factory: Callable[..., str], respx_mock: respx.MockRouter, repository: AsyncMock,
) -> dict[str, str]:
    app.state.jwt_public_key = java_key.public_key()
    app.state.sessions = repository
    app.state.chat_service = ChatService(repository, app.state.llm, app.state.settings)
    respx_mock.get("https://java.example.test/us/users/41/enabled").respond(200, json={"code": 200, "data": True})
    return {"Authorization": f"Bearer {token_factory()}"}


def completion(content: str = "第一轮回答", finish: str = "stop") -> dict[str, object]:
    return {"id": "test-completion", "model": "test-model", "choices": [
        {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish},
    ], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


@pytest.mark.asyncio
async def test_session_create_list_and_detail(
    api_client: httpx.AsyncClient, authenticated: dict[str, str], repository: AsyncMock,
) -> None:
    now = datetime.now(timezone.utc)
    session = SessionInfo(id=uuid4(), title="测试", created_at=now, updated_at=now)
    repository.create.return_value = session
    repository.list.return_value = SessionList(items=[session], total=1, limit=20, offset=0)
    repository.detail.return_value = SessionDetail(
        **session.model_dump(), messages=[], total_messages=0, message_limit=100, message_offset=0, recent_runs=[],
    )
    created = await api_client.post("/v1/sessions", headers=authenticated, json={"title": " 测试 "})
    assert created.status_code == 201
    repository.create.assert_awaited_once_with(41, "测试")
    listed = await api_client.get("/v1/sessions", headers=authenticated)
    assert listed.json()["items"][0]["id"] == str(session.id)
    repository.list.assert_awaited_once_with(41, 20, 0)
    detail = await api_client.get(f"/v1/sessions/{session.id}", headers=authenticated)
    assert detail.status_code == 200 and detail.json()["messages"] == []
    repository.detail.assert_awaited_once_with(41, session.id, 100, 0)


@pytest.mark.asyncio
async def test_different_user_never_uses_owner_from_browser(
    api_client: httpx.AsyncClient, authenticated: dict[str, str], token_factory: Callable[..., str],
    repository: AsyncMock, respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get("https://java.example.test/us/users/42/enabled").respond(200, json={"code": 200, "data": True})
    repository.detail.side_effect = AppError("SESSION_NOT_FOUND", "会话不存在。", 404)
    repository.start_run.side_effect = AppError("SESSION_NOT_FOUND", "会话不存在。", 404)
    session_id = uuid4()
    headers = {"Authorization": f"Bearer {token_factory(42)}", "user-info": "41", "userId": "41", "role": "admin"}
    assert (await api_client.get(f"/v1/sessions/{session_id}", headers=headers)).status_code == 404
    assert (await api_client.post(f"/v1/sessions/{session_id}/chat", headers=headers, json={"content": "问题"})).status_code == 404
    assert repository.detail.await_args.args[0] == 42
    assert repository.start_run.await_args.args[0] == 42
    assert not repository.history.called


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"title": "test", "userId": 99}, {"title": "test", "role": "admin"}])
async def test_body_cannot_set_identity(
    api_client: httpx.AsyncClient, authenticated: dict[str, str], repository: AsyncMock, body: dict[str, object],
) -> None:
    assert (await api_client.post("/v1/sessions", headers=authenticated, json=body)).status_code == 422
    repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_two_rounds_use_server_history(
    api_client: httpx.AsyncClient, authenticated: dict[str, str], repository: AsyncMock, respx_mock: respx.MockRouter,
) -> None:
    repository.history.side_effect = [[], [("第一问", "第一轮回答")]]
    model = respx_mock.post("https://model.example.test/v1/chat/completions").respond(200, json=completion())
    session_id = uuid4()
    for question in ("第一问", "继续解释"):
        response = await api_client.post(f"/v1/sessions/{session_id}/chat", headers=authenticated, json={"content": question})
        assert response.status_code == 200
        assert response.json()["usage"]["total_tokens"] == 15
        assert response.json()["run_id"] == str(repository.start_run.return_value.id)
    second = json.loads(model.calls[1].request.content)
    assert second["messages"] == [
        {"role": "user", "content": "第一问"}, {"role": "assistant", "content": "第一轮回答"},
        {"role": "user", "content": "继续解释"},
    ]
    assert repository.complete_run.await_count == 2
    repository.fail_run.assert_not_called()


def test_context_sliding_window_keeps_whole_recent_turns(settings: Settings) -> None:
    settings.context_max_turns = 1
    assert [m.content for m in build_context([("旧问", "旧答"), ("新问", "新答")], "追问", settings)] == ["新问", "新答", "追问"]
    settings.context_max_turns = 10
    settings.context_max_chars = 20000
    assert [m.content for m in build_context([("旧问" * 10000, "旧答"), ("新问", "新答")], "追问", settings)] == ["新问", "新答", "追问"]
    assert [m.content for m in build_context([("问", "答" * 20001)], "追问", settings)] == ["追问"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,code", [
    ("rate_limit", "LLM_RATE_LIMITED"), ("empty", "LLM_INCOMPLETE_RESPONSE"), ("truncated", "LLM_INCOMPLETE_RESPONSE"),
])
async def test_failed_model_run_is_not_successful_message(
    api_client: httpx.AsyncClient, authenticated: dict[str, str], repository: AsyncMock,
    respx_mock: respx.MockRouter, scenario: str, code: str,
) -> None:
    model = respx_mock.post("https://model.example.test/v1/chat/completions")
    model.respond(429, json={"error": "test-only-secret"}) if scenario == "rate_limit" else model.respond(
        200, json=completion("" if scenario == "empty" else "部分内容", "length" if scenario == "truncated" else "stop"),
    )
    session_id = uuid4()
    response = await api_client.post(f"/v1/sessions/{session_id}/chat", headers=authenticated, json={"content": "问题"})
    assert response.json()["error"]["code"] == code
    repository.complete_run.assert_not_called()
    expected_usage = None if scenario == "rate_limit" else Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    repository.fail_run.assert_awaited_once_with(
        41, session_id, repository.start_run.return_value.id, code, duration_ms=ANY, usage=expected_usage,
    )
    assert repository.fail_run.await_args.kwargs["duration_ms"] >= 0
    assert "test-only-secret" not in response.text


@pytest.mark.asyncio
async def test_database_failure_does_not_return_model_success(
    api_client: httpx.AsyncClient, authenticated: dict[str, str], repository: AsyncMock, respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post("https://model.example.test/v1/chat/completions").respond(200, json=completion())
    repository.complete_run.side_effect = AppError("DATABASE_UNAVAILABLE", "存储失败", 503)
    repository.fail_run.side_effect = AppError("DATABASE_UNAVAILABLE", "存储失败", 503)
    response = await api_client.post(f"/v1/sessions/{uuid4()}/chat", headers=authenticated, json={"content": "问题"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUN_STATE_UNCERTAIN"
    assert "第一轮回答" not in response.text
    assert repository.fail_run.await_args.kwargs["usage"].total_tokens == 15
    assert repository.fail_run.await_args.kwargs["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_cancellation_marks_run_cancelled(repository: AsyncMock, settings: Settings) -> None:
    llm = AsyncMock()
    entered = asyncio.Event()
    async def wait_for_cancel(request: object) -> None:
        entered.set()
        await asyncio.Event().wait()
    llm.complete.side_effect = wait_for_cancel
    service = ChatService(repository, llm, settings)
    session_id = uuid4()
    task = asyncio.create_task(service.chat(41, session_id, SessionChatRequest(content="问题")))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    repository.fail_run.assert_awaited_once_with(
        41, session_id, repository.start_run.return_value.id, "RUN_CANCELLED", status="cancelled",
        duration_ms=ANY, usage=None,
    )
    assert repository.fail_run.await_args.kwargs["duration_ms"] >= 0
    repository.complete_run.assert_not_called()
