"""FastAPI app factory and shared application wiring."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette import status

from app.core.config import settings
from app.core.exceptions import IntegrityError, NotFoundError, ValidationError


HTTP_NOT_FOUND = status.HTTP_404_NOT_FOUND
HTTP_BAD_REQUEST = status.HTTP_400_BAD_REQUEST
HTTP_CONFLICT = status.HTTP_409_CONFLICT


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
    allow_methods = [item.strip() for item in settings.cors_allow_methods.split(",") if item.strip()]
    allow_headers = [item.strip() for item in settings.cors_allow_headers.split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=allow_methods or ["*"],
        allow_headers=allow_headers or ["*"],
    )


def create_app() -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(title=settings.app_title)

    @app.get("/health", summary="Health check")
    def health() -> dict[str, str]:
        return {"status": settings.health_status_value}

    _register_exception_handlers(app)
    _configure_cors(app)
    return app
