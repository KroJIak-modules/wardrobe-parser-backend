from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import NotFoundError, ValidationError
from app.models import ImageAsset
from app.schemas.showcase_media import ShowcaseMediaUploadResponse, ShowcaseStateResponse, ShowcaseStateUpdateRequest
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.showcase_service import ShowcaseService


router = APIRouter(tags=["showcase"])


def _asset_or_not_found(db: Session, asset_id: int) -> ImageAsset:
    asset = db.query(ImageAsset).filter(ImageAsset.id == int(asset_id)).one_or_none()
    if not ShowcaseService.is_showcase_asset(asset):
        raise NotFoundError("Медиафайл витрины не найден")
    return asset


@router.get("/showcase/state", response_model=ShowcaseStateResponse)
def get_showcase_state(db: Session = Depends(get_db)) -> ShowcaseStateResponse:
    return ShowcaseService(db).state()


@router.get("/showcase/media/{asset_id}/file")
def get_showcase_media_file(asset_id: int, db: Session = Depends(get_db)) -> FileResponse:
    asset = _asset_or_not_found(db, asset_id)
    file_path = MediaAssetService(db).resolve_file_path(asset)
    if not file_path.exists():
        raise NotFoundError("Медиафайл витрины не найден")
    return FileResponse(file_path, media_type=asset.mime_type)


@router.post(
    "/showcase/media/upload",
    response_model=ShowcaseMediaUploadResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def upload_showcase_media(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ShowcaseMediaUploadResponse:
    try:
        asset = MediaAssetService(db).save_upload(scope="showcase", upload=file)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    db.commit()
    return ShowcaseMediaUploadResponse(
        ok=True,
        asset=ShowcaseService.asset_payload(asset),
    )


@router.put(
    "/showcase/state",
    response_model=ShowcaseStateResponse,
    dependencies=[Depends(require_permission("showcase.edit"))],
)
def update_showcase_state(payload: ShowcaseStateUpdateRequest, db: Session = Depends(get_db)) -> ShowcaseStateResponse:
    state = ShowcaseService(db).replace_state(payload)
    db.commit()
    return state
