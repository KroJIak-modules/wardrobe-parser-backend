"""Image gateway service that serves images directly from source URLs."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.image_asset import ImageAsset
from app.services.media.image_proxy import build_etag, cache_headers
from app.services.media.image_security import check_rate_limit, ensure_allowed_url


class ImageGatewayService:
    def __init__(self, db: Session):
        self.db = db

    def get_image(self, image_id: int, request: Request) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        check_rate_limit(client_ip, per_minute_limit=settings.image_rate_limit_per_minute)

        asset = (
            self.db.query(ImageAsset)
            .filter(ImageAsset.id == image_id, ImageAsset.deleted_at.is_(None))
            .first()
        )
        if not asset:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

        etag = build_etag(asset.id, asset.created_at, asset.source_url)
        headers = cache_headers(
            etag=etag,
            created_at=asset.created_at,
            max_age_sec=settings.image_cache_max_age_sec,
        )
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

        if asset.storage_mode == "stored_file" and asset.stored_path:
            candidate = Path(asset.stored_path)
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate, headers=headers)

        if not asset.source_url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image source URL is missing")

        normalized_source_url = ensure_allowed_url(asset.source_url)
        # For remote assets use redirect instead of server-side byte proxying.
        # This avoids backend egress bottlenecks and lets CDN delivery work directly in the client.
        return RedirectResponse(url=normalized_source_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers=headers)
