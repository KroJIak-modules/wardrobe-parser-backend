from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.admin_editors import (
    AdminDesignerEditorPayload,
    AdminDesignerSourceEnabledPatch,
    AdminFilterAssignmentRebuildStartResponse,
    AdminFilterAssignmentRebuildStatus,
    AdminTaxonomyEditorPayload,
)
from app.services.auth.admin_auth_service import AdminAuthContext, require_permission
from app.services.catalog.admin_designer_view_service import AdminDesignerViewService
from app.services.catalog.admin_editor_service import AdminEditorService


router = APIRouter(tags=["admin-editors"])


@router.get("/admin/designers/editor", response_model=AdminDesignerEditorPayload)
def get_admin_designers_editor_state(
    admin: AdminAuthContext = Depends(require_permission("control.designers.read")),
    db: Session = Depends(get_db),
) -> dict:
    payload = AdminEditorService(db).list_designer_editor_state()
    return AdminDesignerViewService(db, admin.user_id).apply_new_flags_to_editor_payload(payload)


@router.post("/admin/designers/brands/mark-all-viewed")
def mark_all_admin_brand_views_viewed(
    admin: AdminAuthContext = Depends(require_permission("control.designers.edit")),
    db: Session = Depends(get_db),
) -> dict:
    marked = AdminDesignerViewService(db, admin.user_id).mark_all_brands_viewed()
    return {"ok": True, "marked": marked}


@router.post("/admin/designers/brands/{source_brand}/view")
def mark_admin_brand_view_viewed(
    source_brand: str,
    admin: AdminAuthContext = Depends(require_permission("control.designers.read")),
    db: Session = Depends(get_db),
) -> dict:
    AdminDesignerViewService(db, admin.user_id).mark_brand_viewed(source_brand)
    return {"ok": True, "source_brand": source_brand}


@router.post("/admin/designers/designers/mark-all-viewed")
def mark_all_admin_designer_views_viewed(
    admin: AdminAuthContext = Depends(require_permission("control.designers.edit")),
    db: Session = Depends(get_db),
) -> dict:
    marked = AdminDesignerViewService(db, admin.user_id).mark_all_designers_viewed()
    return {"ok": True, "marked": marked}


@router.post("/admin/designers/designers/{designer_id}/view")
def mark_admin_designer_view_viewed(
    designer_id: int,
    admin: AdminAuthContext = Depends(require_permission("control.designers.read")),
    db: Session = Depends(get_db),
) -> dict:
    AdminDesignerViewService(db, admin.user_id).mark_designer_viewed(designer_id)
    return {"ok": True, "designer_id": int(designer_id)}


@router.put("/admin/designers/editor", response_model=AdminDesignerEditorPayload)
def save_admin_designers_editor_state(
    payload: AdminDesignerEditorPayload,
    admin: AdminAuthContext = Depends(require_permission("control.designers.edit")),
    db: Session = Depends(get_db),
) -> dict:
    result = AdminEditorService(db).save_designer_editor_state(payload.model_dump(exclude_unset=True))
    db.commit()
    return AdminDesignerViewService(db, admin.user_id).apply_new_flags_to_editor_payload(result)


@router.patch(
    "/admin/designers/editor/sources/{source_brand}/enabled",
    response_model=AdminDesignerSourceEnabledPatch,
    dependencies=[Depends(require_permission("control.designers.edit"))],
)
def set_admin_designer_source_enabled(
    source_brand: str,
    payload: AdminDesignerSourceEnabledPatch,
    db: Session = Depends(get_db),
) -> dict:
    result = AdminEditorService(db).set_designer_source_enabled(
        source_brand=source_brand,
        include_in_designers=payload.include_in_designers,
    )
    db.commit()
    return result


@router.get("/admin/taxonomy/editor", dependencies=[Depends(require_permission("control.categories.read"))])
def get_admin_taxonomy_editor_state(db: Session = Depends(get_db)) -> dict:
    return AdminEditorService(db).list_taxonomy_editor_state()


@router.get("/admin/taxonomy/editor/product-library", dependencies=[Depends(require_permission("control.categories.read"))])
def search_admin_taxonomy_editor_product_library(
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": AdminEditorService(db).search_taxonomy_product_library(query=q, limit=limit)}


@router.put("/admin/taxonomy/editor", dependencies=[Depends(require_permission("control.categories.edit"))])
def save_admin_taxonomy_editor_state(payload: AdminTaxonomyEditorPayload, db: Session = Depends(get_db)) -> dict:
    result = AdminEditorService(db).save_taxonomy_editor_state(payload.model_dump())
    db.commit()
    return result


@router.get(
    "/admin/taxonomy/editor/filter-assignment-rebuild",
    response_model=AdminFilterAssignmentRebuildStatus,
    dependencies=[Depends(require_permission("control.categories.read"))],
)
def get_admin_taxonomy_filter_assignment_rebuild_status(db: Session = Depends(get_db)) -> dict:
    return AdminEditorService(db).get_filter_assignment_rebuild_status()


@router.post(
    "/admin/taxonomy/editor/filter-assignment-rebuild",
    response_model=AdminFilterAssignmentRebuildStartResponse,
    dependencies=[Depends(require_permission("control.categories.edit"))],
)
def request_admin_taxonomy_filter_assignment_rebuild(db: Session = Depends(get_db)) -> dict:
    started, status = AdminEditorService(db).request_filter_assignment_rebuild()
    db.commit()
    return {"ok": True, "started": started, "status": status}
