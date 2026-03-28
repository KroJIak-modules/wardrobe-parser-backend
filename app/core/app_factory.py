"""FastAPI app factory and shared application wiring."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import IntegrityError, NotFoundError, ValidationError


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    def not_found_handler(request: object, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    def validation_handler(request: object, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(IntegrityError)
    def integrity_handler(request: object, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


def _configure_cors(app: FastAPI) -> None:
    allowed_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app() -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(title="Wardrobe Parser Backend API")

    @app.get("/health", summary="Health check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    _register_exception_handlers(app)
    _configure_cors(app)
    app.include_router(api_router)
    return app
