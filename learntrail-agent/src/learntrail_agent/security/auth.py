"""Verify Java access tokens, then check current account state through its gateway."""

import base64
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Request
from pydantic import BaseModel, StrictBool, StrictInt, ValidationError

from learntrail_agent.errors import AppError
from learntrail_agent.settings import PROJECT_ROOT, Settings


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: int


class AccountStatus(BaseModel):
    code: StrictInt
    data: StrictBool


def load_public_key(settings: Settings) -> RSAPublicKey | None:
    """Only a server-configured public file is trusted; never fetch a token's jku."""
    if settings.jwt_public_key_file is None:
        return None
    path = settings.jwt_public_key_file
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        if path.stat().st_size > 16384:
            raise ValueError("Public key too large")
        raw = path.read_bytes().strip()
        if raw.startswith(b"-----BEGIN PUBLIC KEY-----"):
            key = serialization.load_pem_public_key(raw)
        else:
            # JwkController returns Base64(X.509 DER), not a standard JWKS JSON.
            key = serialization.load_der_public_key(base64.b64decode(raw, validate=True))
        if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
            raise ValueError("Expected RSA public key")
        return key
    except (OSError, ValueError, TypeError):
        raise RuntimeError("JWT public key configuration is invalid") from None


def decode_access_token(token: str, key: RSAPublicKey) -> AuthenticatedUser:
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], options={"require": ["exp", "user"]})
        # JwtTool.createToken has no jti; the refresh token has one.
        if "jti" in claims or type(claims["exp"]) is not int:
            raise ValueError("Not a Java access token")
        user = claims["user"]
        user_id = user.get("userId") if isinstance(user, dict) else None
        if type(user_id) is not int or not 0 < user_id <= 9223372036854775807:
            raise ValueError("Invalid Java user identity")
        return AuthenticatedUser(user_id)
    except jwt.ExpiredSignatureError:
        raise AppError("TOKEN_EXPIRED", "登录已过期，请重新登录。", 401) from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise AppError("INVALID_TOKEN", "登录凭证无效。", 401) from None


async def require_user(request: Request) -> AuthenticatedUser:
    values = request.headers.getlist("authorization")
    parts = values[0].split() if len(values) == 1 else []
    if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 8192:
        raise AppError("AUTH_REQUIRED", "请提供有效的 Bearer 登录凭证。", 401)
    return await authenticate_token(
        parts[1], request.app.state.jwt_public_key, request.app.state.http, request.app.state.settings,
    )


async def authenticate_token(
    token: str, key: RSAPublicKey | None, http: httpx.AsyncClient, settings: Settings,
) -> AuthenticatedUser:
    """Shared service boundary: signed access identity plus current Java status."""
    if key is None:
        raise AppError("AUTH_NOT_CONFIGURED", "尚未配置可信的 Java JWT 公钥。", 503)
    if len(token) > 8192 or token.count(".") != 2 or any(ord(c) <= 32 or ord(c) > 126 for c in token):
        raise AppError("INVALID_TOKEN", "登录凭证无效。", 401)
    user = decode_access_token(token, key)
    if not settings.java_gateway_base_url:
        raise AppError("ACCOUNT_STATUS_UNAVAILABLE", "暂时无法核验账号状态。", 503, True)
    try:
        response = await http.get(
            f"{settings.java_gateway_base_url}/us/users/{user.user_id}/enabled",
            # AccountAuthFilter.parseToken expects the raw JWT, not a Bearer prefix.
            headers={"Authorization": token}, timeout=settings.java_timeout_seconds,
        )
        if response.status_code == 401:
            raise AppError("INVALID_TOKEN", "Java 服务拒绝了登录凭证。", 401)
        if response.status_code != 200:
            raise ValueError("Account status unavailable")
        status = AccountStatus.model_validate(response.json())
        if status.code != 200:
            raise ValueError("Account status unavailable")
    except (httpx.HTTPError, ValueError, ValidationError):
        raise AppError("ACCOUNT_STATUS_UNAVAILABLE", "暂时无法核验账号状态。", 503, True) from None
    if not status.data:
        raise AppError("ACCOUNT_DISABLED", "账号已禁用或不存在。", 403)
    return user
