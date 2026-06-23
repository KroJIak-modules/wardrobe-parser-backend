from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.taxonomy import TaxonomyState, TaxonomyWriteState
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.taxonomy_service import TaxonomyService


router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("/state", response_model=TaxonomyState, dependencies=[Depends(require_permission("control.categories.read"))])
def get_taxonomy_state(db: Session = Depends(get_db)) -> TaxonomyState:
    return TaxonomyService(db).get_state()


@router.put("/state", response_model=TaxonomyState, dependencies=[Depends(require_permission("control.categories.edit"))])
def replace_taxonomy_state(payload: TaxonomyWriteState, db: Session = Depends(get_db)) -> TaxonomyState:
    return TaxonomyService(db).replace_state_from_write(payload)


@router.get("/products/search", dependencies=[Depends(require_permission("control.categories.read"))])
def search_taxonomy_products(
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    return ProductQueryService(db).search_products(query=q, limit=limit, offset=offset)
