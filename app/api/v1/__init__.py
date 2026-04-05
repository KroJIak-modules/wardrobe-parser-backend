from fastapi import APIRouter

from app.api.v1.categories import router as categories_router
from app.api.v1.dedup import router as dedup_router
from app.api.v1.images import router as images_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.products import router as products_router
from app.api.v1.settings import router as settings_router
from app.api.v1.shopify import router as shopify_router


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(categories_router)
api_router.include_router(dedup_router)
api_router.include_router(settings_router)
api_router.include_router(images_router)
api_router.include_router(jobs_router)
api_router.include_router(products_router)
api_router.include_router(shopify_router)
