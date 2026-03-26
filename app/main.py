from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import IntegrityError, NotFoundError, ValidationError

app = FastAPI(title="Wardrobe Parser Backend API")


@app.get("/health", summary="Health check")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(NotFoundError)
def not_found_handler(request: object, exc: NotFoundError) -> None:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
def validation_handler(request: object, exc: ValidationError) -> None:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(IntegrityError)
def integrity_handler(request: object, exc: IntegrityError) -> None:
    return JSONResponse(status_code=409, content={"detail": str(exc)})

_allowed_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
