"""Isolated app fixtures: all model traffic must be intercepted by respx."""

import time
from collections.abc import AsyncIterator, Callable

import httpx
import jwt
import pytest
import pytest_asyncio
import respx
from fastapi import FastAPI
from cryptography.hazmat.primitives.asymmetric import rsa

from learntrail_agent.main import create_app
from learntrail_agent.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, app_name="LearnTrail AI Test", learntrail_ai_env="test",
        llm_api_key="test-only-model-key", llm_base_url="https://model.example.test/v1",
        llm_model="test-model", llm_timeout_seconds=5, llm_connect_timeout_seconds=1,
        llm_temperature=None, llm_max_tokens=None, llm_stream_include_usage=False,
        database_url="", jwt_public_key_file=None, java_gateway_base_url="https://java.example.test",
        context_max_turns=10, context_max_chars=60000,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest_asyncio.fixture
async def api_client(app: FastAPI, respx_mock: respx.MockRouter) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        assert not app.state.http.is_closed
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
        ) as client:
            yield client
    assert app.state.http.is_closed


@pytest.fixture(scope="session")
def java_key() -> rsa.RSAPrivateKey:
    # Generated in memory for tests; never read or write the Java private key.
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token_factory(java_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def make(user_id: int = 41, **claims: object) -> str:
        payload = {"user": {"userId": user_id, "roleId": 2}, "exp": int(time.time()) + 300}
        payload.update(claims)
        return jwt.encode(payload, java_key, algorithm="RS256")
    return make
