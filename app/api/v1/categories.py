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
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.category_tree_service import CategoryTreeService

router = APIRouter(tags=["categories"])


def _to_catalog_node(node: CategoryTreeNodeResponse) -> CatalogCategoryNodeResponse:
    return CatalogCategoryNodeResponse(
        slug=node.slug,
        name=node.name,
        count=int(node.product_count or 0),
        is_designers_root=bool(node.is_designers_root),
        is_in_designers_branch=bool(node.is_in_designers_branch),
        children=[_to_catalog_node(child) for child in (node.children or []) if child.is_enabled],
    )


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


@router.get(
    "/catalog/categories/roots",
    response_model=list[CatalogCategoryNodeResponse],
    summary="Список корневых категорий витрины",
    description="Возвращает только включённые корневые категории, доступные для публичной витрины.",
    responses={
        200: {
            "description": "Корневые категории витрины.",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "slug": "muzhskoe",
                            "name": "Мужское",
                            "count": 1240,
                            "is_designers_root": False,
                            "is_in_designers_branch": False,
                            "children": [],
                        },
                        {
                            "slug": "dizajnery",
                            "name": "Дизайнеры",
                            "count": 860,
                            "is_designers_root": True,
                            "is_in_designers_branch": False,
                            "children": [],
                        },
                    ]
                }
            },
        }
    },
)
def get_catalog_roots(
    include_counts: bool = Query(default=True, description="Добавить количество товаров в категории."),
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
                count=int(node.product_count or 0),
                is_designers_root=bool(node.is_designers_root),
                is_in_designers_branch=bool(node.is_in_designers_branch),
                children=[],
            )
        )
    return roots


@router.get(
    "/catalog/categories/root/{root_slug}",
    response_model=CatalogCategoryNodeResponse,
    summary="Дерево категорий выбранного корня",
    description="Возвращает выбранный корень и всех его включённых потомков для меню/навигации витрины.",
    responses={
        200: {
            "description": "Ветка дерева категорий.",
            "content": {
                "application/json": {
                    "example": {
                        "slug": "muzhskoe",
                        "name": "Мужское",
                        "count": 1240,
                        "is_designers_root": False,
                        "is_in_designers_branch": False,
                        "children": [
                            {
                                "slug": "muzhskoe-kurtki",
                                "name": "Куртки",
                                "count": 310,
                                "is_designers_root": False,
                                "is_in_designers_branch": False,
                                "children": [],
                            }
                        ],
                    }
                }
            },
        }
    },
)
def get_catalog_root_branch(
    root_slug: str,
    include_counts: bool = Query(default=True, description="Добавить количество товаров для каждой категории."),
    db: Session = Depends(get_db),
):
    slug = root_slug.strip().lower()
    tree = CategoryTreeService(db).get_category_tree(include_counts=include_counts)
    for node in tree:
        if node.parent_id is None and node.is_enabled and node.slug == slug:
            return _to_catalog_node(node)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Root category not found")


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
