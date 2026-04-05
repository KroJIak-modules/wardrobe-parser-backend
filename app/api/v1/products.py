"""Proxy product endpoints to service API."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.services.proxy.service_api_proxy import forward_service_request


router = APIRouter(tags=["products"])
_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@router.api_route("/products", methods=_PROXY_METHODS)
async def proxy_products_root(request: Request) -> Response:
    body = await request.body()
    return forward_service_request(request=request, path="products", body=body)


@router.api_route("/products/{path:path}", methods=_PROXY_METHODS)
async def proxy_products_path(path: str, request: Request) -> Response:
    body = await request.body()
    return forward_service_request(request=request, path=f"products/{path}", body=body)

