"""HTTP proxy helpers for image gateway responses."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime

import requests
from fastapi import HTTPException, status


def build_etag(image_id: int, created_at: datetime | None, source_url: str | None) -> str:
    created_ts = "0"
    if created_at is not None:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        created_ts = str(int(dt.timestamp()))
    src_len = str(len(source_url or ""))
    return f'W/"img-{image_id}-{created_ts}-{src_len}"'


def cache_headers(*, etag: str, created_at: datetime | None, max_age_sec: int) -> dict[str, str]:
    headers = {
        "Cache-Control": f"public, max-age={max_age_sec}",
        "ETag": etag,
    }
    if created_at is not None:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        headers["Last-Modified"] = format_datetime(dt, usegmt=True)
    return headers


def fetch_image_bytes(*, source_url: str, timeout_sec: float, max_bytes: int) -> tuple[bytes, str]:
    """Download image with content-type and size validation."""
    try:
        upstream = requests.get(source_url, timeout=timeout_sec, stream=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Image fetch failed: {exc}") from exc

    if upstream.status_code >= 400:
        upstream.close()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Upstream image is unavailable")

    content_type = upstream.headers.get("Content-Type", "image/jpeg")
    if not content_type.lower().startswith("image/"):
        upstream.close()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Upstream is not an image")

    content_length_header = upstream.headers.get("Content-Length")
    if content_length_header is not None:
        try:
            content_length = int(content_length_header)
        except ValueError:
            content_length = -1
        if content_length > max_bytes:
            upstream.close()
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Image payload is too large",
            )

    chunks: list[bytes] = []
    total_size = 0
    try:
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total_size += len(chunk)
            if total_size > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Image payload is too large",
                )
            chunks.append(chunk)
    finally:
        upstream.close()

    media_type = upstream.headers.get("Content-Type", "image/jpeg")
    return b"".join(chunks), media_type
