from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import ParserProduct, ParserProductOriginVariant, ParserSource
from app.models.pricing import ParserSupplier

router = APIRouter(tags=["sources"])
_MANUAL_SOURCE_KEY = "__manual_admin_source__"
_MANUAL_SOURCE_NAME = "__manual_admin_source__"
_MANUAL_SOURCE_URL = "manual://admin/products"


def _service_sources_base() -> str:
    return f"{settings.service_base_url.rstrip('/')}/api/v1/sync/sources"


def _norm_host(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    return str(parsed.netloc or "").strip().lower()


def _service_list() -> list[dict]:
    try:
        res = requests.get(_service_sources_base(), timeout=(5, 30))
        res.raise_for_status()
        payload = res.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Service API unavailable: {exc}") from exc
    if not isinstance(payload, list):
        return []
    return [it for it in payload if isinstance(it, dict)]


def _service_patch(source_key: str, payload: dict) -> dict:
    try:
        res = requests.patch(f"{_service_sources_base()}/{source_key}", json=payload, timeout=(5, 30))
        res.raise_for_status()
        data = res.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Service API unavailable: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Invalid service response")
    return data


def _currency_priority_from_config(config: dict | None) -> list[str]:
    cfg = config if isinstance(config, dict) else {}
    currency_cfg = cfg.get("shopify_currency") if isinstance(cfg.get("shopify_currency"), dict) else {}
    raw = currency_cfg.get("requested_currency_priority")
    if not isinstance(raw, list):
        return ["USD", "EUR", "GBP"]
    out = [str(x).strip().upper() for x in raw if str(x).strip()]
    return out or ["USD", "EUR", "GBP"]


def _currency_method_from_config(config: dict | None) -> tuple[str, str]:
    cfg = config if isinstance(config, dict) else {}
    currency_cfg = cfg.get("shopify_currency") if isinstance(cfg.get("shopify_currency"), dict) else {}
    method = str(currency_cfg.get("method") or "priority_list").strip().lower()
    if method not in {"priority_list", "locked_param_currency", "locked_no_currency"}:
        method = "priority_list"
    locked_currency = str(currency_cfg.get("locked_currency") or "").strip().upper()
    if locked_currency == "GBR":
        locked_currency = "GBP"
    if locked_currency not in {"USD", "EUR", "GBP", "JPY"}:
        locked_currency = ""
    return method, locked_currency


def _find_profile(db: Session, source_key: str, source_url: str) -> ParserSource | None:
    key_host = _norm_host(source_key)
    url_host = _norm_host(source_url)
    for row in db.query(ParserSource).filter(ParserSource.deleted_at.is_(None)).all():
        row_url = str(row.url or "")
        row_name = str(row.name or "")
        if url_host and _norm_host(row_url) == url_host:
            return row
        if key_host and _norm_host(row_url) == key_host:
            return row
        if source_key.strip().lower() == row_name.strip().lower():
            return row
    return None


def _products_count(db: Session, source_id: int) -> int:
    direct_ids = {
        int(row[0])
        for row in (
            db.query(ParserProduct.id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.source_id == int(source_id))
            .all()
        )
    }
    origin_ids = {
        int(row[0])
        for row in (
            db.query(ParserProductOriginVariant.product_id)
            .join(ParserProduct, ParserProduct.id == ParserProductOriginVariant.product_id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProductOriginVariant.source_id == int(source_id))
            .all()
        )
    }
    return len(direct_ids | origin_ids)


def _manual_products_breakdown(db: Session, manual_source_id: int) -> tuple[int, int, int]:
    total_manual = int(
        db.query(ParserProduct.id)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.source_id == int(manual_source_id))
        .count()
    )
    bound_sync = int(
        db.query(ParserProduct.id)
        .join(ParserProductOriginVariant, ParserProductOriginVariant.product_id == ParserProduct.id)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.source_id == int(manual_source_id))
        .filter(ParserProductOriginVariant.source_id != int(manual_source_id))
        .distinct(ParserProduct.id)
        .count()
    )
    manual_only = max(0, total_manual - bound_sync)
    return total_manual, manual_only, bound_sync


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _variant_available(variant: object) -> bool:
    if not isinstance(variant, dict):
        return False
    raw = variant.get("available")
    if isinstance(raw, bool):
        return raw
    if raw is not None and str(raw).strip().lower() in {"1", "true", "yes", "y", "in_stock"}:
        return True
    try:
        qty = float(variant.get("inventory_quantity"))
        return qty > 0
    except Exception:
        return False


def _derive_status_from_variants(current_status: str, variants: object) -> str:
    normalized = str(current_status or "").strip().lower()
    if normalized == "unavailable":
        return normalized
    if isinstance(variants, list) and any(_variant_available(v) for v in variants):
        return "available"
    return "out_of_stock"


def _map_source(db: Session, item: dict) -> dict:
    source_key = str(item.get("key") or "").strip()
    source_url = str(item.get("url") or "").strip()
    profile = _find_profile(db, source_key, source_url)
    profile_id = int(profile.id) if profile is not None else 0
    last_sync_status = str(getattr(profile, "last_sync_status", "") or "").strip().lower() if profile is not None else ""
    is_password_protected = last_sync_status == "password_protected"
    cfg = item.get("config") if isinstance(item.get("config"), dict) else {}
    currency_method, locked_currency = _currency_method_from_config(cfg)
    source_mode = str(cfg.get("mode") or "auto").strip().lower()
    if source_mode not in {"auto", "manual"}:
        source_mode = "auto"
    seq = cfg.get("strategy_sequence") if isinstance(cfg.get("strategy_sequence"), list) else []
    normalized_seq = [str(x).strip().lower() for x in seq if str(x).strip()]
    is_auto_ingest = source_mode == "auto"
    return {
        "key": source_key,
        # Must match ParserProduct.source_id from backend DB, otherwise UI source mapping is corrupted.
        "source_id": profile_id,
        "service_source_id": int(item.get("id") or 0),
        "name": str(profile.name if profile is not None else source_key),
        "base_url": source_url,
        "parser_type": "parser",
        "enabled": _as_bool(getattr(profile, "enabled", None), True) if profile is not None else True,
        "sync_enabled": bool(item.get("sync_enabled", True)),
        "hide_auto_added_products": _as_bool(getattr(profile, "hide_auto_added_products", None), False) if profile is not None else False,
        "show_description": _as_bool(getattr(profile, "show_description", None), True) if profile is not None else True,
        "show_images": _as_bool(getattr(profile, "show_images", None), True) if profile is not None else True,
        "currency_priority": _currency_priority_from_config(item.get("config")),
        "currency_method": currency_method,
        "locked_currency": locked_currency,
        "currency_priority_editable": currency_method == "priority_list",
        "mode": source_mode,
        "notes": None,
        "status_label": None,
        "products_count": _products_count(db, profile_id) if profile is not None else 0,
        "categories_count": 0,
        "last_sync_at": getattr(profile, "last_sync_at", None) if profile is not None else None,
        "last_sync_duration_sec": int(getattr(profile, "last_sync_duration_sec", 0) or 0) if profile is not None else 0,
        "last_sync_status": str(getattr(profile, "last_sync_status", "") or "").strip() or None if profile is not None else None,
        "is_password_protected": is_password_protected,
        "is_auto_ingest": is_auto_ingest,
        "is_personal": False,
    }


def _get_or_create_manual_source(db: Session) -> ParserSource:
    existing = (
        db.query(ParserSource)
        .filter(ParserSource.deleted_at.is_(None))
        .filter(ParserSource.name == _MANUAL_SOURCE_NAME)
        .first()
    )
    if existing is not None:
        return existing
    supplier = db.query(ParserSupplier).order_by(ParserSupplier.id.asc()).first()
    if supplier is None:
        raise HTTPException(status_code=500, detail="Нет доступного supplier для личного источника")
    source = ParserSource(
        name=_MANUAL_SOURCE_NAME,
        url=_MANUAL_SOURCE_URL,
        enabled=True,
        supplier_id=int(supplier.id),
        show_description=True,
        show_images=True,
        hide_auto_added_products=False,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _map_manual_source(db: Session, source: ParserSource) -> dict:
    total_manual, manual_only, bound_sync = _manual_products_breakdown(db, int(source.id))
    return {
        "key": _MANUAL_SOURCE_KEY,
        "source_id": int(source.id),
        "service_source_id": 0,
        "name": "Личный каталог",
        "base_url": "",
        "parser_type": "backend_manual",
        "enabled": bool(source.enabled),
        "sync_enabled": True,
        "hide_auto_added_products": bool(getattr(source, "hide_auto_added_products", False)),
        "show_description": bool(getattr(source, "show_description", True)),
        "show_images": bool(getattr(source, "show_images", True)),
        "currency_priority": [],
        "currency_method": "priority_list",
        "locked_currency": "",
        "currency_priority_editable": False,
        "notes": None,
        "status_label": None,
        "products_count": total_manual,
        "manual_products_count": manual_only,
        "bound_sync_products_count": bound_sync,
        "categories_count": 0,
        "last_sync_at": getattr(source, "last_sync_at", None),
        "last_sync_duration_sec": None,
        "last_sync_status": None,
        "is_password_protected": None,
        "is_auto_ingest": None,
        "is_personal": True,
    }


class EnabledPayload(BaseModel):
    enabled: bool


class SyncEnabledPayload(BaseModel):
    sync_enabled: bool


class HideAutoPayload(BaseModel):
    hide_auto_added_products: bool


class AttrVisibilityPayload(BaseModel):
    show_description: bool | None = None
    show_images: bool | None = None


class CurrencyPriorityPayload(BaseModel):
    currency_priority: list[str] | None = None
    currency_method: str | None = None
    locked_currency: str | None = None


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)) -> list[dict]:
    manual_source = _get_or_create_manual_source(db)
    items = _service_list()
    mapped = [_map_source(db, item) for item in items]
    return [_map_manual_source(db, manual_source), *mapped]


@router.patch("/sources/{source_key}/enabled")
def patch_enabled(source_key: str, payload: EnabledPayload, db: Session = Depends(get_db)) -> dict:
    if source_key == _MANUAL_SOURCE_KEY:
        profile = _get_or_create_manual_source(db)
        profile.enabled = bool(payload.enabled)
        db.commit()
        db.refresh(profile)
        return _map_manual_source(db, profile)
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    profile = _find_profile(db, source_key, str(src.get("url") or ""))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"backend source profile not found: {source_key}")
    profile.enabled = bool(payload.enabled)
    db.commit()
    db.refresh(profile)
    return _map_source(db, src)


@router.patch("/sources/{source_key}/sync-enabled")
def patch_sync_enabled(source_key: str, payload: SyncEnabledPayload, db: Session = Depends(get_db)) -> dict:
    if source_key == _MANUAL_SOURCE_KEY:
        # Personal source sync toggle is UI-visible and accepted, but currently
        # manual source always participates as backend-only source.
        profile = _get_or_create_manual_source(db)
        return _map_manual_source(db, profile)
    _service_patch(source_key, {"sync_enabled": bool(payload.sync_enabled)})
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    return _map_source(db, src)


@router.patch("/sources/{source_key}/hide-auto-added-products")
def patch_hide_auto(source_key: str, payload: HideAutoPayload, db: Session = Depends(get_db)) -> dict:
    if source_key == _MANUAL_SOURCE_KEY:
        profile = _get_or_create_manual_source(db)
        profile.hide_auto_added_products = bool(payload.hide_auto_added_products)
        rows = (
            db.query(ParserProduct)
            .join(ParserProductOriginVariant, ParserProductOriginVariant.product_id == ParserProduct.id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProductOriginVariant.source_id == int(profile.id))
            .distinct(ParserProduct.id)
            .all()
        )
        if bool(payload.hide_auto_added_products):
            for product in rows:
                if bool(getattr(product, "is_auto_added", True)):
                    product.status = "hidden"
                    product.auto_hide_force_visible = False
        else:
            for product in rows:
                if str(getattr(product, "status", "")).strip().lower() != "hidden":
                    continue
                if not bool(getattr(product, "is_auto_added", True)):
                    continue
                product.status = _derive_status_from_variants(str(product.status or ""), product.variants)
                product.auto_hide_force_visible = False
        db.commit()
        db.refresh(profile)
        return _map_manual_source(db, profile)
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    profile = _find_profile(db, source_key, str(src.get("url") or ""))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"backend source profile not found: {source_key}")
    profile.hide_auto_added_products = bool(payload.hide_auto_added_products)
    rows = (
        db.query(ParserProduct)
        .join(ParserProductOriginVariant, ParserProductOriginVariant.product_id == ParserProduct.id)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProductOriginVariant.source_id == int(profile.id))
        .distinct(ParserProduct.id)
        .all()
    )
    if bool(payload.hide_auto_added_products):
        for product in rows:
            if bool(getattr(product, "is_auto_added", True)):
                product.status = "hidden"
                product.auto_hide_force_visible = False
    else:
        for product in rows:
            if str(getattr(product, "status", "")).strip().lower() != "hidden":
                continue
            if not bool(getattr(product, "is_auto_added", True)):
                continue
            product.status = _derive_status_from_variants(str(product.status or ""), product.variants)
            product.auto_hide_force_visible = False
    db.commit()
    db.refresh(profile)
    return _map_source(db, src)


@router.patch("/sources/{source_key}/attribute-visibility")
def patch_attr_visibility(source_key: str, payload: AttrVisibilityPayload, db: Session = Depends(get_db)) -> dict:
    if source_key == _MANUAL_SOURCE_KEY:
        profile = _get_or_create_manual_source(db)
        if payload.show_description is not None:
            setattr(profile, "show_description", bool(payload.show_description))
        if payload.show_images is not None:
            setattr(profile, "show_images", bool(payload.show_images))
        db.commit()
        db.refresh(profile)
        return _map_manual_source(db, profile)
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    profile = _find_profile(db, source_key, str(src.get("url") or ""))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"backend source profile not found: {source_key}")
    if payload.show_description is not None:
        setattr(profile, "show_description", bool(payload.show_description))
    if payload.show_images is not None:
        setattr(profile, "show_images", bool(payload.show_images))
    db.commit()
    db.refresh(profile)
    return _map_source(db, src)


@router.patch("/sources/{source_key}/currency-priority")
def patch_currency_priority(source_key: str, payload: CurrencyPriorityPayload, db: Session = Depends(get_db)) -> dict:
    if source_key == _MANUAL_SOURCE_KEY:
        raise HTTPException(status_code=400, detail="Для личного источника приоритет валют не используется")
    service_payload: dict = {}
    if payload.currency_priority is not None:
        normalized = [str(x).strip().upper() for x in payload.currency_priority if str(x).strip()]
        if not normalized:
            raise HTTPException(status_code=400, detail="currency_priority is empty")
        service_payload["requested_currency_priority"] = normalized
    if payload.currency_method is not None:
        method = str(payload.currency_method).strip().lower()
        if method not in {"priority_list", "locked_param_currency", "locked_no_currency"}:
            raise HTTPException(status_code=400, detail="invalid currency_method")
        service_payload["currency_method"] = method
    if payload.locked_currency is not None:
        locked = str(payload.locked_currency).strip().upper()
        if locked == "GBR":
            locked = "GBP"
        if locked and locked not in {"USD", "EUR", "GBP", "JPY"}:
            raise HTTPException(status_code=400, detail="invalid locked_currency")
        service_payload["locked_currency"] = locked
    if not service_payload:
        raise HTTPException(status_code=400, detail="empty currency payload")
    _service_patch(source_key, service_payload)
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    return _map_source(db, src)
