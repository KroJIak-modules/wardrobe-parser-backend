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
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import ValidationError
from app.models import ShowcaseSetting, SiteAccessSetting
from app.schemas.admin_site_content import (
    AdminSiteAccessPasswordGenerateResponse,
    AdminSiteAccessSettingsResponse,
    AdminSiteAccessSettingsUpdateRequest,
)
from app.schemas.site import SiteAccessStatusResponse, SiteAccessUnlockResponse
from app.services.auth.passwords import hash_password, verify_password


SITE_ACCESS_COOKIE_NAME = "site_access_token"
_SITE_ACCESS_TOKEN_TYPE = "site_access"
_TOKEN_VERSION = 1


@dataclass(frozen=True)
class SiteAccessUnlockResult:
    response: SiteAccessUnlockResponse
    token: str | None
    max_age: int


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _sign(payload: str) -> str:
    digest = hmac.new(settings.site_access_token_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64_encode(digest)


def _encode_token(claims: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT", "ver": _TOKEN_VERSION}
    header_b64 = _b64_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _sign(f"{header_b64}.{payload_b64}")
    return f"{header_b64}.{payload_b64}.{signature}"


def _decode_token(token: str) -> dict[str, Any] | None:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, signature = parts
    expected_signature = _sign(f"{header_b64}.{payload_b64}")
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(_b64_decode(payload_b64))
    except (json.JSONDecodeError, ValueError):
        return None
    if str(payload.get("type") or "") != _SITE_ACCESS_TOKEN_TYPE:
        return None
    if int(payload.get("exp") or 0) <= int(time.time()):
        return None
    return payload if isinstance(payload, dict) else None


class SiteAccessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_settings(self) -> SiteAccessSetting:
        entity = self.db.query(SiteAccessSetting).order_by(SiteAccessSetting.id.asc()).first()
        if entity is None:
            entity = SiteAccessSetting(id=1)
            self.db.add(entity)
            self.db.flush()
        return entity

    @staticmethod
    def _admin_response(entity: SiteAccessSetting) -> AdminSiteAccessSettingsResponse:
        return AdminSiteAccessSettingsResponse(
            enabled=bool(entity.enabled),
            title=str(entity.title or ""),
            description=str(entity.description or ""),
            password=str(entity.password_value or ""),
            updated_at=entity.updated_at.isoformat() if entity.updated_at is not None else "",
        )

    def get_admin_settings(self) -> AdminSiteAccessSettingsResponse:
        return self._admin_response(self.ensure_settings())

    @staticmethod
    def generate_password() -> AdminSiteAccessPasswordGenerateResponse:
        return AdminSiteAccessPasswordGenerateResponse(password=secrets.token_urlsafe(14))

    def update_admin_settings(self, payload: AdminSiteAccessSettingsUpdateRequest) -> AdminSiteAccessSettingsResponse:
        entity = self.ensure_settings()
        next_enabled = bool(payload.enabled)
        next_title = str(payload.title or "").strip()
        next_description = str(payload.description or "").strip()
        next_password = str(payload.password or "").strip()
        if next_enabled and not next_password:
            raise ValidationError("Укажите пароль для защиты сайта")

        password_changed = next_password != str(entity.password_value or "")
        enabled_changed = next_enabled != bool(entity.enabled)
        entity.enabled = next_enabled
        entity.title = next_title
        entity.description = next_description
        password_hash_missing = bool(next_password) and not str(entity.password_hash or "").strip()
        if password_changed or password_hash_missing:
            entity.password_value = next_password
            entity.password_hash = hash_password(next_password) if next_password else ""
        if password_changed or enabled_changed or password_hash_missing:
            entity.session_version = int(entity.session_version or 1) + 1
        self.db.flush()
        return self._admin_response(entity)

    def public_status(self, token: str | None) -> SiteAccessStatusResponse:
        entity = self.ensure_settings()
        return SiteAccessStatusResponse(
            enabled=bool(entity.enabled),
            unlocked=self.is_unlocked(token, entity=entity),
            title=str(entity.title or ""),
            description=str(entity.description or ""),
        )

    def unlock(self, password: str) -> SiteAccessUnlockResult:
        entity = self.ensure_settings()
        max_age = int(settings.site_access_token_ttl_sec)
        if not bool(entity.enabled):
            return SiteAccessUnlockResult(SiteAccessUnlockResponse(ok=True, unlocked=True), None, max_age)
        if not verify_password(str(password or ""), str(entity.password_hash or "")):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный пароль")
        now = int(time.time())
        token = _encode_token(
            {
                "type": _SITE_ACCESS_TOKEN_TYPE,
                "ver": int(entity.session_version or 1),
                "iat": now,
                "exp": now + max_age,
                "jti": uuid.uuid4().hex,
            }
        )
        return SiteAccessUnlockResult(SiteAccessUnlockResponse(ok=True, unlocked=True), token, max_age)

    def is_unlocked(self, token: str | None, *, entity: SiteAccessSetting | None = None) -> bool:
        current = entity or self.ensure_settings()
        if not bool(current.enabled):
            return True
        payload = _decode_token(str(token or ""))
        if payload is None:
            return False
        return int(payload.get("ver") or 0) == int(current.session_version or 1)

    def require_unlocked(self, token: str | None) -> None:
        if not self.is_unlocked(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется пароль сайта")

    def is_current_hero_asset(self, asset_id: int) -> bool:
        entity = self.db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
        if entity is None:
            return False
        current_ids = {
            int(raw_id)
            for raw_id in (entity.desktop_hero_image_asset_id, entity.mobile_hero_image_asset_id)
            if raw_id is not None
        }
        return int(asset_id) in current_ids

    def require_media_access(self, asset_id: int, token: str | None) -> None:
        if self.is_current_hero_asset(asset_id):
            return
        self.require_unlocked(token)


def require_site_access(
    site_access_token: str | None = Cookie(default=None, alias=SITE_ACCESS_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> None:
    SiteAccessService(db).require_unlocked(site_access_token)
