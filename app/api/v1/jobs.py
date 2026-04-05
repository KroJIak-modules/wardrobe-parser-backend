"""Proxy jobs endpoints to service API."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.services.proxy.service_api_proxy import forward_service_request


router = APIRouter(tags=["jobs"])
_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@router.api_route("/jobs", methods=_PROXY_METHODS)
async def proxy_jobs_root(request: Request) -> Response:
    body = await request.body()
    return forward_service_request(request=request, path="jobs", body=body)


@router.api_route("/jobs/{path:path}", methods=_PROXY_METHODS)
async def proxy_jobs_path(path: str, request: Request) -> Response:
    body = await request.body()
    return forward_service_request(request=request, path=f"jobs/{path}", body=body)

