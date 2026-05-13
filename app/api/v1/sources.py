"""Proxy neutral sources endpoints to parser-service /sync/sources.

Keeps admin UI response contract stable.
"""

from __future__ import annotations

import requests
from fastapi import APIRouter, HTTPException, Request, Response, status

from app.services.proxy.service_api_proxy import forward_service_request
from app.core.config import settings


router = APIRouter(tags=["sources"])
_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _service_sources_base() -> str:
    return f"{settings.service_base_url.rstrip('/')}/api/v1/sync/sources"


def _map_source(item: dict) -> dict:
    return {
        "key": str(item.get("key") or ""),
        "source_id": int(item.get("id") or 0),
        "name": str(item.get("key") or ""),
        "base_url": str(item.get("url") or ""),
        "parser_type": "parser",
        "enabled": bool(item.get("enabled", True)),
        "sync_enabled": bool(item.get("sync_enabled", True)),
        "hide_auto_added_products": False,
        "notes": None,
        "status_label": None,
        "products_count": 0,
        "categories_count": 0,
        "last_sync_at": None,
        "last_sync_duration_sec": None,
        "last_sync_status": None,
        "supplier_id": None,
        "supplier_key": None,
        "supplier_name": None,
        "promo_factor": 1.0,
        "promo_only_no_discount": False,
        "buyout_surcharge_value": 0.0,
        "buyout_surcharge_currency": "RUB",
    }


@router.api_route("/sources", methods=_PROXY_METHODS)
async def proxy_sources_root(request: Request) -> Response:
    if request.method.upper() == "GET":
        try:
            upstream = requests.get(_service_sources_base(), timeout=(5, 30))
            upstream.raise_for_status()
            payload = upstream.json()
            mapped = [_map_source(item) for item in payload] if isinstance(payload, list) else []
        except requests.RequestException as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Service API unavailable: {exc}") from exc
        return Response(
            content=__import__("json").dumps(mapped, ensure_ascii=False),
            status_code=200,
            media_type="application/json",
        )
    body = await request.body()
    return forward_service_request(request=request, path="sync/sources", body=body)


@router.api_route("/sources/{path:path}", methods=_PROXY_METHODS)
async def proxy_sources_path(path: str, request: Request) -> Response:
    if request.method.upper() == "PATCH":
        body = await request.body()
        return forward_service_request(request=request, path=f"sync/sources/{path}", body=body)
    body = await request.body()
    return forward_service_request(request=request, path=f"sync/sources/{path}", body=body)
