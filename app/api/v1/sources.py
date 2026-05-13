from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import ParserProduct, ParserSource

router = APIRouter(tags=["sources"])


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
    return int(
        db.query(ParserProduct)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.source_id == int(source_id))
        .count()
    )


def _map_source(db: Session, item: dict) -> dict:
    source_key = str(item.get("key") or "").strip()
    source_url = str(item.get("url") or "").strip()
    profile = _find_profile(db, source_key, source_url)
    profile_id = int(profile.id) if profile is not None else 0
    return {
        "key": source_key,
        "source_id": int(item.get("id") or 0),
        "name": str(profile.name if profile is not None else source_key),
        "base_url": source_url,
        "parser_type": "parser",
        "enabled": bool(profile.enabled) if profile is not None else True,
        "sync_enabled": bool(item.get("sync_enabled", True)),
        "hide_auto_added_products": bool(profile.hide_auto_added_products) if profile is not None else False,
        "show_description": bool(getattr(profile, "show_description", True)) if profile is not None else True,
        "show_images": bool(getattr(profile, "show_images", True)) if profile is not None else True,
        "currency_priority": _currency_priority_from_config(item.get("config")),
        "notes": None,
        "status_label": None,
        "products_count": _products_count(db, profile_id) if profile is not None else 0,
        "categories_count": 0,
        "last_sync_at": None,
        "last_sync_duration_sec": None,
        "last_sync_status": None,
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
    currency_priority: list[str]


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)) -> list[dict]:
    items = _service_list()
    return [_map_source(db, item) for item in items]


@router.patch("/sources/{source_key}/enabled")
def patch_enabled(source_key: str, payload: EnabledPayload, db: Session = Depends(get_db)) -> dict:
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
    _service_patch(source_key, {"sync_enabled": bool(payload.sync_enabled)})
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    return _map_source(db, src)


@router.patch("/sources/{source_key}/hide-auto-added-products")
def patch_hide_auto(source_key: str, payload: HideAutoPayload, db: Session = Depends(get_db)) -> dict:
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    profile = _find_profile(db, source_key, str(src.get("url") or ""))
    if profile is None:
        raise HTTPException(status_code=404, detail=f"backend source profile not found: {source_key}")
    profile.hide_auto_added_products = bool(payload.hide_auto_added_products)
    db.commit()
    db.refresh(profile)
    return _map_source(db, src)


@router.patch("/sources/{source_key}/attribute-visibility")
def patch_attr_visibility(source_key: str, payload: AttrVisibilityPayload, db: Session = Depends(get_db)) -> dict:
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
    normalized = [str(x).strip().upper() for x in payload.currency_priority if str(x).strip()]
    if not normalized:
        raise HTTPException(status_code=400, detail="currency_priority is empty")
    _service_patch(source_key, {"requested_currency_priority": normalized})
    items = _service_list()
    src = next((it for it in items if str(it.get("key") or "").strip() == source_key), None)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    return _map_source(db, src)
