"""API endpoints for recursive category tree and keyword rules."""

from fastapi import HTTPException, status
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.parser import (
    CatalogCategoryNodeResponse,
    CategoryCreateRequest,
    CategoryKeywordRequest,
    CategoryManualProductRequest,
    CategoryManualProductResponse,
    CategoryTreeNodeResponse,
    CategoryUpdateRequest,
)
from app.services.catalog.category_tree_service import CategoryTreeService

router = APIRouter(tags=["categories"])


def _to_catalog_node(node: CategoryTreeNodeResponse) -> CatalogCategoryNodeResponse:
    return CatalogCategoryNodeResponse(
        slug=node.slug,
        name=node.name,
        parent_id=node.parent_id,
        count=int(node.product_count or 0),
        is_enabled=bool(node.is_enabled),
        is_designers_root=bool(node.is_designers_root),
        is_in_designers_branch=bool(node.is_in_designers_branch),
        children=[_to_catalog_node(child) for child in (node.children or []) if child.is_enabled],
    )


@router.get("/categories/tree", response_model=list[CategoryTreeNodeResponse])
def get_category_tree(
    include_counts: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    return CategoryTreeService(db).get_category_tree(include_counts=include_counts)


@router.get("/catalog/categories/roots", response_model=list[CatalogCategoryNodeResponse])
def get_catalog_roots(
    include_counts: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    tree = CategoryTreeService(db).get_category_tree(include_counts=include_counts)
    roots: list[CatalogCategoryNodeResponse] = []
    for node in tree:
        if node.parent_id is not None or not node.is_enabled:
            continue
        roots.append(
            CatalogCategoryNodeResponse(
                slug=node.slug,
                name=node.name,
                parent_id=node.parent_id,
                count=int(node.product_count or 0),
                is_enabled=bool(node.is_enabled),
                is_designers_root=bool(node.is_designers_root),
                is_in_designers_branch=bool(node.is_in_designers_branch),
                children=[],
            )
        )
    return roots


@router.get("/catalog/categories/root/{root_slug}", response_model=CatalogCategoryNodeResponse)
def get_catalog_root_branch(
    root_slug: str,
    include_counts: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    slug = root_slug.strip().lower()
    tree = CategoryTreeService(db).get_category_tree(include_counts=include_counts)
    for node in tree:
        if node.parent_id is None and node.is_enabled and node.slug == slug:
            return _to_catalog_node(node)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Root category not found")


@router.post("/categories", response_model=CategoryTreeNodeResponse)
def create_category(payload: CategoryCreateRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).create_category(payload)


@router.patch("/categories/{category_id}", response_model=CategoryTreeNodeResponse)
def update_category(category_id: int, payload: CategoryUpdateRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).update_category(category_id, payload)


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).delete_category(category_id)


@router.post("/categories/{category_id}/keywords")
def add_category_keyword(category_id: int, payload: CategoryKeywordRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).add_category_keyword(category_id, payload)


@router.delete("/categories/{category_id}/keywords/{keyword}")
def remove_category_keyword(
    category_id: int,
    keyword: str,
    scope: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return CategoryTreeService(db).remove_category_keyword(category_id, keyword, scope=scope)


@router.get("/categories/{category_id}/manual-products", response_model=list[CategoryManualProductResponse])
def get_manual_products(category_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).list_manual_products(category_id)


@router.get("/categories/{category_id}/manual-products/search", response_model=list[CategoryManualProductResponse])
def search_manual_products(category_id: int, query: str = Query(min_length=1, max_length=255), limit: int = Query(default=3), db: Session = Depends(get_db)):
    return CategoryTreeService(db).search_manual_products(category_id, query=query, limit=limit)


@router.post("/categories/{category_id}/manual-products")
def add_manual_product(category_id: int, payload: CategoryManualProductRequest, db: Session = Depends(get_db)):
    return CategoryTreeService(db).add_manual_product(category_id, payload.product_id)


@router.delete("/categories/{category_id}/manual-products/{product_id}")
def remove_manual_product(category_id: int, product_id: int, db: Session = Depends(get_db)):
    return CategoryTreeService(db).remove_manual_product(category_id, product_id)
