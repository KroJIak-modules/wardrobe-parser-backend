from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.showcase_preview import (
    CatalogExperienceResponse,
    ShowcaseDesignersDirectoryResponse,
    ShowcaseNavigationResponse,
)
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.admin_showcase_preview_service import AdminShowcasePreviewService


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
    "/admin/showcase/designers-directory",
    response_model=ShowcaseDesignersDirectoryResponse,
    dependencies=[Depends(require_permission("showcase.read"))],
)
def get_admin_showcase_designers_directory(db: Session = Depends(get_db)) -> dict:
    return AdminShowcasePreviewService(db).designers_directory()
