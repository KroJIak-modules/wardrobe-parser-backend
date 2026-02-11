from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import IntegrityError, NotFoundError, ValidationError

app = FastAPI(title="... API")


@app.get("/health", summary="Health check")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(NotFoundError)
def not_found_handler(request: object, exc: NotFoundError) -> None:
    raise HTTPException(status_code=404, detail=str(exc))


@app.exception_handler(ValidationError)
def validation_handler(request: object, exc: ValidationError) -> None:
    raise HTTPException(status_code=400, detail=str(exc))


@app.exception_handler(IntegrityError)
def integrity_handler(request: object, exc: IntegrityError) -> None:
    raise HTTPException(status_code=409, detail=str(exc))

_local_origins = [
    "http://...",
    "http://..."
]

_extra_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_local_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
