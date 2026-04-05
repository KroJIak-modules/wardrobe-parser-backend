"""Forward selected backend API requests to service API."""

from __future__ import annotations

from urllib.parse import urlencode

import requests
from fastapi import HTTPException, Request, Response, status

from app.core.config import settings


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "date",
    "server",
}


def _target_url(path: str, query_items: list[tuple[str, str]]) -> str:
    cleaned_base = settings.service_base_url.rstrip("/")
    cleaned_path = path.lstrip("/")
    base = f"{cleaned_base}/api/v1"
    if cleaned_path:
        base = f"{base}/{cleaned_path}"
    if not query_items:
        return base
    query = urlencode(query_items, doseq=True)
    return f"{base}?{query}"


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP_BY_HOP_HEADERS:
            continue
        headers[key] = value
    return headers


def _response_headers(upstream: requests.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        if key.lower() in _HOP_BY_HOP_HEADERS:
            continue
        headers[key] = value
    return headers


def forward_service_request(request: Request, path: str, body: bytes) -> Response:
    target = _target_url(path=path, query_items=list(request.query_params.multi_items()))
    try:
        upstream = requests.request(
            method=request.method,
            url=target,
            data=body if body else None,
            headers=_forward_headers(request),
            timeout=(
                settings.service_proxy_connect_timeout_sec,
                settings.service_proxy_read_timeout_sec,
            ),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Service API unavailable: {exc}",
        ) from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
    )

