"""SSE framing independent of transport packet boundaries."""

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from learntrail_agent.errors import protocol_error

EventName = Literal["meta", "delta", "usage", "done", "error"]
StreamEvent = tuple[EventName, dict[str, Any]]


def encode_event(event: EventName, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n"


async def iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    data: list[str] = []
    size = 0
    first = True
    async for line in lines:
        if first:
            line = line.removeprefix("\ufeff")
            first = False
        if len(line) > 1048576:
            raise protocol_error()
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
                size = 0
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field == "data":
            value = value.removeprefix(" ") if separator else ""
            size += len(value)
            if size > 1048576:
                raise protocol_error()
            data.append(value)
    if data:
        yield "\n".join(data)
