"""Authenticated approvals, skill metadata and explicitly managed long-term memory."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Query

from learntrail_agent.agent.advanced_models import ApprovalDecision, MemoryCreate, MemoryUpdate, MemoryInfo
from learntrail_agent.errors import AppError
from learntrail_agent.security.auth import AuthenticatedUser, require_user
from learntrail_agent.services.capabilities import AdvancedRuntime

router=APIRouter(prefix="/v1",tags=["agent-capabilities"])
CurrentUser=Annotated[AuthenticatedUser,Depends(require_user)]


def runtime(request: Request) -> AdvancedRuntime:
    value=request.app.state.advanced
    if value is None:raise AppError("CAPABILITIES_NOT_CONFIGURED","高级能力需要 AI PostgreSQL 及 V006 迁移。",503)
    return value


@router.get("/skills")
async def skills(request: Request, user: CurrentUser) -> dict:
    advanced=runtime(request)
    catalog=await asyncio.to_thread(advanced.skills.discover)
    return {"items":[s.public() for s in catalog.values()],"mcp_servers":[
        {"id":p.id,"transport":p.transport,"tools":list(p.allow_tools)} for p in advanced.mcp.profiles.values()]}


@router.get("/sessions/{session_id}/approvals")
async def approvals(session_id: UUID, request: Request, user: CurrentUser, run_id: UUID | None = None) -> dict:
    return {"items":await runtime(request).approvals.list(user.user_id,session_id,run_id)}


@router.get("/sessions/{session_id}/approvals/{approval_id}")
@router.get("/sessions/{session_id}/approvals/{approval_id}/result")
async def approval(session_id: UUID, approval_id: UUID, request: Request, user: CurrentUser) -> dict:
    return await runtime(request).approvals.get(user.user_id,session_id,approval_id)


@router.post("/sessions/{session_id}/approvals/{approval_id}/decision")
async def decide(session_id: UUID, approval_id: UUID, body: ApprovalDecision, request: Request, user: CurrentUser) -> dict:
    result=await runtime(request).approvals.decide(user.user_id,session_id,approval_id,body)
    return {**result,"resume_required":result["status"] in {"approved","rejected","cancelled"}}


@router.get("/memories")
async def memories(request: Request, user: CurrentUser, include_inactive: bool = False) -> dict:
    return {"items":await runtime(request).memories.list(user.user_id,include_inactive),"limit":50}


@router.post("/memories",response_model=MemoryInfo,status_code=201)
async def create_memory(body: MemoryCreate, request: Request, user: CurrentUser) -> MemoryInfo:
    return await runtime(request).memories.create(user.user_id,body)


@router.get("/memories/{memory_id}",response_model=MemoryInfo)
async def memory(memory_id: UUID, request: Request, user: CurrentUser) -> MemoryInfo:
    return await runtime(request).memories.get(user.user_id,memory_id)


@router.put("/memories/{memory_id}",response_model=MemoryInfo)
async def update_memory(memory_id: UUID, body: MemoryUpdate, request: Request, user: CurrentUser) -> MemoryInfo:
    return await runtime(request).memories.update(user.user_id,memory_id,body)


@router.delete("/memories/{memory_id}",response_model=MemoryInfo)
async def delete_memory(memory_id: UUID, request: Request, user: CurrentUser, version: int = Query(ge=1)) -> MemoryInfo:
    return await runtime(request).memories.delete(user.user_id,memory_id,version)


@router.get("/memories/{memory_id}/source")
async def memory_source(memory_id: UUID, request: Request, user: CurrentUser) -> dict:
    value=await runtime(request).memories.get(user.user_id,memory_id)
    return value.model_dump(include={"id","source_kind","source_session_id","source_message_id","source_quote","created_at"})
