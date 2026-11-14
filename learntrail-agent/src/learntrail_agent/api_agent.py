"""Authenticated Agent entry points, reusing the existing session and SSE response."""

from contextlib import aclosing
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from learntrail_agent.agent.schemas import AgentChatRequest, AgentChatResponse, ResumeRequest
from learntrail_agent.api_sessions import SessionStreamResponse
from learntrail_agent.security.auth import AuthenticatedUser, require_user
from learntrail_agent.streaming import encode_event
from learntrail_agent.services.agent import AgentService, PreparedAgent

router=APIRouter(prefix="/v1/sessions",tags=["agent"])
CurrentUser=Annotated[AuthenticatedUser,Depends(require_user)]


def stream_response(request: Request, service: AgentService, prepared: PreparedAgent) -> SessionStreamResponse:
    async def events() -> AsyncIterator[str]:
        async with aclosing(service.stream(prepared,request.is_disconnected)) as stream:
            async for kind,data in stream:
                yield encode_event(kind,data)
    async def cleanup() -> None:
        await service.abandon(prepared)
    return SessionStreamResponse(events(),cleanup)


@router.post("/{session_id}/agent",response_model=AgentChatResponse)
async def agent(session_id: UUID, body: AgentChatRequest, request: Request, user: CurrentUser) -> AgentChatResponse:
    service=request.app.state.agent_service
    prepared=await service.prepare(user.user_id,session_id,body,request.headers["authorization"].split()[1])
    return await service.execute(prepared,disconnected=request.is_disconnected)


@router.post("/{session_id}/agent/stream",response_class=SessionStreamResponse,
             responses={200:{"content":{"text/event-stream":{}}}})
async def agent_stream(session_id: UUID, body: AgentChatRequest, request: Request, user: CurrentUser) -> SessionStreamResponse:
    service=request.app.state.agent_service
    prepared=await service.prepare(user.user_id,session_id,body,request.headers["authorization"].split()[1])
    return stream_response(request,service,prepared)


@router.get("/{session_id}/agent/runs/{run_id}")
async def trace(session_id: UUID, run_id: UUID, request: Request, user: CurrentUser) -> dict:
    return await request.app.state.agent_service.trace(user.user_id,session_id,run_id,
                                                       request.headers["authorization"].split()[1])


@router.get("/{session_id}/agent/runs/{run_id}/state")
async def workflow_state(session_id: UUID, run_id: UUID, request: Request, user: CurrentUser) -> dict:
    return await request.app.state.agent_service.workflow_state(user.user_id,session_id,run_id,
        request.headers["authorization"].split()[1])


@router.post("/{session_id}/agent/runs/{run_id}/resume",response_model=AgentChatResponse)
async def resume(session_id: UUID, run_id: UUID, body: ResumeRequest, request: Request,
                 user: CurrentUser) -> AgentChatResponse:
    service=request.app.state.agent_service
    prepared=await service.prepare_resume(user.user_id,session_id,run_id,request.headers["authorization"].split()[1])
    return await service.execute(prepared,disconnected=request.is_disconnected)


@router.post("/{session_id}/agent/runs/{run_id}/resume/stream",response_class=SessionStreamResponse)
async def resume_stream(session_id: UUID, run_id: UUID, body: ResumeRequest, request: Request,
                        user: CurrentUser) -> SessionStreamResponse:
    service=request.app.state.agent_service
    prepared=await service.prepare_resume(user.user_id,session_id,run_id,request.headers["authorization"].split()[1])
    return stream_response(request,service,prepared)
