from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import AdminRole, AdminUser
from app.repositories import AdminUserRepository
from app.services.auth.admin_accounts_service import AdminAccountsService
from app.services.auth.passwords import verify_password
from app.services.auth.permissions import normalize_permission_list

_TOKEN_VERSION = 2
_ACCESS_TOKEN_TYPE = "access"
_REFRESH_TOKEN_TYPE = "refresh"
_auth_scheme = HTTPBearer(auto_error=False)
ACCESS_COOKIE_NAME = "admin_access_token"
REFRESH_COOKIE_NAME = "admin_refresh_token"


@dataclass(frozen=True)
class AdminTokenClaims:
    user_id: int
    login: str
    token_type: str
    exp: int
    iat: int
    jti: str


@dataclass(frozen=True)
class AdminAuthContext:
    claims: AdminTokenClaims
    user: AdminUser
    permissions: frozenset[str]

    @property
    def is_superuser(self) -> bool:
        return bool(self.user.is_superuser)

    @property
    def user_id(self) -> int:
        return int(self.user.id)

    @property
    def login(self) -> str:
        return str(self.user.login)

    def has_permission(self, permission: str) -> bool:
        normalized = str(permission or "").strip()
        if not normalized:
            return False
        if self.is_superuser:
            return True
        return normalized in self.permissions


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
    user_id = int(payload.get("uid") or 0)
    login = str(payload.get("sub") or "").strip()
    if user_id <= 0 or not login:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный пользователь токена")
    iat = int(payload.get("iat") or 0)
    jti = str(payload.get("jti") or "")
    return AdminTokenClaims(user_id=user_id, login=login, token_type=token_type, exp=exp, iat=iat, jti=jti)


def _role_permissions(role: AdminRole | None) -> frozenset[str]:
    if role is None:
        return frozenset()
    return frozenset(normalize_permission_list(role.permissions))


def _build_auth_context(claims: AdminTokenClaims, user: AdminUser) -> AdminAuthContext:
    permissions = frozenset() if bool(user.is_superuser) else _role_permissions(user.role)
    return AdminAuthContext(claims=claims, user=user, permissions=permissions)


def _load_user_for_claims(db: Session, claims: AdminTokenClaims) -> AdminUser:
    repository = AdminUserRepository(db)
    user = repository.get_with_role(claims.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь токена не найден")
    if str(user.login or "").strip() != claims.login:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Токен не соответствует пользователю")
    if not bool(user.is_active):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь отключен")
    return user


def issue_admin_token_pair(*, user_id: int, login: str) -> dict[str, Any]:
    now = int(time.time())
    access_claims = {
        "uid": int(user_id),
        "sub": str(login),
        "type": _ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + settings.admin_access_token_ttl_sec,
        "jti": uuid.uuid4().hex,
    }
    refresh_claims = {
        "uid": int(user_id),
        "sub": str(login),
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


def verify_admin_credentials(db: Session, login: str, password: str) -> AdminUser | None:
    AdminAccountsService(db).ensure_superadmin_user()
    repository = AdminUserRepository(db)
    user = repository.get_by_login(str(login or "").strip())
    if user is None:
        return None
    if not bool(user.is_active):
        return None
    if not verify_password(password, str(user.password_hash or "")):
        return None
    return repository.get_with_role(int(user.id))


def require_admin_access(
    credentials: HTTPAuthorizationCredentials | None = Depends(_auth_scheme),
    access_cookie_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> AdminAuthContext:
    bearer_token = None
    if credentials is not None and str(credentials.scheme).lower() == "bearer" and credentials.credentials:
        bearer_token = credentials.credentials
    token = bearer_token or access_cookie_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    claims = _decode_and_validate(token, expected_type=_ACCESS_TOKEN_TYPE)
    user = _load_user_for_claims(db, claims)
    return _build_auth_context(claims, user)


def verify_refresh_token(refresh_token: str, db: Session) -> AdminAuthContext:
    claims = _decode_and_validate(refresh_token, expected_type=_REFRESH_TOKEN_TYPE)
    user = _load_user_for_claims(db, claims)
    return _build_auth_context(claims, user)


def require_superadmin(context: AdminAuthContext = Depends(require_admin_access)) -> AdminAuthContext:
    if not context.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return context


def require_permission(permission: str) -> Callable[[AdminAuthContext], AdminAuthContext]:
    required = str(permission or "").strip()

    def dependency(context: AdminAuthContext = Depends(require_admin_access)) -> AdminAuthContext:
        if context.has_permission(required):
            return context
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")

    return dependency
