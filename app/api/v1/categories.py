"""API endpoints for recursive category tree and keyword rules."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.parser import (
    CategoryCreateRequest,
    CategoryKeywordRequest,
    CategoryManualProductRequest,
    CategoryManualProductResponse,
    CategoryTreeNodeResponse,
    CategoryUpdateRequest,
)
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.category_tree_service import CategoryTreeService

router = APIRouter(tags=["categories"])


@router.get(
    "/categories/tree",
    response_model=list[CategoryTreeNodeResponse],
    dependencies=[Depends(require_permission("control.categories.read"))],
)
def get_category_tree(
    include_counts: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    return CategoryTreeService(db).get_category_tree(include_counts=include_counts)


@router.post(
    "/categories",
    response_model=CategoryTreeNodeResponse,
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def create_category(payload: CategoryCreateRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).create_category(payload)


@router.patch(
    "/categories/{category_id}",
    response_model=CategoryTreeNodeResponse,
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def update_category(category_id: int, payload: CategoryUpdateRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).update_category(category_id, payload)


@router.delete("/categories/{category_id}", dependencies=[Depends(require_permission("control.categories.edit"))])
def delete_category(category_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).delete_category(category_id)


@router.post(
    "/categories/{category_id}/keywords",
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def add_category_keyword(category_id: int, payload: CategoryKeywordRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).add_category_keyword(category_id, payload)


@router.delete(
    "/categories/{category_id}/keywords/{keyword}",
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def remove_category_keyword(
    category_id: int,
    keyword: str,
    scope: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return CategoryTreeService(db).remove_category_keyword(category_id, keyword, scope=scope)


@router.get(
    "/categories/{category_id}/manual-products",
    response_model=list[CategoryManualProductResponse],
    dependencies=[Depends(require_permission("control.categories.read"))],
)
def get_manual_products(category_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).list_manual_products(category_id)


@router.get(
    "/categories/{category_id}/manual-products/search",
    response_model=list[CategoryManualProductResponse],
    dependencies=[Depends(require_permission("control.categories.read"))],
)
def search_manual_products(category_id: int, query: str = Query(min_length=1, max_length=255), limit: int = Query(default=3), db: Session = Depends(get_db)):
    return CategoryTreeService(db).search_manual_products(category_id, query=query, limit=limit)


@router.post(
    "/categories/{category_id}/manual-products",
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def add_manual_product(category_id: int, payload: CategoryManualProductRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).add_manual_product(category_id, payload.product_id)


@router.delete(
    "/categories/{category_id}/manual-products/{product_id}",
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def remove_manual_product(category_id: int, product_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).remove_manual_product(category_id, product_id)
