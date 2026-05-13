from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_TOKEN_VERSION = 1
_ACCESS_TOKEN_TYPE = "access"
_REFRESH_TOKEN_TYPE = "refresh"
_auth_scheme = HTTPBearer(auto_error=False)
ACCESS_COOKIE_NAME = "admin_access_token"
REFRESH_COOKIE_NAME = "admin_refresh_token"


@dataclass(frozen=True)
class AdminTokenClaims:
    sub: str
    token_type: str
    exp: int
    iat: int
    jti: str


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _sign(payload: str) -> str:
    digest = hmac.new(settings.admin_token_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64_encode(digest)


def _encode_token(claims: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT", "ver": _TOKEN_VERSION}
    header_b64 = _b64_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _sign(f"{header_b64}.{payload_b64}")
    return f"{header_b64}.{payload_b64}.{signature}"


def _decode_and_validate(token: str, *, expected_type: str) -> AdminTokenClaims:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен")
    header_b64, payload_b64, signature = parts
    expected_signature = _sign(f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверная подпись токена")
    try:
        payload = json.loads(_b64_decode(payload_b64))
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен")
    now = int(time.time())
    token_type = str(payload.get("type") or "")
    exp = int(payload.get("exp") or 0)
    if token_type != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный тип токена")
    if exp <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Токен истёк")
    sub = str(payload.get("sub") or "")
    if sub != settings.admin_superuser_login:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный пользователь токена")
    iat = int(payload.get("iat") or 0)
    jti = str(payload.get("jti") or "")
    return AdminTokenClaims(sub=sub, token_type=token_type, exp=exp, iat=iat, jti=jti)


def issue_admin_token_pair() -> dict[str, Any]:
    now = int(time.time())
    access_claims = {
        "sub": settings.admin_superuser_login,
        "type": _ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + settings.admin_access_token_ttl_sec,
        "jti": uuid.uuid4().hex,
    }
    refresh_claims = {
        "sub": settings.admin_superuser_login,
        "type": _REFRESH_TOKEN_TYPE,
        "iat": now,
        "exp": now + settings.admin_refresh_token_ttl_sec,
        "jti": uuid.uuid4().hex,
    }
    return {
        "access_token": _encode_token(access_claims),
        "refresh_token": _encode_token(refresh_claims),
        "access_expires_in": settings.admin_access_token_ttl_sec,
        "refresh_expires_in": settings.admin_refresh_token_ttl_sec,
    }


def verify_admin_credentials(login: str, password: str) -> bool:
    # compare_digest for str raises TypeError on non-ASCII input in Python 3.12.
    # Compare UTF-8 bytes to keep constant-time semantics and avoid 500 on brute-force payloads.
    login_ok = secrets.compare_digest(login.encode("utf-8"), settings.admin_superuser_login.encode("utf-8"))
    password_ok = secrets.compare_digest(password.encode("utf-8"), settings.admin_superuser_password.encode("utf-8"))
    return login_ok and password_ok


def require_admin_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(_auth_scheme),
    access_cookie_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
) -> AdminTokenClaims:
    bearer_token = None
    if credentials is not None and str(credentials.scheme).lower() == "bearer" and credentials.credentials:
        bearer_token = credentials.credentials
    token = bearer_token or access_cookie_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    return _decode_and_validate(token, expected_type=_ACCESS_TOKEN_TYPE)


def verify_refresh_token(refresh_token: str) -> AdminTokenClaims:
    return _decode_and_validate(refresh_token, expected_type=_REFRESH_TOKEN_TYPE)
