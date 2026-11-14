"""RSA/JWT tests and mocked Java account-state checks, not Java integration."""

import base64
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from learntrail_agent.errors import AppError
from learntrail_agent.security.auth import decode_access_token, load_public_key
from learntrail_agent.settings import Settings


def test_java_claims_are_verified(java_key: rsa.RSAPrivateKey, token_factory: Callable[..., str]) -> None:
    assert decode_access_token(token_factory(42), java_key.public_key()).user_id == 42


@pytest.mark.parametrize("claims", [
    {"exp": int(time.time()) - 60}, {"jti": "test-refresh-id"}, {"user": {}},
    {"user": {"userId": "41"}}, {"user": {"userId": True}}, {"user": {"userId": -1}},
    {"user": {"userId": 2**63}}, {"user": "41"}, {"exp": None},
    {"exp": str(int(time.time()) + 300)}, {"exp": float(int(time.time()) + 300)},
])
def test_bad_claims_rejected(
    java_key: rsa.RSAPrivateKey, token_factory: Callable[..., str], claims: dict[str, object],
) -> None:
    token = token_factory(**claims)
    with pytest.raises(AppError) as caught:
        decode_access_token(token, java_key.public_key())
    assert caught.value.status_code == 401
    assert token not in str(caught.value)


def test_missing_exp_and_bad_signature(java_key: rsa.RSAPrivateKey, token_factory: Callable[..., str]) -> None:
    tokens = [jwt.encode({"user": {"userId": 41}}, java_key, algorithm="RS256"),
              jwt.encode({"exp": int(time.time()) + 300}, java_key, algorithm="RS256"),
              token_factory()]
    different_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    for token in tokens:
        with pytest.raises(AppError):
            decode_access_token(token, different_key if token == tokens[-1] else java_key.public_key())


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_algorithm_is_fixed_to_rs256(java_key: rsa.RSAPrivateKey, algorithm: str) -> None:
    token = jwt.encode(
        {"user": {"userId": 41}, "exp": int(time.time()) + 300},
        None if algorithm == "none" else "test-only-hmac-secret-32-characters", algorithm=algorithm,
    )
    with pytest.raises(AppError):
        decode_access_token(token, java_key.public_key())


@pytest.mark.parametrize("encoding", ["pem", "base64-der"])
def test_load_trusted_public_file(tmp_path: Path, java_key: rsa.RSAPrivateKey, encoding: str) -> None:
    raw = java_key.public_key().public_bytes(
        serialization.Encoding.PEM if encoding == "pem" else serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "public-key"
    path.write_bytes(raw if encoding == "pem" else base64.b64encode(raw))
    settings = Settings(_env_file=None, jwt_public_key_file=path)
    assert load_public_key(settings).public_numbers() == java_key.public_key().public_numbers()


def test_invalid_key_error_does_not_echo_file(tmp_path: Path) -> None:
    path = tmp_path / "invalid-key"
    path.write_text("not-a-public-key", encoding="utf-8")
    with pytest.raises(RuntimeError) as caught:
        load_public_key(Settings(_env_file=None, jwt_public_key_file=path))
    assert "not-a-public-key" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "raw-token", "Basic test", "Bearer a b"])
async def test_missing_or_bad_bearer(api_client: httpx.AsyncClient, header: str | None) -> None:
    response = await api_client.get("/v1/sessions", headers={} if header is None else {"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_missing_key_is_configuration_failure(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/v1/sessions", headers={"Authorization": "Bearer test"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status,body,expected,code", [
    (200, {"code": 200, "data": True}, 503, "DATABASE_NOT_CONFIGURED"),
    (200, {"code": 200, "data": False}, 403, "ACCOUNT_DISABLED"),
    (200, {"code": 200, "data": "true"}, 503, "ACCOUNT_STATUS_UNAVAILABLE"),
    (200, {"code": 0, "data": True}, 503, "ACCOUNT_STATUS_UNAVAILABLE"),
    (200, True, 503, "ACCOUNT_STATUS_UNAVAILABLE"),
    (401, {"secret": "test-only-sensitive"}, 401, "INVALID_TOKEN"),
    (500, {"secret": "test-only-sensitive"}, 503, "ACCOUNT_STATUS_UNAVAILABLE"),
])
async def test_java_status_is_required_and_fail_closed(
    api_client: httpx.AsyncClient, app: FastAPI, java_key: rsa.RSAPrivateKey,
    token_factory: Callable[..., str], respx_mock: respx.MockRouter,
    upstream_status: int, body: object, expected: int, code: str,
) -> None:
    app.state.jwt_public_key = java_key.public_key()
    token = token_factory()
    route = respx_mock.get("https://java.example.test/us/users/41/enabled").respond(upstream_status, json=body)
    response = await api_client.get("/v1/sessions", headers={"Authorization": f"Bearer {token}", "user-info": "999"})
    assert response.status_code == expected
    assert response.json()["error"]["code"] == code
    assert route.calls[0].request.headers["authorization"] == token
    assert "user-info" not in route.calls[0].request.headers
    assert token not in response.text and "test-only-sensitive" not in response.text


@pytest.mark.asyncio
async def test_java_timeout_is_not_authenticated(
    api_client: httpx.AsyncClient, app: FastAPI, java_key: rsa.RSAPrivateKey,
    token_factory: Callable[..., str], respx_mock: respx.MockRouter,
) -> None:
    app.state.jwt_public_key = java_key.public_key()
    respx_mock.get("https://java.example.test/us/users/41/enabled").mock(side_effect=httpx.ReadTimeout("test-only-secret"))
    response = await api_client.get("/v1/sessions", headers={"Authorization": f"Bearer {token_factory()}"})
    assert response.status_code == 503
    assert "test-only-secret" not in response.text


@pytest.mark.asyncio
async def test_bad_token_never_calls_java(
    api_client: httpx.AsyncClient, app: FastAPI, java_key: rsa.RSAPrivateKey, respx_mock: respx.MockRouter,
) -> None:
    app.state.jwt_public_key = java_key.public_key()
    response = await api_client.get("/v1/sessions", headers={"Authorization": "Bearer invalid-test-token"})
    assert response.status_code == 401
    assert not respx_mock.calls
