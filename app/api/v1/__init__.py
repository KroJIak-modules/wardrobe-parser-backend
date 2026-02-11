from fastapi import APIRouter

from app.api.v1.parser import router as parser_router
from app.api.v1.products import router as products_router
from app.api.v1.sites import router as sites_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(sites_router, prefix="/sites", tags=["sites"])
api_router.include_router(products_router, prefix="/products", tags=["products"])
api_router.include_router(parser_router, prefix="/parser", tags=["parser"])
