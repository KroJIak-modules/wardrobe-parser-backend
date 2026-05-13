from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ImageAsset
from app.schemas.parser import (
    AdminUiSettingsUpdateRequest,
    ShowcaseCarouselOrderRequest,
    ShowcaseHeroSetRequest,
    ShowcaseMediaSettingsResponse,
)
from app.services.settings.pricing_service import PricingSettingsService

router = APIRouter(prefix="/showcase", tags=["showcase"])

_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads" / "showcase"
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_CAROUSEL_LIMIT = 20


def _save_upload(file: UploadFile, db: Session) -> int:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не передан")
    extension = Path(file.filename).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый формат изображения")
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не является корректным изображением")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in Path(file.filename).stem.lower()).strip("-") or "showcase"
    file_name = f"{safe_stem}-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{uuid4().hex[:8]}{extension}"
    target = _UPLOAD_DIR / file_name
    target.write_bytes(content)
    asset = ImageAsset(
        source_url=f"stored://showcase/{file_name}",
        storage_mode="stored_file",
        stored_path=str(target),
        created_at=datetime.now(timezone.utc),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return int(asset.id)


def _file_response_for_asset(asset_id: int, db: Session) -> FileResponse:
    asset = db.query(ImageAsset).filter(ImageAsset.id == asset_id).one_or_none()
    if asset is None or asset.storage_mode != "stored_file" or not asset.stored_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    path = Path(asset.stored_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл изображения не найден")
    return FileResponse(path)


@router.get("/state", response_model=ShowcaseMediaSettingsResponse)
def showcase_state(db: Session = Depends(get_db)):
    ui = PricingSettingsService(db).get_admin_ui_settings()
    hero_id = int(ui.showcase_hero_image_asset_id or 0)
    carousel_items = [int(x) for x in list(ui.showcase_carousel_image_asset_ids or []) if int(x) > 0 and int(x) != hero_id]
    return ShowcaseMediaSettingsResponse(
        showcase_hero_image_asset_id=(hero_id if hero_id > 0 else None),
        showcase_carousel_image_asset_ids=carousel_items,
        carousel_limit=_CAROUSEL_LIMIT,
    )


@router.get("/hero/image")
def hero_image(db: Session = Depends(get_db)):
    ui = PricingSettingsService(db).get_admin_ui_settings()
    hero_id = int(ui.showcase_hero_image_asset_id or 0)
    if hero_id <= 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заставка не установлена")
    return _file_response_for_asset(hero_id, db)


@router.post("/hero/upload")
def upload_hero(file: UploadFile = File(...), db: Session = Depends(get_db)):
    image_id = _save_upload(file, db)
    pricing_svc = PricingSettingsService(db)
    ui = pricing_svc.get_admin_ui_settings()
    carousel = [x for x in list(ui.showcase_carousel_image_asset_ids or []) if int(x) != int(image_id)]
    pricing_svc.update_admin_ui_settings(
        AdminUiSettingsUpdateRequest(
            showcase_hero_image_asset_id=image_id,
            showcase_carousel_image_asset_ids=carousel,
        )
    )
    return {"ok": True, "image_asset_id": image_id}


@router.delete("/hero")
def clear_hero(db: Session = Depends(get_db)):
    PricingSettingsService(db).update_admin_ui_settings(
        AdminUiSettingsUpdateRequest(
            showcase_hero_image_asset_id=None,
        )
    )
    return {"ok": True}


@router.patch("/hero")
def set_hero(payload: ShowcaseHeroSetRequest, db: Session = Depends(get_db)):
    image_id = int(payload.image_asset_id)
    pricing_svc = PricingSettingsService(db)
    ui = pricing_svc.get_admin_ui_settings()
    carousel = [x for x in list(ui.showcase_carousel_image_asset_ids or []) if int(x) != int(image_id)]
    pricing_svc.update_admin_ui_settings(
        AdminUiSettingsUpdateRequest(
            showcase_hero_image_asset_id=image_id,
            showcase_carousel_image_asset_ids=carousel,
        )
    )
    return {"ok": True, "image_asset_id": image_id}


@router.get("/carousel")
def carousel_state(db: Session = Depends(get_db)):
    ui = PricingSettingsService(db).get_admin_ui_settings()
    hero_id = int(ui.showcase_hero_image_asset_id or 0)
    items = [int(x) for x in list(ui.showcase_carousel_image_asset_ids or []) if int(x) > 0 and int(x) != hero_id]
    return {"items": items, "limit": _CAROUSEL_LIMIT}


@router.post("/carousel/upload")
def upload_carousel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    image_id = _save_upload(file, db)
    pricing_svc = PricingSettingsService(db)
    ui = pricing_svc.get_admin_ui_settings()
    hero_id = int(ui.showcase_hero_image_asset_id or 0)
    items = [int(x) for x in list(ui.showcase_carousel_image_asset_ids or []) if int(x) != hero_id]
    if image_id not in items:
        items.append(image_id)
    items = items[:_CAROUSEL_LIMIT]
    pricing_svc.update_admin_ui_settings(
        AdminUiSettingsUpdateRequest(
            showcase_carousel_image_asset_ids=items,
        )
    )
    return {"ok": True, "image_asset_id": image_id, "items": items}


@router.patch("/carousel/order")
def reorder_carousel(payload: ShowcaseCarouselOrderRequest, db: Session = Depends(get_db)):
    items: list[int] = []
    seen: set[int] = set()
    for x in payload.items:
        value = int(x)
        if value > 0 and value not in seen:
            seen.add(value)
            items.append(value)
        if len(items) >= _CAROUSEL_LIMIT:
            break
    PricingSettingsService(db).update_admin_ui_settings(
        AdminUiSettingsUpdateRequest(
            showcase_carousel_image_asset_ids=items,
        )
    )
    return {"ok": True, "items": items}


@router.delete("/carousel/{image_id}")
def remove_carousel_item(image_id: int, db: Session = Depends(get_db)):
    ui = PricingSettingsService(db).get_admin_ui_settings()
    items = [int(x) for x in (ui.showcase_carousel_image_asset_ids or []) if int(x) != int(image_id)]
    PricingSettingsService(db).update_admin_ui_settings(
        AdminUiSettingsUpdateRequest(
            showcase_carousel_image_asset_ids=items,
        )
    )
    return {"ok": True, "items": items}


@router.get("/carousel/{image_id}/image")
def carousel_image(image_id: int, db: Session = Depends(get_db)):
    return _file_response_for_asset(image_id, db)
