"""Deployment exposure must not include the unauthenticated developer client."""

import httpx
import pytest
from fastapi import FastAPI

from learntrail_agent.deployment import DeploymentApi


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/v1/chat", "/v1/chat/stream", "/docs", "/openapi.json",
    "/v1/rag/index", "/v1/sessions-extra", "/v1/memories-other",
])
async def test_deployment_blocks_developer_and_unlisted_routes(app: FastAPI, path: str) -> None:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=DeploymentApi(app)), base_url="http://test") as client:
            response = await client.post(path, json={"content": "must not reach a model"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/sessions", "/v1/skills", "/v1/memories", "/mcp/"])
async def test_deployment_keeps_authentication_on_exposed_routes(app: FastAPI, path: str) -> None:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=DeploymentApi(app)), base_url="http://test") as client:
            response = await client.get(path)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deployment_preserves_lifespan_and_readiness(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=DeploymentApi(app)), base_url="http://test") as client:
            response = await client.get("/v1/health")
            assert not app.state.http.is_closed
    assert response.status_code == 200
    assert app.state.http.is_closed
