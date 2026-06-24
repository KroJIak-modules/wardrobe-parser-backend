from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
import app.api.v1.sources as sources_module
from app.core.database import SessionLocal
from app.main import app
from app.models import ImageAsset, Source
from app.services.catalog.media_asset_service import MediaAssetService


class DummyLimiter:
    def __init__(self) -> None:
        self.failed: dict[str, int] = {}

    def is_limited(self, client_key: str) -> bool:
        return self.failed.get(client_key, 0) >= 2

    def register_failed_attempt(self, client_key: str) -> None:
        self.failed[client_key] = self.failed.get(client_key, 0) + 1


def _authorized_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert login.status_code == 200
    return client


def _create_source(db, *, key: str) -> Source:
    source = Source(
        key=key,
        name=key.replace("-", " ").title(),
        base_url=f"https://{key}.example.com",
        base_url_normalized=f"https://{key}.example.com",
        host_normalized=f"{key}.example.com",
    )
    db.add(source)
    db.flush()
    return source


def _unique_key(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def test_source_logo_upload_endpoint_returns_asset_id(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    monkeypatch.setattr(
        sources_module.MediaAssetService,
        "save_upload",
        lambda self, *, scope, upload: SimpleNamespace(id=654),
    )

    response = client.post(
        "/api/v1/sources/logo/upload",
        files={"file": ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml")},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "image_asset_id": 654}


def test_media_asset_service_detects_svg_mime_type_for_source_scope(monkeypatch, tmp_path) -> None:
    db = SessionLocal()
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")
    try:
        asset = MediaAssetService(db).save_bytes(
            scope="sources",
            file_name="logo.svg",
            content=b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'></svg>",
        )

        assert asset.mime_type == "image/svg+xml"
        assert asset.width_px is None
        assert asset.height_px is None
        assert MediaAssetService(db).resolve_file_path(asset).exists()
    finally:
        db.rollback()
        db.close()


def test_source_logo_patch_round_trips_in_sources_payload(monkeypatch, tmp_path) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")
    try:
        source = _create_source(db, key=_unique_key("logo-source"))
        svg_bytes = (
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
            f"<text x='1' y='8'>{uuid4().hex[:6]}</text>"
            f"</svg>"
        ).encode("utf-8")
        asset = MediaAssetService(db).save_bytes(scope="sources", file_name="logo.svg", content=svg_bytes)
        db.commit()

        patch_response = client.patch(
            f"/api/v1/sources/{source.key}/logo",
            json={"logo_image_asset_id": int(asset.id)},
        )
        assert patch_response.status_code == 200
        payload = patch_response.json()
        assert payload["logo_image_asset_id"] == int(asset.id)

        list_response = client.get("/api/v1/sources")
        assert list_response.status_code == 200
        listed = next(item for item in list_response.json() if item["key"] == source.key)
        assert listed["logo_image_asset_id"] == int(asset.id)

        image_response = client.get(f"/api/v1/sources/images/{int(asset.id)}")
        assert image_response.status_code == 200
        assert image_response.headers["content-type"] == "image/svg+xml"

        clear_response = client.patch(
            f"/api/v1/sources/{source.key}/logo",
            json={"logo_image_asset_id": None},
        )
        assert clear_response.status_code == 200
        assert clear_response.json()["logo_image_asset_id"] is None

        persisted = db.query(Source).filter(Source.id == int(source.id)).one()
        assert persisted.logo_image_asset_id is None
    finally:
        db.rollback()
        db.close()


def test_source_logo_patch_rejects_unknown_asset(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    try:
        source = _create_source(db, key=_unique_key("logo-source-invalid"))
        db.commit()

        response = client.patch(
            f"/api/v1/sources/{source.key}/logo",
            json={"logo_image_asset_id": 999999},
        )

        assert response.status_code == 400
        assert "Логотип не найден" in response.text
    finally:
        db.rollback()
        db.close()
