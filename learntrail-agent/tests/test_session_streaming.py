"""Session SSE tests use real HTTP parsing with mocked Java/model/storage."""

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import respx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from starlette.requests import ClientDisconnect

from learntrail_agent.api_sessions import SessionStreamResponse
from learntrail_agent.errors import AppError
from learntrail_agent.schemas import RunInfo, SessionChatRequest
from learntrail_agent.services.chat import ChatService
from learntrail_agent.storage.repositories import SessionRepository, StartedRun
from test_streaming import Packets, decode_events, frame

URL = "https://model.example.test/v1/chat/completions"
pytestmark = pytest.mark.asyncio


@pytest.fixture
def stream_setup(
    api_client: httpx.AsyncClient, app: FastAPI, java_key: RSAPrivateKey,
    token_factory: Callable[..., str], respx_mock: respx.MockRouter,
) -> tuple[AsyncMock, dict[str, str]]:
    repository = AsyncMock(spec=SessionRepository)
    repository.start_run.return_value = StartedRun(uuid4(), uuid4())
    repository.history.return_value = [("第一问", "第一答")]
    repository.get_run.return_value = SimpleNamespace(status="running")
    repository.complete_run.return_value = uuid4()
    app.state.jwt_public_key = java_key.public_key()
    app.state.sessions = repository
    app.state.chat_service = ChatService(repository, app.state.llm, app.state.settings)
    respx_mock.get("https://java.example.test/us/users/41/enabled").respond(200, json={"code": 200, "data": True})
    return repository, {"Authorization": f"Bearer {token_factory()}"}


async def test_session_sse_commits_before_done(
    api_client: httpx.AsyncClient, stream_setup: tuple, respx_mock: respx.MockRouter,
) -> None:
    repo, headers = stream_setup
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    first = {"id": "provider-stream-test", "model": "provider-model", "choices": [
        {"index": 0, "delta": {"content": "你好，学途"}, "finish_reason": None},
    ]}
    wire = ("data: " + json.dumps(first, ensure_ascii=False) + "\n\n").encode() + frame(finish="stop")
    wire += ("data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n").encode() + b"data: [DONE]\n\n"
    packets = Packets([bytes([byte]) for byte in wire])
    route = respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    response = await api_client.post(f"/v1/sessions/{uuid4()}/chat/stream", headers=headers, json={"content": "继续"})
    assert response.status_code == 200
    events = decode_events(response.text)
    assert events[0][0] == "meta" and events[-1][0] == "done"
    assert "".join(data["content"] for event, data in events if event == "delta") == "你好，学途"
    assert events[-1][1]["usage"] == usage
    assert events[-1][1]["duration_ms"] >= 0
    reply = repo.complete_run.await_args.args[3]
    assert reply.id == "provider-stream-test" and reply.model == "provider-model"
    assert reply.usage.total_tokens == 15
    assert json.loads(route.calls[0].request.content)["messages"] == [
        {"role": "user", "content": "第一问"}, {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "继续"},
    ]
    repo.fail_run.assert_not_called()
    assert packets.closed


@pytest.mark.parametrize("scenario,code", [
    ("broken", "LLM_STREAM_INTERRUPTED"), ("length", "LLM_INCOMPLETE_RESPONSE"),
    ("401", "LLM_AUTH_ERROR"), ("database", "DATABASE_UNAVAILABLE"),
])
async def test_session_stream_never_reports_failed_generation_as_done(
    api_client: httpx.AsyncClient, stream_setup: tuple, respx_mock: respx.MockRouter, scenario: str, code: str,
) -> None:
    repo, headers = stream_setup
    if scenario == "database":
        repo.complete_run.side_effect = AppError("DATABASE_UNAVAILABLE", "存储暂不可用", 503)
    wire = frame("部分回答")
    if scenario != "broken":
        wire += frame(finish="length" if scenario == "length" else "stop") + b"data: [DONE]\n\n"
    respx_mock.post(URL).respond(
        401 if scenario == "401" else 200, headers={"content-type": "text/event-stream"},
        content=b"test-only-sensitive" if scenario == "401" else wire,
    )
    response = await api_client.post(f"/v1/sessions/{uuid4()}/chat/stream", headers=headers, json={"content": "问题"})
    events = decode_events(response.text)
    assert events[-1][0] == "error" and events[-1][1]["error"]["code"] == code
    assert all(event != "done" for event, _ in events)
    assert repo.fail_run.await_args.kwargs["status"] == "failed"
    assert repo.fail_run.await_args.kwargs["duration_ms"] >= 0
    assert "test-only-sensitive" not in response.text


async def test_database_cancel_interrupts_stalled_model(
    api_client: httpx.AsyncClient, stream_setup: tuple, respx_mock: respx.MockRouter,
) -> None:
    repo, headers = stream_setup
    repo.get_run.side_effect = [SimpleNamespace(status="running"), SimpleNamespace(status="cancelled")]
    packets = Packets([frame("不应等到这里")], delay=5)
    respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    response = await asyncio.wait_for(api_client.post(
        f"/v1/sessions/{uuid4()}/chat/stream", headers=headers, json={"content": "问题"},
    ), timeout=2)
    assert decode_events(response.text)[-1][1]["error"]["code"] == "RUN_CANCELLED"
    assert repo.fail_run.await_args.kwargs["status"] == "cancelled"
    repo.complete_run.assert_not_called()
    assert packets.closed


async def test_consumer_close_after_meta_cancels_pending_run(
    api_client: httpx.AsyncClient, app: FastAPI, stream_setup: tuple,
) -> None:
    repo, _ = stream_setup
    service = app.state.chat_service
    session_id = uuid4()
    prepared = await service.prepare_stream(41, session_id, SessionChatRequest(content="问题"))
    events = service.stream(41, session_id, prepared)
    assert (await anext(events))[0] == "meta"
    await events.aclose()
    assert repo.fail_run.await_args.kwargs["status"] == "cancelled"
    repo.complete_run.assert_not_called()


async def test_disconnect_cancels_before_opening_model(
    api_client: httpx.AsyncClient, app: FastAPI, stream_setup: tuple,
) -> None:
    repo, _ = stream_setup
    service = app.state.chat_service
    prepared = await service.prepare_stream(41, uuid4(), SessionChatRequest(content="问题"))
    disconnected = AsyncMock(return_value=True)
    # Cancellation can race a model-open task; mock it as stalled, with no HTTP.
    service.llm = AsyncMock()
    async def stalled(request: object) -> None:
        await asyncio.sleep(5)
    service.llm.open_stream.side_effect = stalled
    events = [event async for event in service.stream(41, uuid4(), prepared, disconnected)]
    assert events[0][0] == "meta" and len(events) == 1
    assert repo.fail_run.await_args.kwargs["status"] == "cancelled"


async def test_response_send_failure_closes_iterator() -> None:
    closed = asyncio.Event()
    async def body():
        try:
            yield "event: meta\ndata: {}\n\n"
            await asyncio.sleep(5)
        finally:
            closed.set()
    cleanup = AsyncMock()
    response = SessionStreamResponse(body(), cleanup)
    async def receive():
        await asyncio.Event().wait()
    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")
    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert closed.is_set()
    cleanup.assert_awaited_once()


async def test_stop_endpoint_is_owned_and_returns_run_state(
    api_client: httpx.AsyncClient, stream_setup: tuple,
) -> None:
    repo, headers = stream_setup
    session_id, run_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    repo.cancel_run.return_value = RunInfo(
        id=run_id, status="cancelled", model="test", finish_reason=None, usage=None, duration_ms=None,
        error_code="RUN_CANCELLED", created_at=now, expires_at=now+timedelta(seconds=60), finished_at=now,
    )
    path = f"/v1/sessions/{session_id}/runs/{run_id}/cancel"
    assert (await api_client.post(path, headers=headers)).json()["status"] == "cancelled"
    repo.cancel_run.assert_awaited_once_with(41, session_id, run_id)
    repo.cancel_run.side_effect = AppError("SESSION_NOT_FOUND", "会话不存在。", 404)
    assert (await api_client.post(path, headers=headers)).status_code == 404


@pytest_asyncio.fixture
async def tcp_client(
    app: FastAPI, api_client: httpx.AsyncClient, respx_mock: respx.MockRouter,
) -> AsyncIterator[httpx.AsyncClient]:
    # Keep the existing fixture's app lifespan, but exercise Uvicorn's actual
    # socket streaming/disconnect behaviour instead of ASGITransport buffering.
    respx_mock.route(host="127.0.0.1").pass_through()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, lifespan="off", access_log=False, log_config=None, log_level="critical",
        timeout_graceful_shutdown=1,
    ))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()


@pytest.mark.parametrize("action", ["cancel", "disconnect"])
async def test_actual_tcp_delta_and_stop_cleanup(
    stream_setup: tuple, tcp_client: httpx.AsyncClient, respx_mock: respx.MockRouter, action: str,
) -> None:
    repo, headers = stream_setup
    session_id = uuid4()
    run_id = repo.start_run.return_value.id
    status = "running"
    async def current(*args):
        return SimpleNamespace(status=status)
    async def cancel(*args):
        nonlocal status
        status = "cancelled"
        now = datetime.now(timezone.utc)
        return RunInfo(id=run_id, status=status, model="test", finish_reason=None, usage=None,
                       duration_ms=None, error_code="RUN_CANCELLED", created_at=now,
                       expires_at=now+timedelta(seconds=60), finished_at=now)
    repo.get_run.side_effect = current
    repo.cancel_run.side_effect = cancel
    packets = Packets([frame("真实 TCP 增量"), frame("不应完成")], delay=0.3)
    respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    async with tcp_client.stream("POST", f"/v1/sessions/{session_id}/chat/stream", headers=headers,
                                 json={"content": "问题"}) as response:
        lines = response.aiter_lines()
        async for line in lines:
            if line == "event: delta":
                assert "真实 TCP 增量" in await anext(lines)
                break
        else:
            pytest.fail("No incremental content arrived")
        if action == "cancel":
            stopped = await tcp_client.post(f"/v1/sessions/{session_id}/runs/{run_id}/cancel", headers=headers)
            assert stopped.json()["status"] == "cancelled"
            remainder = "\n".join([line async for line in lines])
            assert "RUN_CANCELLED" in remainder and "event: done" not in remainder
        await lines.aclose()
    async with asyncio.timeout(3):
        while not repo.fail_run.await_count or not packets.closed:
            await asyncio.sleep(0.01)
    assert repo.fail_run.await_args.kwargs["status"] == "cancelled"
    repo.complete_run.assert_not_called()
