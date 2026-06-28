from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Header, UploadFile
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi.responses import FileResponse
from starlette import status
from app.core.database import get_db
from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models import ImageAsset, Product, ProductListing, ProductListingMember, Source
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.media_asset_service import MediaAssetService
from app.services.catalog.sync_error_humanizer import humanize_sync_error
from app.services.catalog.source_registry_service import SourceRegistryService


router = APIRouter(tags=["sources"])


class EnabledPatch(BaseModel):
    enabled: bool


class SyncEnabledPatch(BaseModel):
    sync_enabled: bool


class DedupEnabledPatch(BaseModel):
    dedup_enabled: bool


class AutoHidePatch(BaseModel):
    hide_auto_added_products: bool


class AttributeVisibilityPatch(BaseModel):
    description_mode: str | None = None
    show_images: bool | None = None


class SupplierPatch(BaseModel):
    supplier_id: int | None = None
    promo_factor: float | None = None
    promo_only_no_discount: bool | None = None
    buyout_surcharge_value: float | None = None
    buyout_surcharge_currency: str | None = None


class SourceLogoPatch(BaseModel):
    logo_image_asset_id: int | None = None


class InternalSourceEntryResponse(BaseModel):
    id: int
    key: str
    url: str
    adapter_key: str
    enabled: bool
    sync_enabled: bool
    config: dict[str, Any]


class InternalSourceBootstrapRequest(BaseModel):
    sources: list[InternalSourceEntryResponse]


def _require_internal_api_token(x_internal_token: str | None = Header(default=None)) -> None:
    if str(x_internal_token or "").strip() != str(settings.internal_api_token or "").strip():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid internal token")


def _normalize_source_sync_status(raw: object) -> str | None:
    value = str(raw or "").strip().lower()
    if value in {"success", "partial", "failed"}:
        return value
    if value == "completed":
        return "success"
    return None


def _source_counts_by_id(db: Session) -> dict[int, dict[str, int]]:
    rows = (
        db.query(
            ProductListing.source_id.label("source_id"),
            ProductListing.ingest_mode.label("ingest_mode"),
            func.count(func.distinct(Product.id)).label("product_count"),
        )
        .join(ProductListingMember, ProductListingMember.product_id == Product.id)
        .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
        .filter(Product.lifecycle_status == "active")
        .group_by(ProductListing.source_id, ProductListing.ingest_mode)
        .all()
    )
    counts: dict[int, dict[str, int]] = {}
    for source_id, ingest_mode, product_count in rows:
        bucket = counts.setdefault(int(source_id), {"products_count": 0, "bound_sync_products_count": 0, "manual_products_count": 0})
        count = int(product_count or 0)
        bucket["products_count"] += count
        if str(ingest_mode) == "sync":
            bucket["bound_sync_products_count"] += count
        if str(ingest_mode) == "manual":
            bucket["manual_products_count"] += count
    return counts


def _source_payload(source: Source, counts_by_source_id: dict[int, dict[str, int]] | None = None) -> dict:
    counts = (counts_by_source_id or {}).get(int(source.id), {})
    setting = source.setting
    sync_state = source.sync_state
    supplier = getattr(setting, "supplier", None) if setting is not None else None
    mode = SourceRegistryService.derive_source_mode(source)
    return {
        "key": source.key,
        "source_id": int(source.id),
        "mode": mode,
        "name": source.name,
        "base_url": source.base_url,
        "logo_image_asset_id": int(source.logo_image_asset_id) if source.logo_image_asset_id is not None else None,
        "enabled": bool(getattr(setting, "is_enabled", True)),
        "sync_enabled": bool(getattr(setting, "is_sync_enabled", True)),
        "dedup_enabled": bool(getattr(setting, "dedup_enabled", True)),
        "hide_auto_added_products": bool(getattr(setting, "hide_auto_added_products", False)),
        "description_mode": str(getattr(setting, "description_mode", "text") or "text"),
        "show_images": bool(getattr(setting, "show_images", True)),
        "products_count": int(counts.get("products_count", 0)),
        "manual_products_count": int(counts.get("manual_products_count", 0)),
        "bound_sync_products_count": int(counts.get("bound_sync_products_count", 0)),
        "last_sync_at": sync_state.last_sync_at.isoformat() if getattr(sync_state, "last_sync_at", None) else None,
        "last_sync_duration_sec": getattr(sync_state, "last_sync_duration_sec", None),
        "last_sync_status": _normalize_source_sync_status(getattr(sync_state, "last_sync_status", None)),
        "last_error_code": getattr(sync_state, "last_error_code", None),
        "last_error_message": humanize_sync_error(
            getattr(sync_state, "last_error_message", None),
            getattr(sync_state, "last_error_code", None),
        ),
        "supplier_id": int(getattr(setting, "supplier_id", 0) or 0) or None,
        "supplier_key": getattr(supplier, "key", None),
        "supplier_name": getattr(supplier, "name", None),
        "promo_factor": float(getattr(setting, "promo_factor", 1) or 1),
        "promo_only_no_discount": bool(getattr(setting, "promo_only_no_discount", False)),
        "buyout_surcharge_value": (
            float(setting.buyout_surcharge_value)
            if getattr(setting, "buyout_surcharge_value", None) is not None
            else None
        ),
        "buyout_surcharge_currency": (
            str(setting.buyout_surcharge_currency)
            if getattr(setting, "buyout_surcharge_currency", None)
            else None
        ),
    }


@router.get("/sources", dependencies=[Depends(require_permission("control.sources.read"))])
def list_sources(db: Session = Depends(get_db)) -> list[dict]:
    registry = SourceRegistryService(db)
    sources = registry.list_all()
    counts_by_source_id = _source_counts_by_id(db)
    return [
        _source_payload(source, counts_by_source_id)
        for source in sources
    ]


@router.post("/sources/logo/upload", dependencies=[Depends(require_permission("control.sources.edit"))])
def upload_source_logo(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    asset = MediaAssetService(db).save_upload(scope="sources", upload=file)
    db.commit()
    return {"ok": True, "image_asset_id": int(asset.id)}


@router.get("/sources/images/{image_id}")
def get_source_logo_image(image_id: int, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.query(ImageAsset).filter(ImageAsset.id == int(image_id)).one_or_none()
    if asset is None:
        raise NotFoundError("Изображение не найдено")
    return FileResponse(MediaAssetService(db).resolve_file_path(asset), media_type=asset.mime_type)


@router.patch("/sources/{source_key}/enabled", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_enabled(source_key: str, payload: EnabledPatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.is_enabled = bool(payload.enabled)
    db.commit()
    return _source_payload(entity, _source_counts_by_id(db))


@router.patch("/sources/{source_key}/sync-enabled", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_sync_enabled(source_key: str, payload: SyncEnabledPatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.is_sync_enabled = bool(payload.sync_enabled)
    db.commit()
    return _source_payload(entity, _source_counts_by_id(db))


@router.patch("/sources/{source_key}/dedup-enabled", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_dedup_enabled(source_key: str, payload: DedupEnabledPatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.dedup_enabled = bool(payload.dedup_enabled)
    db.commit()
    return _source_payload(entity, _source_counts_by_id(db))


@router.patch("/sources/{source_key}/hide-auto-added-products", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_hide_auto_added(source_key: str, payload: AutoHidePatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.hide_auto_added_products = bool(payload.hide_auto_added_products)
    db.commit()
    return _source_payload(entity, _source_counts_by_id(db))


@router.patch("/sources/{source_key}/attribute-visibility", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_attribute_visibility(source_key: str, payload: AttributeVisibilityPatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    if payload.description_mode is not None:
        description_mode = str(payload.description_mode or "").strip().lower()
        if description_mode not in {"hidden", "text", "html"}:
            raise ValidationError("Некорректный description_mode")
        setting.description_mode = description_mode
    if payload.show_images is not None:
        setting.show_images = bool(payload.show_images)
    db.commit()
    return _source_payload(entity, _source_counts_by_id(db))


@router.patch("/sources/{source_key}/supplier", dependencies=[Depends(require_permission("control.pricing.edit"))])
def patch_source_supplier(source_key: str, payload: SupplierPatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    if "supplier_id" in payload.model_fields_set:
        setting.supplier_id = int(payload.supplier_id) if payload.supplier_id is not None else None
    if "promo_factor" in payload.model_fields_set:
        setting.promo_factor = float(payload.promo_factor) if payload.promo_factor is not None else 1.0
    if "promo_only_no_discount" in payload.model_fields_set:
        setting.promo_only_no_discount = bool(payload.promo_only_no_discount)
    if "buyout_surcharge_value" in payload.model_fields_set:
        setting.buyout_surcharge_value = (
            float(payload.buyout_surcharge_value)
            if payload.buyout_surcharge_value is not None
            else None
        )
    if "buyout_surcharge_currency" in payload.model_fields_set:
        value = str(payload.buyout_surcharge_currency or "").strip().upper() or None
        setting.buyout_surcharge_currency = value
    db.commit()
    refreshed = repo.get_by_key(source_key)
    return _source_payload(refreshed, _source_counts_by_id(db))


@router.patch("/sources/{source_key}/logo", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_source_logo(source_key: str, payload: SourceLogoPatch, db: Session = Depends(get_db)) -> dict:
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    logo_image_asset_id = payload.logo_image_asset_id
    if logo_image_asset_id is not None:
        asset_exists = db.query(ImageAsset.id).filter(ImageAsset.id == int(logo_image_asset_id)).one_or_none()
        if asset_exists is None:
            raise ValidationError("Логотип не найден")
        entity.logo_image_asset_id = int(logo_image_asset_id)
    else:
        entity.logo_image_asset_id = None
    db.commit()
    refreshed = repo.get_by_key(source_key)
    return _source_payload(refreshed, _source_counts_by_id(db))


@router.get(
    "/internal/service/sources",
    include_in_schema=False,
    dependencies=[Depends(_require_internal_api_token)],
    response_model=list[InternalSourceEntryResponse],
)
def list_internal_service_sources(db: Session = Depends(get_db)) -> list[InternalSourceEntryResponse]:
    sources = SourceRegistryService(db).repo.list_registry_sources()
    items: list[InternalSourceEntryResponse] = []
    for source in sources:
        setting = SourceRegistryService(db).repo.ensure_setting(source)
        adapter_key = str(getattr(source, "adapter_key", "") or "").strip()
        if not adapter_key:
            continue
        items.append(
            InternalSourceEntryResponse(
                id=int(source.id),
                key=str(source.key),
                url=str(source.base_url),
                adapter_key=adapter_key,
                enabled=bool(getattr(setting, "is_enabled", True)),
                sync_enabled=bool(getattr(setting, "is_sync_enabled", True)),
                config=dict(getattr(source, "parser_config", None) or {}),
            )
        )
    return items


@router.post(
    "/internal/service/sources/bootstrap",
    include_in_schema=False,
    dependencies=[Depends(_require_internal_api_token)],
    status_code=status.HTTP_200_OK,
)
def bootstrap_internal_service_sources(payload: InternalSourceBootstrapRequest, db: Session = Depends(get_db)) -> dict:
    sources = SourceRegistryService(db).seed_from_payload([item.model_dump() for item in payload.sources])
    db.commit()
    return {"ok": True, "sources_count": len(sources)}
