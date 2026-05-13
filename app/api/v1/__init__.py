from fastapi import APIRouter, Depends

from app.api.v1.auth import router as auth_router
from app.api.v1.categories import router as categories_router
from app.api.v1.dedup import router as dedup_router
from app.api.v1.images import router as images_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.products import router as products_router
from app.api.v1.public_parser_contract import router as public_parser_contract_router
from app.api.v1.settings import router as settings_router
from app.api.v1.sources import router as sources_router
from app.services.auth.admin_auth_service import require_admin_access


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(categories_router)
api_router.include_router(dedup_router, dependencies=[Depends(require_admin_access)])
api_router.include_router(settings_router, dependencies=[Depends(require_admin_access)])
api_router.include_router(public_parser_contract_router)
api_router.include_router(images_router)
api_router.include_router(jobs_router, dependencies=[Depends(require_admin_access)])
api_router.include_router(products_router)
api_router.include_router(sources_router, dependencies=[Depends(require_admin_access)])
