import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI

from learntrail_agent.schemas import ChatRequest
from learntrail_agent.settings import Settings

URL = "https://model.example.test/v1/chat/completions"
BODY = {"messages": [{"role": "user", "content": "你好"}]}
pytestmark = pytest.mark.asyncio


def frame(content: str = "", finish: str | None = None) -> bytes:
    data = {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]}
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\r\n\r\n").encode()


class Packets(httpx.AsyncByteStream):
    def __init__(self, packets: list[bytes], error: Exception | None = None, delay: float = 0):
        self.packets = packets
        self.error = error
        self.delay = delay
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for packet in self.packets:
            await asyncio.sleep(self.delay)
            yield packet
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


def decode_events(text: str) -> list[tuple[str, dict]]:
    result = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        result.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return result


async def test_chinese_packets_usage_and_done(
    api_client: httpx.AsyncClient, settings: Settings, respx_mock: respx.MockRouter,
) -> None:
    settings.llm_stream_include_usage = True
    usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    wire = b": heartbeat\r\n\r\n" + frame("你好") + frame("，学途！") + frame(finish="stop")
    wire += ("data: " + json.dumps({"choices": [], "usage": usage}) + "\r\n\r\n").encode()
    wire += b"data: [DONE]\r\n\r\n"
    # Every UTF-8 code point and SSE field may span multiple network reads.
    packets = Packets([bytes([byte]) for byte in wire])
    route = respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    response = await api_client.post("/v1/chat/stream", json=BODY)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    events = decode_events(response.text)
    assert "".join(data["content"] for event, data in events if event == "delta") == "你好，学途！"
    assert ("usage", usage) in events
    assert events[-1] == ("done", {"finish_reason": "stop"})
    assert packets.closed
    sent = json.loads(route.calls[0].request.content)
    assert sent["stream"] and sent["stream_options"] == {"include_usage": True}


async def test_stream_without_usage(api_client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL).respond(
        200, headers={"content-type": "text/event-stream"},
        content=frame("回答", "length") + b"data: [DONE]\n\n",
    )
    response = await api_client.post("/v1/chat/stream", json=BODY)
    events = decode_events(response.text)
    assert events[-1] == ("done", {"finish_reason": "length"})
    assert not any(event == "usage" for event, _ in events)
    assert "stream_options" not in json.loads(route.calls[0].request.content)


@pytest.mark.parametrize("wire,code", [
    (b"data: not-json\n\n", "LLM_INVALID_RESPONSE"),
    (b'data: {"error":{"message":"PRIVATE-UPSTREAM-BODY"}}\n\n', "LLM_INVALID_RESPONSE"),
    (b"data: [DONE]\n\n", "LLM_INVALID_RESPONSE"),
    (frame("尚未完成"), "LLM_STREAM_INTERRUPTED"),
    (frame(finish="stop"), "LLM_STREAM_INTERRUPTED"),
    (frame(finish="stop") + frame("结束后出现正文"), "LLM_INVALID_RESPONSE"),
])
async def test_invalid_or_truncated_stream(
    wire: bytes, code: str, api_client: httpx.AsyncClient, respx_mock: respx.MockRouter,
) -> None:
    packets = Packets([wire])
    respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    response = await api_client.post("/v1/chat/stream", json=BODY)
    events = decode_events(response.text)
    assert events[-1][0] == "error" and events[-1][1]["error"]["code"] == code
    assert not any(event == "done" for event, _ in events)
    assert "PRIVATE-UPSTREAM-BODY" not in response.text
    assert packets.closed


@pytest.mark.parametrize("error,code", [
    (httpx.RemoteProtocolError("PRIVATE-UPSTREAM-BODY"), "LLM_STREAM_INTERRUPTED"),
    (httpx.ReadTimeout("PRIVATE-UPSTREAM-BODY"), "LLM_TIMEOUT"),
])
async def test_stream_read_failure(
    error: Exception, code: str, api_client: httpx.AsyncClient, respx_mock: respx.MockRouter,
) -> None:
    packets = Packets([frame("部分回答")], error=error)
    respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    response = await api_client.post("/v1/chat/stream", json=BODY)
    assert decode_events(response.text)[-1][1]["error"]["code"] == code
    assert "event: done" not in response.text and "PRIVATE-UPSTREAM-BODY" not in response.text
    assert packets.closed


async def test_wrong_content_type_before_sse(api_client: httpx.AsyncClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).respond(200, json={"message": "not SSE"})
    response = await api_client.post("/v1/chat/stream", json=BODY)
    assert response.status_code == 502 and response.json()["error"]["code"] == "LLM_INVALID_RESPONSE"


async def test_stream_deadline(
    api_client: httpx.AsyncClient, settings: Settings, respx_mock: respx.MockRouter,
) -> None:
    settings.llm_timeout_seconds = 0.01
    packets = Packets([frame("太晚")], delay=1)
    respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    response = await api_client.post("/v1/chat/stream", json=BODY)
    assert decode_events(response.text)[-1][1]["error"]["code"] == "LLM_TIMEOUT"
    assert packets.closed


async def test_cancelled_consumer_closes_upstream(
    api_client: httpx.AsyncClient, app: FastAPI, respx_mock: respx.MockRouter,
) -> None:
    packets = Packets([frame("部分回答"), frame("继续")], delay=0.01)
    respx_mock.post(URL).respond(200, headers={"content-type": "text/event-stream"}, stream=packets)
    handle = await app.state.llm.open_stream(ChatRequest.model_validate(BODY))
    events = app.state.llm.stream_events(handle)
    assert (await anext(events))[0] == "delta"
    pending = asyncio.create_task(anext(events))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert packets.closed
