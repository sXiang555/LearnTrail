"""Authenticated JSON retrieval and session-scoped grounded question answering."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from learntrail_agent.rag.schemas import RagResponse, SearchRequest, SearchResponse
from learntrail_agent.security.auth import AuthenticatedUser, require_user

router = APIRouter(prefix="/v1", tags=["rag"])
CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]


@router.post("/rag/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request, user: CurrentUser) -> SearchResponse:
    token = request.headers["authorization"].split()[1]  # require_user already validates this header.
    return await request.app.state.retriever.search(user.user_id, body, token)


@router.post("/sessions/{session_id}/rag", response_model=RagResponse)
async def answer(session_id: UUID, body: SearchRequest, request: Request, user: CurrentUser) -> RagResponse:
    token = request.headers["authorization"].split()[1]
    return await request.app.state.rag_service.answer(user.user_id, session_id, body, token)
