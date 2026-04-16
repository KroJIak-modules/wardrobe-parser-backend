"""Image gateway service that serves images directly from source URLs."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.image_asset import ImageAsset
from app.services.media.image_cache import get_image_cache
from app.services.media.image_proxy import build_etag, cache_headers, fetch_image_bytes
from app.services.media.image_security import check_rate_limit, ensure_allowed_url


class ImageGatewayService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _parse_resize_params(request: Request) -> tuple[int | None, int | None, int]:
        def _parse_int(raw: str | None, *, min_value: int, max_value: int) -> int | None:
            if raw is None:
                return None
            value = str(raw).strip()
            if not value:
                return None
            try:
                parsed = int(value)
            except ValueError:
                return None
            return max(min_value, min(max_value, parsed))

        width = _parse_int(request.query_params.get("w"), min_value=16, max_value=2400)
        height = _parse_int(request.query_params.get("h"), min_value=16, max_value=2400)
        quality = _parse_int(request.query_params.get("q"), min_value=25, max_value=95) or 65
        return width, height, quality

    @staticmethod
    def _transform_image(
        content: bytes,
        *,
        width: int | None,
        height: int | None,
        quality: int,
        fallback_media_type: str,
    ) -> tuple[bytes, str]:
        if width is None and height is None:
            return content, fallback_media_type
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                source = image.convert("RGB")
                src_w, src_h = source.size
                if src_w <= 0 or src_h <= 0:
                    return content, fallback_media_type
                target_w = width or int(round((height or src_h) * (src_w / src_h)))
                target_h = height or int(round((width or src_w) * (src_h / src_w)))
                target_w = max(16, min(2400, target_w))
                target_h = max(16, min(2400, target_h))
                if width is not None and height is not None:
                    resized = ImageOps.fit(
                        source,
                        (target_w, target_h),
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.5),
                    )
                else:
                    resized = source.resize((target_w, target_h), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                resized.save(output, format="WEBP", quality=quality, method=5)
                return output.getvalue(), "image/webp"
        except (UnidentifiedImageError, OSError, ValueError):
            return content, fallback_media_type

    def get_image(self, image_id: int, request: Request) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        check_rate_limit(client_ip, per_minute_limit=settings.image_rate_limit_per_minute)
        width, height, quality = self._parse_resize_params(request)

        asset = (
            self.db.query(ImageAsset)
            .filter(ImageAsset.id == image_id, ImageAsset.deleted_at.is_(None))
            .first()
        )
        if not asset:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

        base_etag = build_etag(asset.id, asset.created_at, asset.source_url)
        variant_etag = f'{base_etag}:w{width or 0}:h{height or 0}:q{quality}'
        headers = cache_headers(
            etag=variant_etag,
            created_at=asset.created_at,
            max_age_sec=settings.image_cache_max_age_sec,
        )
        if request.headers.get("if-none-match") == variant_etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

        if asset.storage_mode == "stored_file" and asset.stored_path:
            candidate = Path(asset.stored_path)
            if candidate.exists() and candidate.is_file():
                if width is not None or height is not None:
                    try:
                        transformed, media_type = self._transform_image(
                            candidate.read_bytes(),
                            width=width,
                            height=height,
                            quality=quality,
                            fallback_media_type="image/jpeg",
                        )
                        return Response(content=transformed, media_type=media_type, headers=headers)
                    except OSError:
                        pass
                return FileResponse(candidate, headers=headers)

        if not asset.source_url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image source URL is missing")

        normalized_source_url = ensure_allowed_url(asset.source_url)
        image_cache = get_image_cache()
        cached = image_cache.get(image_id=asset.id, etag=variant_etag)
        if cached is not None:
            return Response(content=cached.body, media_type=cached.content_type, headers=headers)

        content, media_type = fetch_image_bytes(
            source_url=normalized_source_url,
            timeout_sec=settings.image_proxy_timeout_sec,
            max_bytes=settings.image_proxy_max_bytes,
        )
        transformed_content, transformed_media_type = self._transform_image(
            content,
            width=width,
            height=height,
            quality=quality,
            fallback_media_type=media_type,
        )
        final_media_type = transformed_media_type or media_type
        image_cache.set(image_id=asset.id, etag=variant_etag, body=transformed_content, content_type=final_media_type)
        return Response(content=transformed_content, media_type=final_media_type, headers=headers)
