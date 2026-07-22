from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import ImageAsset, SiteNotificationSetting
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


def test_site_notification_create_public_reset_and_delete_roundtrip(monkeypatch, tmp_path) -> None:
    client = _authorized_client(monkeypatch)
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")

    marker = uuid4().hex[:8]
    upload_response = client.post(
        "/api/v1/admin/site-content/media/upload",
        files={
            "file": (
                f"{marker}.svg",
                f"<svg xmlns='http://www.w3.org/2000/svg'><text>{marker}</text></svg>".encode("utf-8"),
                "image/svg+xml",
            )
        },
    )
    assert upload_response.status_code == 200
    asset_id = int(upload_response.json()["asset"]["id"])
    notification_id: int | None = None
    try:
        empty_public_response = client.get("/api/v1/site/home/notification")
        assert empty_public_response.status_code == 200

        create_response = client.post(
            "/api/v1/admin/site-content/notification",
            json={
                "title": f"TITLE {marker}",
                "description": f"DESCRIPTION {marker}",
                "button_text": "OPEN",
                "button_url": "https://example.com/notification",
                "image_asset_id": asset_id,
            },
        )
        assert create_response.status_code == 200
        create_payload = create_response.json()
        assert len(create_payload["items"]) >= 1
        created = create_payload["items"][0]
        notification_id = int(created["id"])
        assert created["title"] == f"TITLE {marker}"
        assert created["image"]["id"] == asset_id
        assert created["image"]["url"] == f"/api/v1/admin/site-content/media/{asset_id}/file"
        media_response = client.get(created["image"]["url"])
        assert media_response.status_code == 200
        assert int(created["version"]) == 1

        public_response = client.get("/api/v1/site/home/notification")
        assert public_response.status_code == 200
        public_payload = public_response.json()
        assert public_payload["enabled"] is True
        assert public_payload["id"] == f"telegram-updates:{notification_id}"
        assert public_payload["title"] == f"TITLE {marker}"
        assert public_payload["description"] == f"DESCRIPTION {marker}"
        assert public_payload["cta_label"] == "OPEN"
        assert public_payload["cta_href"] == "https://example.com/notification"
        assert public_payload["image_src"] == f"/api/v1/site/media/{asset_id}/file"

        reset_response = client.post(f"/api/v1/admin/site-content/notification/{notification_id}/reset")
        assert reset_response.status_code == 200
        reset_item = next(item for item in reset_response.json()["items"] if int(item["id"]) == notification_id)
        assert int(reset_item["version"]) == 2

        delete_response = client.delete(f"/api/v1/admin/site-content/notification/{notification_id}")
        assert delete_response.status_code == 200
        assert all(int(item["id"]) != notification_id for item in delete_response.json()["items"])
    finally:
        db = SessionLocal()
        try:
            if notification_id is not None:
                db.query(SiteNotificationSetting).filter(SiteNotificationSetting.id == notification_id).delete(synchronize_session=False)
            asset = db.query(ImageAsset).filter(ImageAsset.id == asset_id).one_or_none()
            if asset is not None:
                file_path = MediaAssetService(db).resolve_file_path(asset)
                if file_path.exists():
                    file_path.unlink()
                db.delete(asset)
            db.commit()
        finally:
            db.close()
