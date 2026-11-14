"""Local development routes; no user authentication or conversation storage yet."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from learntrail_agent.clients.llm import LLMClient
from learntrail_agent.errors import LLMError
from learntrail_agent.schemas import ChatRequest, ChatResponse, HealthResponse
from learntrail_agent.settings import Settings
from learntrail_agent.streaming import encode_event

router = APIRouter(prefix="/v1")
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        app_name=settings.app_name,
        environment=settings.learntrail_ai_env,
        llm_configured=settings.llm_configured,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    llm: LLMClient = request.app.state.llm
    return await llm.complete(body)


@router.post("/chat/stream", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}}}})
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    llm: LLMClient = request.app.state.llm
    stream = await llm.open_stream(body)

    async def events() -> AsyncIterator[str]:
        try:
            yield encode_event("meta", {"model": llm.settings.llm_model})
            async with aclosing(llm.stream_events(stream)) as upstream:
                async for event, data in upstream:
                    yield encode_event(event, data)
        except LLMError as error:
            yield encode_event("error", error.payload())
        except Exception as error:
            logger.error("Chat stream failed: %s", type(error).__name__)
            yield encode_event("error", LLMError("INTERNAL_ERROR", "流式响应处理失败。", 500).payload())
        finally:
            await asyncio.shield(stream.response.aclose())

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
