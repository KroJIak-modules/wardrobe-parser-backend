from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.admin_editors import AdminDesignerEditorPayload, AdminTaxonomyEditorPayload
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.admin_editor_service import AdminEditorService


router = APIRouter(tags=["admin-editors"])


@router.get("/admin/designers/editor", response_model=AdminDesignerEditorPayload, dependencies=[Depends(require_permission("control.designers.read"))])
def get_admin_designers_editor_state(db: Session = Depends(get_db)) -> dict:
    return AdminEditorService(db).list_designer_editor_state()


@router.put("/admin/designers/editor", response_model=AdminDesignerEditorPayload, dependencies=[Depends(require_permission("control.designers.edit"))])
def save_admin_designers_editor_state(payload: AdminDesignerEditorPayload, db: Session = Depends(get_db)) -> dict:
    result = AdminEditorService(db).save_designer_editor_state(payload.model_dump(exclude_unset=True))
    db.commit()
    return result


@router.post("/admin/designers/logo/upload", dependencies=[Depends(require_permission("control.designers.edit"))])
def upload_admin_designer_logo(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    asset = MediaAssetService(db).save_upload(scope="designers", upload=file)
    db.commit()
    return {"ok": True, "image_asset_id": int(asset.id)}


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
