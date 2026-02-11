from fastapi import APIRouter

from app.api.v1 import ..., ...

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(...router, prefix="/...", tags=["..."])
api_router.include_router(...router, prefix="/...", tags=["..."])
