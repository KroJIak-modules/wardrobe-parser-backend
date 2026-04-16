"""API endpoints for parser/admin settings."""

import logging
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ImageAsset
from app.schemas.parser import (
    PricingSettingsResponse,
    PricingSettingsUpdateRequest,
    PricingSupplierCreateRequest,
    PricingSupplierResponse,
    PricingSupplierUpdateRequest,
    SettingsTransferPayload,
    SettingsTransferResponse,
    WeightMissingProductResponse,
    WeightRuleCreateRequest,
    WeightRuleKeywordRequest,
    WeightRuleResponse,
    WeightRuleUpdateRequest,
)
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.settings_transfer_service import SettingsTransferService
from app.services.settings.weight_rule_service import WeightRuleService

router = APIRouter(prefix="/settings", tags=["settings"])
LOGGER = logging.getLogger(__name__)
_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads" / "showcase"
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@router.get("/pricing", response_model=PricingSettingsResponse)
def get_pricing_settings(db: Session = Depends(get_db)):
    return PricingSettingsService(db).get_settings(refresh_bybit=False)


@router.patch("/pricing", response_model=PricingSettingsResponse)
def update_pricing_settings(payload: PricingSettingsUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_settings(payload)


@router.patch("/pricing/suppliers/{supplier_id}", response_model=PricingSupplierResponse)
def update_pricing_supplier(supplier_id: int, payload: PricingSupplierUpdateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).update_supplier(supplier_id=supplier_id, payload=payload)


@router.post("/pricing/suppliers", response_model=PricingSupplierResponse)
def create_pricing_supplier(payload: PricingSupplierCreateRequest, db: Session = Depends(get_db)):
    return PricingSettingsService(db).create_supplier(payload)


@router.delete("/pricing/suppliers/{supplier_id}")
def delete_pricing_supplier(supplier_id: int, db: Session = Depends(get_db)):
    return PricingSettingsService(db).delete_supplier(supplier_id)


@router.get("/weight-rules", response_model=list[WeightRuleResponse])
def list_weight_rules(db: Session = Depends(get_db)):
    try:
        return WeightRuleService(db).list_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules, returning empty list")
        return []


@router.get("/weight-rules/missing-products", response_model=list[WeightMissingProductResponse])
def list_missing_weight_products(limit: int = 500, db: Session = Depends(get_db)):
    try:
        return WeightRuleService(db).list_missing_weight_products(limit=limit)
    except Exception:
        LOGGER.exception("Failed to load missing weight products, returning empty list")
        return []


@router.post("/weight-rules", response_model=WeightRuleResponse)
def create_weight_rule(payload: WeightRuleCreateRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).create_rule(payload)


@router.patch("/weight-rules/{rule_id}", response_model=WeightRuleResponse)
def update_weight_rule(rule_id: int, payload: WeightRuleUpdateRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).update_rule(rule_id, payload)


@router.delete("/weight-rules/{rule_id}")
def delete_weight_rule(rule_id: int, db: Session = Depends(get_db)):
    return WeightRuleService(db).delete_rule(rule_id)


@router.post("/weight-rules/{rule_id}/keywords")
def add_weight_rule_keyword(rule_id: int, payload: WeightRuleKeywordRequest, db: Session = Depends(get_db)):
    return WeightRuleService(db).add_keyword(rule_id, payload)


@router.delete("/weight-rules/{rule_id}/keywords/{keyword}")
def remove_weight_rule_keyword(rule_id: int, keyword: str, db: Session = Depends(get_db)):
    return WeightRuleService(db).remove_keyword(rule_id, keyword)


@router.get("/export", response_model=SettingsTransferPayload)
def export_settings(db: Session = Depends(get_db)):
    return SettingsTransferService(db).export_payload()


@router.post("/import", response_model=SettingsTransferResponse)
def import_settings(payload: SettingsTransferPayload, db: Session = Depends(get_db)):
    return SettingsTransferService(db).import_payload(payload)


@router.post("/showcase/upload-image")
async def upload_showcase_image(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не передан")
    extension = Path(file.filename).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый формат изображения")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не является корректным изображением")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in Path(file.filename).stem.lower()).strip("-") or "showcase"
    file_name = f"{safe_stem}-{int(time.time() * 1000)}-{uuid4().hex[:8]}{extension}"
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
    return {
        "ok": True,
        "image_asset_id": int(asset.id),
        "image_url": f"/api/v1/images/{int(asset.id)}",
    }
