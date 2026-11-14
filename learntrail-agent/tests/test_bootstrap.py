"""Offline checks for the Python environment and test tooling only."""

import importlib
import sys

import httpx
import jwt
import psycopg
import pydantic
import pytest
import respx


def test_python_package_and_runtime_dependencies() -> None:
    assert sys.version_info[:2] == (3, 12)
    for module in (
        "learntrail_agent",
        "fastapi",
        "uvicorn",
        "pydantic_settings",
    ):
        importlib.import_module(module)
    assert pydantic.__version__.split(".")[0] == "2"
    assert psycopg.pq.version() > 0
    assert "RS256" in jwt.algorithms.get_default_algorithms()


@pytest.mark.asyncio
async def test_async_http_mock_tooling(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("https://dependencies.example.test/probe").respond(204)
    async with httpx.AsyncClient() as client:
        response = await client.get("https://dependencies.example.test/probe")
    assert response.status_code == 204
    assert route.call_count == 1
