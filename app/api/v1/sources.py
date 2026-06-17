from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
import requests

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import NotFoundError, ValidationError
from app.models import Product, ProductListing, ProductListingMember, Source
from app.services.auth.admin_auth_service import require_permission
from app.services.catalog.source_registry_service import SourceRegistryService


router = APIRouter(tags=["sources"])


class EnabledPatch(BaseModel):
    enabled: bool


class SyncEnabledPatch(BaseModel):
    sync_enabled: bool


class AutoHidePatch(BaseModel):
    hide_auto_added_products: bool


class AttributeVisibilityPatch(BaseModel):
    description_mode: str | None = None
    show_images: bool | None = None


class SupplierPatch(BaseModel):
    supplier_id: int | None = None


def _service_sources_payload() -> dict[str, dict]:
    try:
        response = requests.get(f"{settings.service_base_url.rstrip('/')}/api/v1/sync/sources", timeout=(3, 20))
        response.raise_for_status()
        payload = response.json()
    except Exception:
        payload = []
    items = payload if isinstance(payload, list) else []
    return {
        str(item.get("key") or "").strip().lower(): item
        for item in items
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }


def _source_payload(db: Session, source: Source, service_item: dict | None) -> dict:
    products_count = (
        db.query(func.count(func.distinct(Product.id)))
        .join(ProductListingMember, ProductListingMember.product_id == Product.id)
        .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
        .filter(Product.lifecycle_status != "merged")
        .filter(ProductListing.source_id == int(source.id))
        .scalar()
        or 0
    )
    bound_sync_products_count = (
        db.query(func.count(func.distinct(Product.id)))
        .join(ProductListingMember, ProductListingMember.product_id == Product.id)
        .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
        .filter(Product.lifecycle_status != "merged")
        .filter(ProductListing.source_id == int(source.id))
        .filter(ProductListing.ingest_mode == "sync")
        .scalar()
        or 0
    )
    manual_products_count = (
        db.query(func.count(func.distinct(Product.id)))
        .join(ProductListingMember, ProductListingMember.product_id == Product.id)
        .join(ProductListing, ProductListing.id == ProductListingMember.listing_id)
        .filter(Product.lifecycle_status != "merged")
        .filter(ProductListing.source_id == int(source.id))
        .filter(ProductListing.ingest_mode == "manual")
        .scalar()
        or 0
    )
    setting = source.setting
    sync_state = source.sync_state
    supplier = getattr(setting, "supplier", None) if setting is not None else None
    return {
        "key": source.key,
        "source_id": int(source.id),
        "name": source.name,
        "base_url": source.base_url,
        "enabled": bool(getattr(setting, "is_enabled", True)),
        "sync_enabled": bool(getattr(setting, "is_sync_enabled", True)),
        "hide_auto_added_products": bool(getattr(setting, "hide_auto_added_products", False)),
        "description_mode": str(getattr(setting, "description_mode", "text") or "text"),
        "show_images": bool(getattr(setting, "show_images", True)),
        "products_count": int(products_count),
        "manual_products_count": int(manual_products_count),
        "bound_sync_products_count": int(bound_sync_products_count),
        "last_sync_at": sync_state.last_sync_at.isoformat() if getattr(sync_state, "last_sync_at", None) else None,
        "last_sync_duration_sec": getattr(sync_state, "last_sync_duration_sec", None),
        "last_sync_status": getattr(sync_state, "last_sync_status", None),
        "supplier_id": int(getattr(setting, "supplier_id", 0) or 0) or None,
        "supplier_key": getattr(supplier, "key", None),
        "supplier_name": getattr(supplier, "name", None),
        "promo_factor": float(getattr(setting, "promo_factor", 1) or 1),
        "promo_only_no_discount": bool(getattr(setting, "promo_only_no_discount", False)),
        "buyout_surcharge_value": float(getattr(setting, "buyout_surcharge_value", 0) or 0),
        "buyout_surcharge_currency": str(getattr(setting, "buyout_surcharge_currency", "RUB") or "RUB"),
    }


@router.get("/sources", dependencies=[Depends(require_permission("control.sources.read"))])
def list_sources(db: Session = Depends(get_db)) -> list[dict]:
    registry = SourceRegistryService(db)
    sources = registry.refresh_from_service()
    service_items = _service_sources_payload()
    return [_source_payload(db, source, service_items.get(source.key)) for source in sources]


@router.patch("/sources/{source_key}/enabled", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_enabled(source_key: str, payload: EnabledPatch, db: Session = Depends(get_db)) -> dict:
    SourceRegistryService(db).refresh_from_service()
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.is_enabled = bool(payload.enabled)
    db.commit()
    return _source_payload(db, entity, _service_sources_payload().get(entity.key))


@router.patch("/sources/{source_key}/sync-enabled", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_sync_enabled(source_key: str, payload: SyncEnabledPatch, db: Session = Depends(get_db)) -> dict:
    SourceRegistryService(db).refresh_from_service()
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.is_sync_enabled = bool(payload.sync_enabled)
    db.commit()
    return _source_payload(db, entity, _service_sources_payload().get(entity.key))


@router.patch("/sources/{source_key}/hide-auto-added-products", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_hide_auto_added(source_key: str, payload: AutoHidePatch, db: Session = Depends(get_db)) -> dict:
    SourceRegistryService(db).refresh_from_service()
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.hide_auto_added_products = bool(payload.hide_auto_added_products)
    db.commit()
    return _source_payload(db, entity, _service_sources_payload().get(entity.key))


@router.patch("/sources/{source_key}/attribute-visibility", dependencies=[Depends(require_permission("control.sources.edit"))])
def patch_attribute_visibility(source_key: str, payload: AttributeVisibilityPatch, db: Session = Depends(get_db)) -> dict:
    SourceRegistryService(db).refresh_from_service()
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
    return _source_payload(db, entity, _service_sources_payload().get(entity.key))


@router.patch("/sources/{source_key}/supplier", dependencies=[Depends(require_permission("control.pricing.edit"))])
def patch_source_supplier(source_key: str, payload: SupplierPatch, db: Session = Depends(get_db)) -> dict:
    SourceRegistryService(db).refresh_from_service()
    repo = SourceRegistryService(db).repo
    entity = repo.get_by_key(source_key)
    if entity is None:
        raise NotFoundError("Источник не найден")
    setting = repo.ensure_setting(entity)
    setting.supplier_id = int(payload.supplier_id) if payload.supplier_id is not None else None
    db.commit()
    refreshed = repo.get_by_key(source_key)
    return _source_payload(db, refreshed, _service_sources_payload().get(refreshed.key))
