from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import ImageAsset, ShowcaseCarouselImage, ShowcaseSetting
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


def _upload_svg(client: TestClient, *, label: str) -> int:
    response = client.post(
        "/api/v1/showcase/media/upload",
        files={
            "file": (
                f"{label}.svg",
                f"<svg xmlns='http://www.w3.org/2000/svg'><text>{label}</text></svg>".encode("utf-8"),
                "image/svg+xml",
            )
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    return int(payload["asset"]["id"])


def test_showcase_state_roundtrip_supports_desktop_and_mobile_media(monkeypatch, tmp_path) -> None:
    client = _authorized_client(monkeypatch)
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")
    created_asset_ids: list[int] = []
    try:
        marker = uuid4().hex[:8]
        desktop_hero_id = _upload_svg(client, label=f"{marker}-desktop-hero")
        desktop_slide_id = _upload_svg(client, label=f"{marker}-desktop-slide")
        mobile_hero_id = _upload_svg(client, label=f"{marker}-mobile-hero")
        mobile_slide_id = _upload_svg(client, label=f"{marker}-mobile-slide")
        created_asset_ids.extend([desktop_hero_id, desktop_slide_id, mobile_hero_id, mobile_slide_id])

        update_response = client.put(
            "/api/v1/showcase/state",
            json={
                "desktop": {
                    "hero_asset_id": desktop_hero_id,
                    "carousel_asset_ids": [desktop_slide_id],
                },
                "mobile": {
                    "hero_asset_id": mobile_hero_id,
                    "carousel_asset_ids": [mobile_slide_id],
                },
            },
        )

        assert update_response.status_code == 200
        update_payload = update_response.json()
        assert update_payload["desktop"]["hero_asset"]["id"] == desktop_hero_id
        assert [item["id"] for item in update_payload["desktop"]["carousel_assets"]] == [desktop_slide_id]
        assert update_payload["mobile"]["hero_asset"]["id"] == mobile_hero_id
        assert [item["id"] for item in update_payload["mobile"]["carousel_assets"]] == [mobile_slide_id]
        assert update_payload["carousel_limit"] == 20

        state_response = client.get("/api/v1/showcase/state")
        assert state_response.status_code == 200
        state_payload = state_response.json()
        assert state_payload == update_payload

        file_response = client.get(f"/api/v1/showcase/media/{desktop_hero_id}/file")
        assert file_response.status_code == 200
        assert file_response.headers["content-type"] == "image/svg+xml"
    finally:
        db = SessionLocal()
        try:
            db.query(ShowcaseCarouselImage).delete(synchronize_session=False)
            settings = db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
            if settings is not None:
                settings.desktop_hero_image_asset_id = None
                settings.mobile_hero_image_asset_id = None
            if created_asset_ids:
                db.query(ImageAsset).filter(ImageAsset.id.in_(created_asset_ids)).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


def test_showcase_state_rejects_unknown_asset(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.put(
        "/api/v1/showcase/state",
        json={
            "desktop": {
                "hero_asset_id": 999999,
                "carousel_asset_ids": [],
            },
            "mobile": {
                "hero_asset_id": None,
                "carousel_asset_ids": [],
            },
        },
    )

    assert response.status_code == 400
    assert "Медиафайл витрины не найден" in response.text


def test_showcase_media_endpoints_reject_non_showcase_assets(monkeypatch, tmp_path) -> None:
    client = _authorized_client(monkeypatch)
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")

    db = SessionLocal()
    try:
        asset = MediaAssetService(db).save_bytes(
            scope="sources",
            file_name="source-logo.svg",
            content=b"<svg xmlns='http://www.w3.org/2000/svg'><text>source</text></svg>",
        )
        asset_id = int(asset.id)
        db.commit()
    finally:
        db.close()

    try:
        file_response = client.get(f"/api/v1/showcase/media/{asset_id}/file")
        assert file_response.status_code == 404
        assert "Медиафайл витрины не найден" in file_response.text

        update_response = client.put(
            "/api/v1/showcase/state",
            json={
                "desktop": {
                    "hero_asset_id": asset_id,
                    "carousel_asset_ids": [],
                },
                "mobile": {
                    "hero_asset_id": None,
                    "carousel_asset_ids": [],
                },
            },
        )
        assert update_response.status_code == 400
        assert "Медиафайл витрины не найден" in update_response.text
    finally:
        db = SessionLocal()
        try:
            persisted_asset = db.query(ImageAsset).filter(ImageAsset.id == asset_id).one_or_none()
            if persisted_asset is not None:
                file_path = MediaAssetService(db).resolve_file_path(persisted_asset)
                if file_path.exists():
                    file_path.unlink()
                db.delete(persisted_asset)
                db.commit()
        finally:
            db.close()


def test_showcase_upload_creates_scope_specific_asset_for_existing_product_image(monkeypatch, tmp_path) -> None:
    client = _authorized_client(monkeypatch)
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")

    product_asset_id: int | None = None
    showcase_asset_id: int | None = None
    image_bytes = b"<svg xmlns='http://www.w3.org/2000/svg'><text>shared-image</text></svg>"

    db = SessionLocal()
    try:
        product_asset = MediaAssetService(db).save_bytes(
            scope="products",
            file_name="shared-image.svg",
            content=image_bytes,
        )
        product_asset_id = int(product_asset.id)
        db.commit()
    finally:
        db.close()

    try:
        response = client.post(
            "/api/v1/showcase/media/upload",
            files={"file": ("shared-image.svg", image_bytes, "image/svg+xml")},
        )
        assert response.status_code == 200
        payload = response.json()
        showcase_asset_id = int(payload["asset"]["id"])
        assert showcase_asset_id != product_asset_id

        db = SessionLocal()
        try:
            product_asset = db.query(ImageAsset).filter(ImageAsset.id == product_asset_id).one()
            showcase_asset = db.query(ImageAsset).filter(ImageAsset.id == showcase_asset_id).one()
            assert product_asset.scope == "products"
            assert showcase_asset.scope == "showcase"
            assert product_asset.checksum_sha256 == showcase_asset.checksum_sha256
        finally:
            db.close()

        update_response = client.put(
            "/api/v1/showcase/state",
            json={
                "desktop": {
                    "hero_asset_id": showcase_asset_id,
                    "carousel_asset_ids": [],
                },
                "mobile": {
                    "hero_asset_id": None,
                    "carousel_asset_ids": [],
                },
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["desktop"]["hero_asset"]["id"] == showcase_asset_id
    finally:
        db = SessionLocal()
        try:
            db.query(ShowcaseCarouselImage).delete(synchronize_session=False)
            settings = db.query(ShowcaseSetting).order_by(ShowcaseSetting.id.asc()).first()
            if settings is not None:
                if product_asset_id is not None and int(settings.desktop_hero_image_asset_id or 0) == product_asset_id:
                    settings.desktop_hero_image_asset_id = None
                if showcase_asset_id is not None and int(settings.desktop_hero_image_asset_id or 0) == showcase_asset_id:
                    settings.desktop_hero_image_asset_id = None
                if product_asset_id is not None and int(settings.mobile_hero_image_asset_id or 0) == product_asset_id:
                    settings.mobile_hero_image_asset_id = None
                if showcase_asset_id is not None and int(settings.mobile_hero_image_asset_id or 0) == showcase_asset_id:
                    settings.mobile_hero_image_asset_id = None
            for asset_id in [product_asset_id, showcase_asset_id]:
                if asset_id is None:
                    continue
                persisted_asset = db.query(ImageAsset).filter(ImageAsset.id == asset_id).one_or_none()
                if persisted_asset is None:
                    continue
                file_path = MediaAssetService(db).resolve_file_path(persisted_asset)
                if file_path.exists():
                    file_path.unlink()
                db.delete(persisted_asset)
            db.commit()
        finally:
            db.close()
