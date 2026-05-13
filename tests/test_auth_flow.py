from fastapi.testclient import TestClient

from app.main import app
import app.api.v1.auth as auth_module


class DummyLimiter:
    def __init__(self) -> None:
        self.failed: dict[str, int] = {}

    def is_limited(self, client_key: str) -> bool:
        return self.failed.get(client_key, 0) >= 2

    def register_failed_attempt(self, client_key: str) -> None:
        self.failed[client_key] = self.failed.get(client_key, 0) + 1


def test_login_sets_auth_cookies(monkeypatch):
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)

    res = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})

    assert res.status_code == 200
    assert "admin_access_token" in res.cookies
    assert "admin_refresh_token" in res.cookies
    payload = res.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_expires_in"] > 0
    assert payload["refresh_expires_in"] > 0


def test_refresh_without_body_works_with_cookie(monkeypatch):
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)

    login = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert login.status_code == 200

    refresh = client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 200
    assert "admin_access_token" in refresh.cookies
    assert "admin_refresh_token" in refresh.cookies


def test_logout_clears_cookies(monkeypatch):
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)

    login = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert login.status_code == 200

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    set_cookie = "\n".join(logout.headers.get_list("set-cookie"))
    assert "admin_access_token=" in set_cookie
    assert "admin_refresh_token=" in set_cookie


def test_login_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)

    bad1 = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "wrong"})
    bad2 = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "wrong"})
    blocked = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "wrong"})

    assert bad1.status_code == 401
    assert bad2.status_code == 401
    assert blocked.status_code == 429
