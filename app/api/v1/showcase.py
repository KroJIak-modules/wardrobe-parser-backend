from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.models import ImageAsset, ShowcaseCarouselImage
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.showcase_service import ShowcaseService


router = APIRouter(tags=["showcase"])


class HeroSetRequest(BaseModel):
    image_asset_id: int = Field(ge=1)


class CarouselOrderRequest(BaseModel):
    items: list[int] = Field(default_factory=list)


def _asset_or_error(db: Session, asset_id: int) -> ImageAsset:
    asset = db.query(ImageAsset).filter(ImageAsset.id == int(asset_id)).one_or_none()
    if asset is None:
        raise NotFoundError("Изображение не найдено")
    return asset


@router.get("/showcase/state")
def get_showcase_state(db: Session = Depends(get_db)) -> dict:
    return ShowcaseService(db).state()


@router.get("/showcase/hero/image")
def get_showcase_hero_image(db: Session = Depends(get_db)) -> FileResponse:
    settings = ShowcaseService(db).ensure_settings()
    if not settings.hero_image_asset_id:
        raise NotFoundError("Hero image not set")
    asset = _asset_or_error(db, int(settings.hero_image_asset_id))
    return FileResponse(MediaAssetService(db).resolve_file_path(asset), media_type=asset.mime_type)


@router.get("/showcase/carousel")
def get_showcase_carousel(db: Session = Depends(get_db)) -> dict:
    state = ShowcaseService(db).state()
    return {"items": state["carousel_image_asset_ids"]}


@router.get("/showcase/carousel/{image_id}/image")
def get_showcase_carousel_image(image_id: int, db: Session = Depends(get_db)) -> FileResponse:
    asset = _asset_or_error(db, image_id)
    return FileResponse(MediaAssetService(db).resolve_file_path(asset), media_type=asset.mime_type)


@router.post("/showcase/hero/upload", dependencies=[Depends(require_permission("showcase.edit"))])
def upload_showcase_hero_image(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    asset = MediaAssetService(db).save_upload(scope="showcase", upload=file)
    db.commit()
    return {"ok": True, "image_asset_id": int(asset.id)}


@router.post("/showcase/carousel/upload", dependencies=[Depends(require_permission("showcase.edit"))])
def upload_showcase_carousel_image(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    asset = MediaAssetService(db).save_upload(scope="showcase", upload=file)
    db.commit()
    return {"ok": True, "image_asset_id": int(asset.id)}


@router.put("/showcase/hero", dependencies=[Depends(require_permission("showcase.edit"))])
def set_showcase_hero(payload: HeroSetRequest, db: Session = Depends(get_db)) -> dict:
    _asset_or_error(db, payload.image_asset_id)
    settings = ShowcaseService(db).ensure_settings()
    settings.hero_image_asset_id = int(payload.image_asset_id)
    db.commit()
    return {"ok": True, "image_asset_id": int(payload.image_asset_id)}


@router.delete("/showcase/hero", dependencies=[Depends(require_permission("showcase.edit"))])
def clear_showcase_hero(db: Session = Depends(get_db)) -> dict:
    settings = ShowcaseService(db).ensure_settings()
    settings.hero_image_asset_id = None
    db.commit()
    return {"ok": True}


@router.post("/showcase/carousel/{image_id}", dependencies=[Depends(require_permission("showcase.edit"))])
def add_showcase_carousel_image(image_id: int, db: Session = Depends(get_db)) -> dict:
    _asset_or_error(db, image_id)
    exists = db.query(ShowcaseCarouselImage).filter(ShowcaseCarouselImage.image_asset_id == int(image_id)).one_or_none()
    if exists is None:
        max_position = db.query(ShowcaseCarouselImage.position).order_by(ShowcaseCarouselImage.position.desc()).limit(1).scalar()
        db.add(ShowcaseCarouselImage(image_asset_id=int(image_id), position=int(max_position or 0) + 1))
        db.commit()
    return {"ok": True, "image_asset_id": int(image_id), "items": ShowcaseService(db).state()["carousel_image_asset_ids"]}


@router.delete("/showcase/carousel/{image_id}", dependencies=[Depends(require_permission("showcase.edit"))])
def remove_showcase_carousel_image(image_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.query(ShowcaseCarouselImage).filter(ShowcaseCarouselImage.image_asset_id == int(image_id)).one_or_none()
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True, "items": ShowcaseService(db).state()["carousel_image_asset_ids"]}


@router.put("/showcase/carousel/order", dependencies=[Depends(require_permission("showcase.edit"))])
def reorder_showcase_carousel(payload: CarouselOrderRequest, db: Session = Depends(get_db)) -> dict:
    rows = {
        int(row.image_asset_id): row
        for row in db.query(ShowcaseCarouselImage).all()
    }
    position = 1
    for image_id in [int(item) for item in payload.items if int(item) in rows]:
        rows[image_id].position = position
        position += 1
    for image_id, row in rows.items():
        if image_id in payload.items:
            continue
        row.position = position
        position += 1
    db.commit()
    return {"ok": True, "items": ShowcaseService(db).state()["carousel_image_asset_ids"]}
