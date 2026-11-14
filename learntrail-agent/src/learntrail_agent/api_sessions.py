"""Authenticated session endpoints; identity never comes from request bodies."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from learntrail_agent.schemas import (
    RunInfo, SessionChatRequest, SessionChatResponse, SessionCreate, SessionDetail, SessionInfo, SessionList,
)
from learntrail_agent.security.auth import AuthenticatedUser, require_user
from learntrail_agent.services.chat import ChatService
from learntrail_agent.storage.repositories import SessionRepository
from learntrail_agent.streaming import encode_event

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])
CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
logger = logging.getLogger(__name__)


class SessionStreamResponse(StreamingResponse):
    """Close the body even when an ASGI send raises before iteration completes."""

    def __init__(self, body: AsyncIterator[str], cleanup: Callable[[], Awaitable[None]]):
        super().__init__(body, media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })
        self._body = body
        self._cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self._body.aclose()
                finally:
                    await self._cleanup()


@router.post("", status_code=201, response_model=SessionInfo)
async def create_session(body: SessionCreate, request: Request, user: CurrentUser) -> SessionInfo:
    repository: SessionRepository = request.app.state.sessions
    return await repository.create(user.user_id, body.title)


@router.get("", response_model=SessionList)
async def list_sessions(
    request: Request, user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> SessionList:
    repository: SessionRepository = request.app.state.sessions
    return await repository.list(user.user_id, limit, offset)


@router.get("/{session_id}", response_model=SessionDetail)
async def session_detail(
    session_id: UUID, request: Request, user: CurrentUser,
    message_limit: Annotated[int, Query(ge=1, le=200)] = 100,
    message_offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> SessionDetail:
    repository: SessionRepository = request.app.state.sessions
    detail = await repository.detail(user.user_id, session_id, message_limit, message_offset)
    return await request.app.state.agent_service.sanitize_detail(user.user_id,detail,
                                                                request.headers["authorization"].split()[1])


@router.post("/{session_id}/chat", response_model=SessionChatResponse)
async def session_chat(
    session_id: UUID, body: SessionChatRequest, request: Request, user: CurrentUser,
) -> SessionChatResponse:
    service: ChatService = request.app.state.chat_service
    return await service.chat(user.user_id, session_id, body)


@router.post("/{session_id}/chat/stream", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}}}})
async def session_stream(
    session_id: UUID, body: SessionChatRequest, request: Request, user: CurrentUser,
) -> StreamingResponse:
    service: ChatService = request.app.state.chat_service
    prepared = await service.prepare_stream(user.user_id, session_id, body)

    async def events() -> AsyncIterator[str]:
        async with aclosing(service.stream(user.user_id, session_id, prepared, request.is_disconnected)) as stream:
            async for event, data in stream:
                yield encode_event(event, data)

    async def cleanup() -> None:
        if prepared.started:
            return
        # Cover disconnects before the service generator could start.
        try:
            await service.repository.fail_run(
                user.user_id, session_id, prepared.run.id, "RUN_CANCELLED", status="cancelled",
            )
        except Exception as error:
            logger.error("Response cleanup failed: %s", type(error).__name__)

    return SessionStreamResponse(events(), cleanup)


@router.post("/{session_id}/runs/{run_id}/cancel", response_model=RunInfo)
async def cancel_run(session_id: UUID, run_id: UUID, request: Request, user: CurrentUser) -> RunInfo:
    repository: SessionRepository = request.app.state.sessions
    result=await repository.cancel_run(user.user_id, session_id, run_id)
    advanced=getattr(request.app.state,"advanced",None)
    if advanced and result.status=="cancelled":
        await advanced.approvals.cancel_run(user.user_id,session_id,run_id)
    return result
