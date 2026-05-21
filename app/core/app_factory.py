"""FastAPI app factory and shared application wiring."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette import status

from app.api.v1 import api_router
from app.api.v1.jobs import mark_interrupted_jobs_on_startup
from app.core.database import SessionLocal
from app.core.config import settings
from app.core.exceptions import IntegrityError, NotFoundError, ValidationError
from app.services.auth.admin_accounts_service import AdminAccountsService


HTTP_NOT_FOUND = status.HTTP_404_NOT_FOUND
HTTP_BAD_REQUEST = status.HTTP_400_BAD_REQUEST
HTTP_CONFLICT = status.HTTP_409_CONFLICT
_DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
_PUBLIC_OPENAPI_PATHS = {
    "/health",
    "/api/v1/catalog/categories/roots",
    "/api/v1/catalog/categories/root/{root_slug}",
    "/api/v1/catalog/products",
    "/api/v1/products/{product_id}",
    "/api/v1/products/images/{image_id}",
    "/api/v1/showcase/state",
    "/api/v1/showcase/hero/image",
    "/api/v1/showcase/carousel",
    "/api/v1/showcase/carousel/{image_id}/image",
}
_PUBLIC_OPENAPI_OPERATIONS = {
    "/health": {"get"},
    "/api/v1/catalog/categories/roots": {"get"},
    "/api/v1/catalog/categories/root/{root_slug}": {"get"},
    "/api/v1/catalog/products": {"get"},
    "/api/v1/products/{product_id}": {"get"},
    "/api/v1/products/images/{image_id}": {"get"},
    "/api/v1/showcase/state": {"get"},
    "/api/v1/showcase/hero/image": {"get"},
    "/api/v1/showcase/carousel": {"get"},
    "/api/v1/showcase/carousel/{image_id}/image": {"get"},
}


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    def not_found_handler(request: object, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=HTTP_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    def validation_handler(request: object, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=HTTP_BAD_REQUEST, content={"detail": str(exc)})

    @app.exception_handler(IntegrityError)
    def integrity_handler(request: object, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=HTTP_CONFLICT, content={"detail": str(exc)})


def _configure_cors(app: FastAPI) -> None:
    allowed_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _build_public_openapi(app: FastAPI) -> dict[str, Any]:
    if getattr(app.state, "public_openapi_schema", None):
        return app.state.public_openapi_schema
    schema = get_openapi(
        title="Wardrobe Public API",
        version="1.0.0",
        description=(
            "Публичный API для пользовательской витрины (категории, каталог, карточка товара, "
            "медиа витрины и внутренние изображения товаров). Без админ-методов."
        ),
        routes=app.routes,
    )
    filtered_paths: dict[str, Any] = {}
    for path, path_item in (schema.get("paths") or {}).items():
        if path not in _PUBLIC_OPENAPI_PATHS:
            continue
        allowed_methods = _PUBLIC_OPENAPI_OPERATIONS.get(path, {"get"})
        filtered_operations = {
            method: operation
            for method, operation in path_item.items()
            if method.lower() in allowed_methods
        }
        if filtered_operations:
            filtered_paths[path] = filtered_operations
    schema["paths"] = filtered_paths
    # Keep only component schemas actually referenced by public operations.
    components = schema.get("components") or {}
    schemas = components.get("schemas") or {}
    ref_pattern = re.compile(r"^#/components/schemas/(?P<name>[A-Za-z0-9_.-]+)$")

    def _collect_refs(value: Any, out: set[str]) -> None:
        if isinstance(value, dict):
            raw_ref = value.get("$ref")
            if isinstance(raw_ref, str):
                match = ref_pattern.match(raw_ref.strip())
                if match:
                    out.add(match.group("name"))
            for child in value.values():
                _collect_refs(child, out)
        elif isinstance(value, list):
            for child in value:
                _collect_refs(child, out)

    used_schema_names: set[str] = set()
    _collect_refs(schema["paths"], used_schema_names)
    resolved: set[str] = set()
    queue = list(used_schema_names)
    while queue:
        name = queue.pop()
        if name in resolved:
            continue
        resolved.add(name)
        candidate = schemas.get(name)
        if candidate is None:
            continue
        nested_refs: set[str] = set()
        _collect_refs(candidate, nested_refs)
        for nested in nested_refs:
            if nested not in resolved:
                queue.append(nested)
    if schemas:
        components["schemas"] = {name: schemas[name] for name in sorted(resolved) if name in schemas}
    if components:
        schema["components"] = components

    used_tags = {
        tag
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        for tag in operation.get("tags", [])
    }
    schema["tags"] = [tag for tag in (schema.get("tags") or []) if tag.get("name") in used_tags]
    app.state.public_openapi_schema = schema
    return schema


def create_app() -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(
        title="Wardrobe Parser Backend API",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.on_event("startup")
    def _on_startup_sync_runtime() -> None:
        db = SessionLocal()
        try:
            AdminAccountsService(db).ensure_superadmin_user()
        finally:
            db.close()
        mark_interrupted_jobs_on_startup()

    @app.get("/health", summary="Health check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)

    @app.get("/api/openapi/public.json", include_in_schema=False)
    def get_public_openapi() -> dict[str, Any]:
        return _build_public_openapi(app)

    @app.get("/api/docs/public", include_in_schema=False)
    def get_public_docs():
        return get_swagger_ui_html(
            openapi_url="/api/openapi/public.json",
            title="Wardrobe Public API - Swagger UI",
        )

    @app.get("/api/redoc/public", include_in_schema=False)
    def get_public_redoc():
        return get_redoc_html(
            openapi_url="/api/openapi/public.json",
            title="Wardrobe Public API - ReDoc",
        )

    @app.get("/api/openapi/showcase.json", include_in_schema=False)
    def get_showcase_openapi_compat() -> dict[str, Any]:
        return _build_public_openapi(app)

    @app.get("/api/docs/showcase", include_in_schema=False)
    def get_showcase_docs_compat():
        return get_swagger_ui_html(
            openapi_url="/api/openapi/public.json",
            title="Wardrobe Public API - Swagger UI",
        )

    @app.get("/api/redoc/showcase", include_in_schema=False)
    def get_showcase_redoc_compat():
        return get_redoc_html(
            openapi_url="/api/openapi/public.json",
            title="Wardrobe Public API - ReDoc",
        )

    @app.get("/api/docs/public.md", include_in_schema=False)
    def download_public_markdown() -> FileResponse:
        return FileResponse(
            _DOCS_DIR / "showcase-api.md",
            media_type="text/markdown",
            filename="public-api.md",
        )

    _register_exception_handlers(app)
    _configure_cors(app)
    return app
