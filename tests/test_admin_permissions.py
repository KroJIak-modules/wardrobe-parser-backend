from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.schemas.auth import AdminRoleCreateRequest, AdminUserCreateRequest
from app.services.auth.admin_accounts_service import AdminAccountsService
from app.services.auth.admin_auth_service import AdminAuthContext, require_any_permission
from app.services.auth.permissions import ALL_PERMISSION_KEYS, PERMISSION_SCOPES


def _context(*permissions: str, is_superuser: bool = False) -> AdminAuthContext:
    user = SimpleNamespace(id=1, login="tester", is_superuser=is_superuser, role=None)
    claims = SimpleNamespace()
    return AdminAuthContext(claims=claims, user=user, permissions=frozenset(permissions))


def test_permission_catalog_contains_new_admin_scopes() -> None:
    scope_keys = {scope.key for scope in PERMISSION_SCOPES}
    assert {
        "control.payments",
        "control.content",
        "control.sync",
        "control.settings_transfer",
        "control.site_access",
    } <= scope_keys
    assert "accounts" not in scope_keys
    assert "control.payments.edit" in ALL_PERMISSION_KEYS
    assert "control.settings_transfer.read" in ALL_PERMISSION_KEYS


def test_require_any_permission_accepts_legacy_or_new_scope() -> None:
    dependency = require_any_permission("control.payments.read", "control.settings.read")

    assert dependency(_context("control.payments.read")).user.login == "tester"
    assert dependency(_context("control.settings.read")).user.login == "tester"
    assert dependency(_context(is_superuser=True)).user.login == "tester"

    with pytest.raises(HTTPException) as exc_info:
        dependency(_context("control.pricing.read"))
    assert exc_info.value.status_code == 403


class _DummyLimiter:
    def is_limited(self, _client_key: str) -> bool:
        return False

    def register_failed_attempt(self, _client_key: str) -> None:
        return None


def _client_for_permissions(monkeypatch, permissions: list[str]) -> TestClient:
    marker = uuid4().hex[:12]
    login = f"permissions-{marker}"
    password = "permissions-password"
    db = SessionLocal()
    try:
        service = AdminAccountsService(db)
        role = service.create_role(
            AdminRoleCreateRequest(name=f"permissions-role-{marker}", permissions=permissions)
        )
        service.create_user(
            AdminUserCreateRequest(
                login=login,
                password=password,
                role_id=role.id,
                is_active=True,
            )
        )
    finally:
        db.close()

    monkeypatch.setattr(auth_module, "_login_rate_limiter", _DummyLimiter())
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200
    return client


def test_new_read_scopes_authorize_their_admin_areas(monkeypatch) -> None:
    client = _client_for_permissions(
        monkeypatch,
        [
            "control.payments.read",
            "control.content.read",
            "control.sync.read",
            "control.settings_transfer.read",
            "control.site_access.read",
        ],
    )

    assert client.get("/api/v1/admin/payments").status_code == 200
    assert client.get("/api/v1/admin/site-content/about").status_code == 200
    assert client.get("/api/v1/jobs/latest").status_code == 200
    assert client.get("/api/v1/settings/export").status_code == 200
    assert client.get("/api/v1/admin/site-content/access").status_code == 200
    assert client.post("/api/v1/jobs", json={"triggered_by": "manual"}).status_code == 403
