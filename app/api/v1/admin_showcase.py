from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.showcase_preview import (
    CatalogExperienceResponse,
    ShowcaseDesignersDirectoryResponse,
    ShowcaseNavigationResponse,
)
from app.schemas.site import SiteCatalogProductsResponse
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.admin_showcase_preview_service import AdminShowcasePreviewService
from app.services.catalog.site_query_service import SiteQueryService


router = APIRouter(tags=["admin-showcase"])


@router.get(
    "/admin/showcase/navigation",
    response_model=ShowcaseNavigationResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_showcase_navigation(db: Session = Depends(get_db)) -> dict:
    return AdminShowcasePreviewService(db).navigation()


@router.get(
    "/admin/showcase/catalog-experience",
    response_model=CatalogExperienceResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_showcase_catalog_experience(
    request: Request,
    view_key: str = Query(default="default"),
    db: Session = Depends(get_db),
) -> dict:
    normalized_view_key = str(view_key or "").strip().lower()
    if normalized_view_key not in {"default", "designers", "sale"}:
        normalized_view_key = "default"
    search_params = {
        key: request.query_params.getlist(key)
        for key in request.query_params.keys()
        if key != "view_key"
    }
    return AdminShowcasePreviewService(db).catalog_experience(
        view_key=normalized_view_key,  # type: ignore[arg-type]
        search_params=search_params,
    )


@router.get(
    "/admin/showcase/catalog-products",
    response_model=SiteCatalogProductsResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_showcase_catalog_products(
    q: str = Query(default=""),
    designer: list[str] = Query(default=[]),
    gender: list[str] = Query(default=[]),
    section: list[str] = Query(default=[]),
    collection: str | None = Query(default=None),
    availability: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    discounted_only: bool = Query(default=False),
    limit: int = Query(default=48, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> SiteCatalogProductsResponse:
    normalized_availability = str(availability or "").strip().lower() or None
    if normalized_availability not in {None, "in_stock", "in-stock", "preorder", "by_order"}:
        normalized_availability = None
    normalized_status = str(status or "").strip().lower() or None
    if normalized_status not in {None, "orderable", "sold_out"}:
        normalized_status = None
    return SiteQueryService(db).catalog_products(
        limit=limit,
        offset=offset,
        query=q,
        designer_slugs=designer,
        gender_values=gender,
        filter_slugs=section,
        custom_catalog_slug=collection,
        availability_mode=(
            "in_stock"
            if normalized_availability in {"in_stock", "in-stock"}
            else "by_order"
            if normalized_availability in {"preorder", "by_order"}
            else None
        ),
        orderability_status=normalized_status,
        discounted_only=discounted_only,
        sort=sort,
    )


@router.get(
    "/admin/showcase/designers-directory",
    response_model=ShowcaseDesignersDirectoryResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_showcase_designers_directory(db: Session = Depends(get_db)) -> dict:
    return AdminShowcasePreviewService(db).designers_directory()
