from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.api.v1.site import _media_file_response
from app.api.v1.site import router as site_router
from app.core.database import SessionLocal, get_db
from app.models import ImageAsset
from app.services.catalog.media_asset_service import MediaAssetService


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(site_router)
    app.dependency_overrides[get_db] = lambda: SessionLocal()
    return TestClient(app)


def _create_asset(name: str = "accel-asset.png") -> int:
    db = SessionLocal()
    try:
        service = MediaAssetService(db)
        content = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        asset = service.save_bytes(scope="showcase", file_name=name, content=content)
        db.commit()
        return int(asset.id)
    finally:
        db.close()


def test_media_file_is_handed_to_nginx_when_request_came_through_it() -> None:
    """Через nginx файл отдаёт сам nginx, а не приложение."""
    asset_id = _create_asset()

    client = _client()
    response = client.get(
        f"/site/media/{asset_id}/file",
        headers={"X-Accel-Enabled": "1"},
    )

    assert response.status_code == 200
    assert response.headers.get("x-accel-redirect", "").startswith("/internal/site-media/")
    assert response.content == b""


def test_media_file_is_served_by_the_app_without_nginx() -> None:
    """Без nginx приложение отдаёт файл само."""
    asset_id = _create_asset("accel-direct.png")

    db = SessionLocal()
    try:
        asset = db.query(ImageAsset).filter(ImageAsset.id == asset_id).one()
        path = MediaAssetService(db).resolve_file_path(asset)
        response = _media_file_response(
            path=path,
            mime_type=str(asset.mime_type or "image/png"),
            via_nginx=False,
        )
    finally:
        db.close()

    assert isinstance(response, FileResponse)
    assert "x-accel-redirect" not in response.headers
    # Фото правят в админке: неделя в кэше браузера — слишком долго.
    assert response.headers.get("cache-control") == "private, max-age=300, must-revalidate"
    assert response.headers.get("etag")
    assert response.headers.get("last-modified")


def test_internal_media_path_stays_inside_uploads() -> None:
    """Путь для nginx строится только для файлов внутри каталога загрузок."""
    inside = MediaAssetService.internal_media_path(
        MediaAssetService._ROOT_DIR / ".cache" / "site-media" / "1-w960-q82.webp"
    )
    outside = MediaAssetService.internal_media_path(Path("/etc/passwd"))

    assert inside == "/internal/site-media/.cache/site-media/1-w960-q82.webp"
    assert outside is None


def test_media_file_answers_304_when_file_did_not_change() -> None:
    """Условный запрос: файл не менялся — отдаём 304 без тела."""
    asset_id = _create_asset("accel-conditional.png")
    client = _client()

    first = client.get(f"/site/media/{asset_id}/file")
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag

    second = client.get(f"/site/media/{asset_id}/file", headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers.get("etag") == etag


def test_media_file_answers_304_for_if_modified_since() -> None:
    """Тот же условный запрос, но по дате изменения."""
    asset_id = _create_asset("accel-modified-since.png")
    client = _client()

    first = client.get(f"/site/media/{asset_id}/file")
    assert first.status_code == 200
    last_modified = first.headers.get("last-modified")
    assert last_modified

    second = client.get(
        f"/site/media/{asset_id}/file",
        headers={"If-Modified-Since": last_modified},
    )

    assert second.status_code == 304
    assert second.content == b""
