"""Products API: backend is source of truth for read-side pricing."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import random
import re
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (
    ImageAsset,
    ParserBrandMapping,
    ParserCategory,
    ParserCategoryManualProduct,
    ParserDedupDecision,
    ParserFavoriteProduct,
    ParserProduct,
    ParserProductCategoryMatch,
    ParserProductOriginVariant,
    ParserSource,
)
from app.models.pricing import ParserSupplier
from app.repositories import (
    ParserCategoryKeywordRepository,
    ParserCategoryManualProductRepository,
    ParserCategoryRepository,
    ParserProductRepository,
    ParserSourceRepository,
)
from app.schemas.parser import (
    BrandMappingListResponse,
    BrandMappingUpdateRequest,
    CatalogProductsResponse,
    PricingExampleProductResponse,
    ShowcaseProductResponse,
)
from app.services.catalog.category_index_service import CategoryIndexService
from app.services.catalog.category_tree_utils import build_tree
from app.services.proxy.service_api_proxy import forward_service_request
from app.services.auth.admin_auth_service import require_admin_access
from app.services.brand_mapping_service import BrandMappingService
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.weight_rule_service import WeightRuleService


router = APIRouter(tags=["products"])
LOGGER = logging.getLogger(__name__)
_PROXY_ROOT_METHODS = ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_PROXY_PATH_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_JSON_UNSAFE_HEADERS = {"content-length", "content-encoding", "transfer-encoding"}
_ALLOWED_PRODUCT_STATUSES = {"available", "out_of_stock", "hidden", "unavailable"}
_PUBLIC_PRODUCT_STATUSES = {"available", "out_of_stock", "hidden"}
_CATALOG_MAX_LIMIT = 120
_CATALOG_SCAN_BATCH = 240
_CATALOG_MAX_SCAN_PAGES = 8
_NO_BRAND_FILTER_TOKEN = "__NO_BRAND__"
_PRODUCT_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads" / "products"
_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MANUAL_SOURCE_NAME = "__manual_admin_source__"
_MANUAL_SOURCE_URL = "manual://admin/products"


class InvalidProductStatusError(ValueError):
    """Raised when product status in storage is outside the allowed set."""


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace(",", ".")
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _assert_allowed_product_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _ALLOWED_PRODUCT_STATUSES:
        raise InvalidProductStatusError(f"Invalid product status in storage: {normalized!r}")
    return normalized


class ProductVariantIn(BaseModel):
    title: str
    price: float | None = None
    available: bool = True


class CreateManualProductRequest(BaseModel):
    title: str
    description: str | None = None
    vendor: str | None = None
    product_type: str | None = None
    currency: str = "USD"
    variants: list[ProductVariantIn] = []
    manual_image_asset_ids: list[int] = []
    weight_grams: float | None = None
    status: str | None = None
    bind_sync: bool = False
    bind_source_id: int | None = None
    bind_source_product_url: str | None = None


class UpdateManualProductRequest(BaseModel):
    title: str
    description: str | None = None
    vendor: str | None = None
    product_type: str | None = None
    currency: str = "USD"
    variants: list[ProductVariantIn] = []
    manual_image_asset_ids: list[int] = []
    weight_grams: float | None = None
    status: str | None = None
    bind_sync: bool = False
    bind_source_id: int | None = None
    bind_source_product_url: str | None = None


class ProductUrlPayload(BaseModel):
    url: str


class ProductImageUrlPayload(BaseModel):
    url: str


def _normalize_manual_variants(raw_variants: list[ProductVariantIn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw_variants:
        title = str(item.title or "").strip()
        if not title:
            continue
        price = _safe_float(item.price)
        out.append(
            {
                "title": title,
                "price": f"{price:.2f}" if price is not None and price >= 0 else None,
                "available": bool(item.available),
                "inventory_quantity": 1 if bool(item.available) else 0,
            }
        )
    if out:
        return out
    return [{"title": "Default", "price": None, "available": True, "inventory_quantity": 1}]


def _normalize_url_loose(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urlparse(value)
    except Exception:
        return value.rstrip("/")
    scheme = str(parsed.scheme or "https").lower()
    netloc = str(parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = str(parsed.path or "").rstrip("/")
    # Normalize locale-prefixed shop paths:
    # /en/products/x, /en-ae/products/x => /products/x
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", parts[0].lower()) and parts[1].lower() == "products":
        path = "/" + "/".join(parts[1:])
    return f"{scheme}://{netloc}{path}"


def _norm_host(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    return str(parsed.netloc or "").strip().lower().removeprefix("www.")


def _with_active_sources(query):
    return query.filter(
        or_(
            select(ParserSource.id)
            .where(ParserSource.id == ParserProduct.source_id)
            .where(ParserSource.deleted_at.is_(None))
            .where(ParserSource.enabled.is_(True))
            .exists(),
            select(ParserProductOriginVariant.id)
            .join(ParserSource, ParserSource.id == ParserProductOriginVariant.source_id)
            .where(ParserProductOriginVariant.product_id == ParserProduct.id)
            .where(ParserSource.deleted_at.is_(None))
            .where(ParserSource.enabled.is_(True))
            .exists(),
        )
    )


def _source_profile_map_for_products(
    db: Session,
    *,
    product_ids: set[int],
    fallback_source_ids: set[int] | None = None,
) -> dict[int, ParserSource]:
    if not product_ids and not fallback_source_ids:
        return {}
    origin_source_ids = {
        int(row[0])
        for row in (
            db.query(ParserProductOriginVariant.source_id)
            .filter(ParserProductOriginVariant.product_id.in_(list(product_ids)))
            .distinct()
            .all()
        )
        if row and row[0] is not None
    }
    if fallback_source_ids:
        origin_source_ids.update(int(source_id) for source_id in fallback_source_ids)
    if not origin_source_ids:
        return {}
    source_repo = ParserSourceRepository(db)
    return {
        int(source.id): source
        for source in source_repo.get_active_by_ids(origin_source_ids)
    }


def _primary_source_ids_for_products(
    db: Session,
    *,
    product_ids: set[int],
    fallback_source_ids_by_product: dict[int, int] | None = None,
) -> dict[int, int]:
    if not product_ids and not fallback_source_ids_by_product:
        return {}
    rows = (
        db.query(
            ParserProductOriginVariant.product_id.label("product_id"),
            func.min(ParserProductOriginVariant.source_id).label("source_id"),
        )
        .filter(ParserProductOriginVariant.product_id.in_(list(product_ids or set())))
        .group_by(ParserProductOriginVariant.product_id)
        .all()
        if product_ids
        else []
    )
    out = {
        int(row.product_id): int(row.source_id)
        for row in rows
        if row.product_id is not None and row.source_id is not None
    }
    for product_id, source_id in (fallback_source_ids_by_product or {}).items():
        out.setdefault(int(product_id), int(source_id))
    return out


def _inject_effective_source_id(
    item: dict[str, Any],
    *,
    product_id: int | None,
    primary_source_by_product: dict[int, int],
) -> None:
    if product_id is None:
        return
    current_source_id = _safe_int(item.get("source_id"))
    # Keep native source for product records (especially manual/personal source products).
    # Origin variants are used as linkage metadata and should not overwrite base ownership.
    if current_source_id is not None and current_source_id > 0:
        return
    source_id = primary_source_by_product.get(int(product_id))
    if source_id is None:
        return
    item["source_id"] = int(source_id)


def _variant_is_available(variant: Any) -> bool:
    if not isinstance(variant, dict):
        return False
    raw_available = variant.get("available")
    if isinstance(raw_available, bool):
        if raw_available:
            return True
    elif raw_available is not None:
        if str(raw_available).strip().lower() in {"1", "true", "yes", "y", "in_stock"}:
            return True
    inventory = _safe_float(variant.get("inventory_quantity"))
    if inventory is not None and inventory > 0:
        return True
    return False


def _effective_status_from_variants(
    stored_status: str,
    variants: Any,
    *,
    source_profile: Any | None = None,
    is_auto_added: bool = True,
    auto_hide_force_visible: bool = False,
) -> str:
    if stored_status in {"hidden", "unavailable"}:
        return stored_status
    source_auto_hide = bool(getattr(source_profile, "hide_auto_added_products", False))
    if source_auto_hide and bool(is_auto_added) and not bool(auto_hide_force_visible):
        return "hidden"
    if not isinstance(variants, list) or len(variants) == 0:
        return stored_status
    if any(_variant_is_available(item) for item in variants):
        return "available"
    return "out_of_stock"


def _normalize_int_list(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    values: list[int] = []
    seen: set[int] = set()
    for item in raw:
        parsed = _safe_int(item)
        if parsed is None:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        values.append(parsed)
    return values


def _normalize_image_order_tokens(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = str(item or "").strip()
        if not token:
            continue
        if not (token.startswith("s:") or token.startswith("m:")):
            continue
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _normalized_vendor_key_expr():
    return func.regexp_replace(
        func.lower(func.trim(func.coalesce(ParserProduct.vendor, ""))),
        "[^[:alnum:]]+",
        "",
        "g",
    )


def _mapped_vendor_label_expr():
    vendor_key_expr = _normalized_vendor_key_expr()
    mapped_vendor_subquery = (
        select(ParserBrandMapping.target_brand)
        .where(ParserBrandMapping.source_brand_key == vendor_key_expr)
        .limit(1)
        .scalar_subquery()
    )
    return func.coalesce(func.trim(mapped_vendor_subquery), func.trim(ParserProduct.vendor))


def _normalized_mapped_vendor_key_expr():
    return func.regexp_replace(
        func.lower(func.trim(func.coalesce(_mapped_vendor_label_expr(), ""))),
        "[^[:alnum:]]+",
        "",
        "g",
    )


def _apply_brand_mapping_to_item(item: dict[str, Any], mapping_service: BrandMappingService, mapping_by_key: dict[str, str]) -> None:
    vendor_original, vendor_mapped, vendor_display = mapping_service.resolve_vendor(item.get("vendor"), mapping_by_key)
    item["vendor_original"] = vendor_original
    item["vendor_mapped"] = vendor_mapped
    item["vendor_display"] = vendor_display
    item["vendor"] = vendor_display


def _compose_effective_image_urls(
    *,
    source_image_urls: list[str],
    images_sync_locked: bool,
    hidden_source_image_urls: list[str],
    manual_image_urls: list[str],
    manual_image_order: list[str],
) -> list[str]:
    if not images_sync_locked:
        return list(source_image_urls)

    hidden_set = set(hidden_source_image_urls)
    visible_source = [url for url in source_image_urls if url not in hidden_set]
    source_map = {f"s:{url}": url for url in visible_source}
    manual_map = {f"m:{url}": url for url in manual_image_urls}
    merged_map: dict[str, str] = {}
    merged_map.update(source_map)
    merged_map.update(manual_map)
    ordered: list[str] = []
    used: set[str] = set()
    for token in manual_image_order:
        image_url = merged_map.get(token)
        if image_url is None or image_url in used:
            continue
        used.add(image_url)
        ordered.append(image_url)
    for image_url in [*visible_source, *manual_image_urls]:
        if image_url in used:
            continue
        used.add(image_url)
        ordered.append(image_url)
    return ordered


def _apply_product_overrides_to_item(
    item: dict[str, Any],
    product: ParserProduct | None,
    *,
    default_show_description: bool = True,
    apply_visibility_rules: bool = True,
) -> None:
    if product is None:
        return
    title_sync_locked = bool(getattr(product, "title_sync_locked", False))
    description_sync_locked = bool(getattr(product, "description_sync_locked", False))
    description_visible_override = getattr(product, "description_visible_override", None)
    images_sync_locked = bool(getattr(product, "images_sync_locked", False))
    title_override = getattr(product, "title_override", None)
    description_override = getattr(product, "description_override", None)
    hidden_source_image_urls = _normalize_image_urls(getattr(product, "hidden_source_image_asset_ids", None))
    manual_image_urls = _normalize_image_urls(getattr(product, "manual_image_asset_ids", None))
    manual_image_order = _normalize_image_order_tokens(getattr(product, "manual_image_order", None))

    source_image_urls = _normalize_image_urls(item.get("image_urls"))
    # Manual products store all images as manual assets. Do not treat them as source images.
    product_url = str(getattr(product, "url", "") or "").strip().lower()
    if product_url.startswith("manual://product/") and manual_image_urls:
        source_image_urls = []
    effective_image_urls = _compose_effective_image_urls(
        source_image_urls=source_image_urls,
        images_sync_locked=images_sync_locked,
        hidden_source_image_urls=hidden_source_image_urls,
        manual_image_urls=manual_image_urls,
        manual_image_order=manual_image_order,
    )

    if title_sync_locked and title_override is not None:
        item["title"] = str(title_override)
    if description_sync_locked and description_override is not None:
        item["description"] = str(description_override)
    effective_show_description = (
        bool(description_visible_override)
        if description_visible_override is not None
        else bool(default_show_description)
    )
    # Do not erase description payload for admin product pages.
    # Visibility is represented by product_edit.description_visible_effective and handled by UI.
    item["image_urls"] = effective_image_urls
    item["image_count"] = len(effective_image_urls)
    item["product_edit"] = {
        "title_sync_locked": title_sync_locked,
        "description_sync_locked": description_sync_locked,
        "description_visible_override": description_visible_override,
        "description_visible_effective": effective_show_description,
        "images_sync_locked": images_sync_locked,
        "title_override": title_override,
        "description_override": description_override,
        "hidden_source_image_urls": hidden_source_image_urls,
        "manual_image_urls": manual_image_urls,
        "manual_image_order": manual_image_order,
        "source_image_urls": source_image_urls,
    }


def _normalize_image_urls(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        url = str(item or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _save_manual_product_image(file: UploadFile, db: Session) -> int:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не передан")
    extension = Path(file.filename).suffix.lower()
    if extension not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый формат изображения")
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не является корректным изображением")
    _PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in Path(file.filename).stem.lower()).strip("-") or "product"
    file_name = f"{safe_stem}-{int(datetime.now().timestamp() * 1000)}-{random.randint(1000, 9999)}{extension}"
    target = _PRODUCT_UPLOAD_DIR / file_name
    target.write_bytes(content)
    asset = ImageAsset(
        source_url=f"stored://product/{file_name}",
        storage_mode="stored_file",
        stored_path=str(target),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return int(asset.id)


def _save_manual_product_image_bytes(*, file_name: str, content: bytes, db: Session) -> int:
    extension = Path(file_name).suffix.lower()
    if extension not in _ALLOWED_IMAGE_EXTENSIONS:
        extension = ".jpg"
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    try:
        with Image.open(BytesIO(content)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не является корректным изображением")
    _PRODUCT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in Path(file_name).stem.lower()).strip("-") or "product"
    final_name = f"{safe_stem}-{int(datetime.now().timestamp() * 1000)}-{random.randint(1000, 9999)}{extension}"
    target = _PRODUCT_UPLOAD_DIR / final_name
    target.write_bytes(content)
    asset = ImageAsset(source_url=f"stored://product/{final_name}", storage_mode="stored_file", stored_path=str(target))
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return int(asset.id)


def _extract_product_description(item: dict[str, Any]) -> str | None:
    for key in ("description", "body_html", "body"):
        value = item.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


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
        raise HTTPException(status_code=500, detail="Нет доступного supplier для ручного источника")
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
    db.flush()
    return source


def _extract_buyout_price_rub(item: dict[str, Any]) -> float | None:
    components = item.get("pricing_components")
    if not isinstance(components, dict):
        return None
    for key in ("buyout_rub", "buyout_price_rub", "buyout"):
        parsed = _safe_float(components.get(key))
        if parsed is not None:
            return parsed
    return None


def _project_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(item.get("id") or 0),
        "source_id": int(item.get("source_id") or 0),
        "source_name": str(item.get("source_name") or ""),
        "title": str(item.get("title") or ""),
        "vendor": item.get("vendor"),
        "vendor_original": item.get("vendor_original"),
        "vendor_mapped": item.get("vendor_mapped"),
        "vendor_display": item.get("vendor_display"),
        "url": str(item.get("url") or ""),
        "price": _safe_float(item.get("price")),
        "currency": str(item.get("currency") or "RUB"),
        "source_price": _safe_float(item.get("source_price")),
        "source_currency": item.get("source_currency"),
        "status": str(item.get("status") or "hidden"),
        "image_count": int(item.get("image_count") or 0),
        "image_urls": list(item.get("image_urls") or []),
        "buyout_price_rub": _extract_buyout_price_rub(item),
        "is_favorite": bool(item.get("is_favorite")),
    }


def _project_showcase_product_detail(item: dict[str, Any]) -> dict[str, Any]:
    components = item.get("pricing_components")
    if not isinstance(components, dict):
        components = {}
    product_edit = item.get("product_edit")
    if not isinstance(product_edit, dict):
        product_edit = {}
    return {
        "id": int(item.get("id") or 0),
        "source_id": int(item.get("source_id") or 0),
        "title": str(item.get("title") or ""),
        "vendor": item.get("vendor"),
        "vendor_original": item.get("vendor_original"),
        "vendor_mapped": item.get("vendor_mapped"),
        "vendor_display": item.get("vendor_display"),
        "url": str(item.get("url") or ""),
        "price": _safe_float(item.get("price")),
        "currency": str(item.get("currency") or "RUB"),
        "source_price": _safe_float(item.get("source_price")),
        "source_currency": item.get("source_currency"),
        "final_price": _safe_float(item.get("final_price")),
        "final_currency": item.get("final_currency"),
        "status": str(item.get("status") or "hidden"),
        "image_urls": list(item.get("image_urls") or []),
        "variants": list(item.get("variants") or []),
        "internal_category_name": item.get("internal_category_name"),
        "internal_category_names": list(item.get("internal_category_names") or []),
        "description": item.get("description"),
        "pricing_components": components,
        "product_edit": product_edit,
    }


def _status_sort_rank(raw_status: Any) -> int:
    normalized = str(raw_status or "").strip().lower()
    return 0 if normalized == "available" else 1


def _vendor_sort_value(item: dict[str, Any]) -> str:
    return (
        str(
            item.get("vendor_display")
            or item.get("vendor_mapped")
            or item.get("vendor")
            or item.get("vendor_original")
            or ""
        )
        .strip()
        .casefold()
    )


def _sort_products_for_display(items: list[dict[str, Any]]) -> None:
    items.sort(
        key=lambda item: (
            _status_sort_rank(item.get("status")),
            _vendor_sort_value(item),
            str(item.get("title") or "").strip().casefold(),
            int(item.get("id") or 0),
        )
    )


def _resolve_primary_image_url(item: dict[str, Any]) -> str | None:
    image_urls = _normalize_image_urls(item.get("image_urls"))
    if image_urls:
        return image_urls[0]
    return None


def _encode_cursor(updated_at: datetime, product_id: int) -> str:
    payload = f"{updated_at.isoformat()}|{int(product_id)}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(raw_cursor: str) -> tuple[datetime, int]:
    padded = raw_cursor + ("=" * ((4 - len(raw_cursor) % 4) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        ts_raw, product_id_raw = decoded.rsplit("|", 1)
        ts = datetime.fromisoformat(ts_raw)
        product_id = int(product_id_raw)
        return ts, product_id
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный cursor") from exc


def _cursor_parts_for_product(product: ParserProduct) -> tuple[datetime, int]:
    timestamp = product.updated_at or product.created_at
    if timestamp is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="У товара отсутствует timestamp для cursor")
    return timestamp, int(product.id)


def _upstream_json_or_none(upstream: Response) -> Any | None:
    body = getattr(upstream, "body", b"") or b""
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _json_response_headers(upstream: Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in upstream.headers.items():
        if key.lower() in _JSON_UNSAFE_HEADERS:
            continue
        headers[key] = value
    return headers


def _normalize_source_price(raw_price: Any, currency: str | None) -> float | None:
    _ = currency
    return _safe_float(raw_price)


def _price_input_from_item(item: dict[str, Any]) -> tuple[float | None, str | None]:
    source_currency_raw = item.get("source_currency")
    if source_currency_raw is None:
        source_currency_raw = item.get("currency")
    source_currency = str(source_currency_raw).upper() if source_currency_raw is not None else None
    raw_source_price = item.get("source_price")
    if raw_source_price is None:
        raw_source_price = item.get("price")
    source_price = _normalize_source_price(raw_source_price, source_currency)
    return source_price, source_currency


def _apply_backend_pricing_to_item(
    *,
    item: dict[str, Any],
    settings_service: PricingSettingsService,
    settings,
    source_profile_map: dict[int, Any],
    weight_rules: list[Any],
    apply_source_visibility_rules: bool = True,
    favorite_manual_category_ids_by_product: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    source_id = _safe_int(item.get("source_id"))
    source_profile = source_profile_map.get(source_id) if source_id is not None else None
    source_price, source_currency = _price_input_from_item(item)
    weight_grams = _safe_float(item.get("weight_grams"))
    weight_source = str(item.get("weight_source") or "").strip().lower()
    if weight_grams is None or weight_grams <= 0 or weight_source == "missing":
        matched_weight = WeightRuleService.match_weight_from_rules(
            rules=weight_rules,
            title=str(item.get("title") or "") or None,
            vendor=str(item.get("vendor") or "") or None,
            product_type=str(item.get("product_type") or "") or None,
            handle=str(item.get("handle") or "") or None,
        )
        if matched_weight.weight_grams is not None and matched_weight.weight_grams > 0:
            weight_grams = matched_weight.weight_grams
            item["weight_grams"] = matched_weight.weight_grams
            item["weight_match_keyword"] = matched_weight.matched_keyword
            item["weight_source"] = "fallback_rule" if matched_weight.matched_keyword == "fallback_default" else "keyword_rule"
    pricing = settings_service.calculate_for_product(
        source_price=source_price,
        source_currency=source_currency,
        weight_grams=weight_grams,
        supplier_id=int(source_profile.supplier_id) if source_profile and source_profile.supplier_id is not None else None,
        promo_factor=(
            float(source_profile.promo_factor)
            if source_profile is not None and getattr(source_profile, "promo_factor", None) is not None
            else None
        ),
        promo_only_no_discount=(
            bool(source_profile.promo_only_no_discount)
            if source_profile is not None and getattr(source_profile, "promo_only_no_discount", None) is not None
            else None
        ),
        buyout_surcharge_value=(
            float(source_profile.buyout_surcharge_value)
            if source_profile is not None and getattr(source_profile, "buyout_surcharge_value", None) is not None
            else None
        ),
        buyout_surcharge_currency=(
            str(source_profile.buyout_surcharge_currency)
            if source_profile is not None and getattr(source_profile, "buyout_surcharge_currency", None) is not None
            else None
        ),
        variants=item.get("variants") if isinstance(item.get("variants"), list) else [],
        settings=settings,
    )

    item["source_price"] = source_price
    item["source_currency"] = source_currency
    item["final_price"] = pricing.final_price_rub
    item["final_currency"] = "RUB" if pricing.final_price_rub is not None else None
    item["pricing_manual_required"] = pricing.manual_required
    item["pricing_reason"] = pricing.reason
    item["pricing_components"] = {
        **pricing.components,
        "pricing_calculation_owner": "backend",
    }
    if pricing.final_price_rub is not None:
        item["price"] = pricing.final_price_rub
        item["currency"] = "RUB"
    else:
        item["price"] = source_price
        item["currency"] = source_currency
    normalized_status = _assert_allowed_product_status(item.get("status"))
    item["status"] = _effective_status_from_variants(
        normalized_status,
        item.get("variants"),
        source_profile=source_profile,
        is_auto_added=bool(item.get("is_auto_added", True)),
        auto_hide_force_visible=bool(item.get("auto_hide_force_visible", False)),
    )
    if source_profile is not None and not bool(getattr(source_profile, "enabled", True)):
        item["status"] = "unavailable"

    # Source-level attribute visibility rules are for public projection.
    if apply_source_visibility_rules and source_profile is not None and not bool(getattr(source_profile, "show_description", True)):
        product_edit = item.get("product_edit")
        if isinstance(product_edit, dict):
            product_edit["description_visible_effective"] = False

    if apply_source_visibility_rules and source_profile is not None and not bool(getattr(source_profile, "show_images", True)):
        product_edit = item.get("product_edit")
        manual_urls = []
        manual_order = []
        if isinstance(product_edit, dict):
            manual_urls = _normalize_image_urls(product_edit.get("manual_image_urls"))
            manual_order = _normalize_image_order_tokens(product_edit.get("manual_image_order"))
        manual_map = {f"m:{url}": url for url in manual_urls}
        ordered: list[str] = []
        used: set[str] = set()
        for token in manual_order:
            if not token.startswith("m:"):
                continue
            image_url = manual_map.get(token)
            if image_url is None or image_url in used:
                continue
            used.add(image_url)
            ordered.append(image_url)
        for image_url in manual_urls:
            if image_url in used:
                continue
            used.add(image_url)
            ordered.append(image_url)
        item["image_urls"] = ordered
        item["image_count"] = len(ordered)
    product_id = _safe_int(item.get("id"))
    starred_ids = []
    if favorite_manual_category_ids_by_product is not None and product_id is not None:
        starred_ids = [int(value) for value in favorite_manual_category_ids_by_product.get(int(product_id), [])]
    item["starred_category_ids"] = sorted(set(starred_ids))
    item["is_favorite"] = len(item["starred_category_ids"]) > 0
    return item


def _safe_apply_backend_pricing_to_item(
    *,
    item: dict[str, Any],
    settings_service: PricingSettingsService,
    settings,
    source_profile_map: dict[int, Any],
    weight_rules: list[Any],
    apply_source_visibility_rules: bool = True,
    favorite_manual_category_ids_by_product: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    try:
        return _apply_backend_pricing_to_item(
            item=item,
            settings_service=settings_service,
            settings=settings,
            source_profile_map=source_profile_map,
            weight_rules=weight_rules,
            apply_source_visibility_rules=apply_source_visibility_rules,
            favorite_manual_category_ids_by_product=favorite_manual_category_ids_by_product,
        )
    except InvalidProductStatusError:
        raise
    except Exception:
        LOGGER.exception("Failed to enrich product item", extra={"product_id": item.get("id")})
        return item


def _flatten_tree(nodes: list[Any]) -> list[Any]:
    result: list[Any] = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_tree(list(getattr(node, "children", []) or [])))
    return result


def _collect_descendant_slugs(node: Any, target: set[str]) -> None:
    slug = str(getattr(node, "slug", "") or "").strip()
    if slug:
        target.add(slug)
    for child in list(getattr(node, "children", []) or []):
        _collect_descendant_slugs(child, target)


def _build_descendant_slug_index(tree: list[Any]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for node in _flatten_tree(tree):
        slug = str(getattr(node, "slug", "") or "").strip()
        if not slug:
            continue
        bucket: set[str] = set()
        _collect_descendant_slugs(node, bucket)
        index[slug] = bucket
    return index


def _build_category_full_path_parts(
    node: Any,
    *,
    category_node_by_id: dict[int, Any],
) -> list[str]:
    parts: list[str] = []
    current = node
    guard = 0
    while current is not None and guard < 64:
        name = str(getattr(current, "name", "") or "").strip()
        if name:
            parts.append(name)
        parent_id_raw = getattr(current, "parent_id", None)
        parent_id = _safe_int(parent_id_raw)
        current = category_node_by_id.get(int(parent_id)) if parent_id is not None else None
        guard += 1
    parts.reverse()
    return parts


def _group_category_paths_for_display(path_parts_list: list[list[str]]) -> list[str]:
    grouped: dict[str, set[str]] = {}
    order: list[str] = []

    for parts in path_parts_list:
        clean_parts = [str(part).strip() for part in parts if str(part or "").strip()]
        if not clean_parts:
            continue
        if len(clean_parts) == 1:
            prefix_key = ""
            leaf = clean_parts[0]
        else:
            prefix_key = " -> ".join(clean_parts[:-1])
            leaf = clean_parts[-1]
        if prefix_key not in grouped:
            grouped[prefix_key] = set()
            order.append(prefix_key)
        grouped[prefix_key].add(leaf)

    labels: list[str] = []
    for prefix in order:
        leaves = sorted(grouped[prefix], key=lambda value: value.casefold())
        if not leaves:
            continue
        if prefix:
            labels.append(f"{prefix} -> {', '.join(leaves)}")
        else:
            labels.append(", ".join(leaves))
    return labels


def _format_grouped_category_paths(path_parts_list: list[list[str]]) -> tuple[list[str], str | None]:
    labels = _group_category_paths_for_display(path_parts_list)
    if not labels:
        return [], None
    return labels, "; ".join(labels) + ";"


def _apply_internal_categories_from_ids(
    item: dict[str, Any],
    *,
    product_id: int,
    category_ids_by_product: dict[int, list[int]],
    category_node_by_id: dict[int, Any],
    fallback_node: Any | None,
) -> None:
    raw_ids = [int(value) for value in category_ids_by_product.get(int(product_id), [])]
    matched_nodes = [category_node_by_id[category_id] for category_id in raw_ids if category_id in category_node_by_id]
    if not matched_nodes and fallback_node is not None:
        matched_nodes = [fallback_node]

    path_parts_list = [
        _build_category_full_path_parts(node, category_node_by_id=category_node_by_id)
        for node in matched_nodes
    ]
    grouped_labels, grouped_label = _format_grouped_category_paths(path_parts_list)

    item["internal_category_ids"] = [int(node.id) for node in matched_nodes]
    item["internal_category_names"] = grouped_labels
    item["internal_category_slugs"] = [str(node.slug) for node in matched_nodes]
    if matched_nodes:
        item["internal_category_id"] = int(matched_nodes[0].id)
        item["internal_category_name"] = grouped_label or str(matched_nodes[0].name)
        item["internal_category_slug"] = str(matched_nodes[0].slug)
    else:
        item["internal_category_id"] = None
        item["internal_category_name"] = None
        item["internal_category_slug"] = None


def _product_row_to_item(product: ParserProduct, *, default_show_description: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": int(product.id),
        "source_id": int(product.source_id),
        "handle": str(product.handle),
        "title": str(product.title),
        "description": product.description,
        "vendor": product.vendor,
        "vendor_original": product.vendor,
        "vendor_mapped": product.vendor,
        "vendor_display": product.vendor,
        "product_type": product.product_type,
        "url": str(product.url),
        "price": product.price,
        "currency": str(product.currency),
        "status": str(product.status),
        "is_auto_added": bool(getattr(product, "is_auto_added", True)),
        "auto_hide_force_visible": bool(getattr(product, "auto_hide_force_visible", False)),
        "image_count": int(product.image_count or 0),
        "image_urls": list(product.image_urls or []),
        "variants": list(product.variants or []),
        "weight_grams": product.weight_grams,
        "weight_source": product.weight_source,
        "weight_match_keyword": product.weight_match_keyword,
        "weight_value": product.weight_value,
        "weight_unit": product.weight_unit,
        "created_at": product.created_at.isoformat() if product.created_at is not None else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at is not None else None,
    }
    _apply_product_overrides_to_item(item, product, default_show_description=default_show_description)
    return item


def _apply_admin_product_filters(
    query,
    *,
    search: str | None,
    source_id: int | None,
    vendor: str | None,
    product_type: str | None,
    status_filter: str | None,
):
    normalized_vendor_expr = _normalized_mapped_vendor_key_expr()
    mapped_vendor_expr = _mapped_vendor_label_expr()
    normalized_type_expr = func.lower(func.trim(func.coalesce(ParserProduct.product_type, "")))
    if source_id is not None:
        sid = int(source_id)
        query = query.filter(
            or_(
                ParserProduct.source_id == sid,
                select(ParserProductOriginVariant.id)
                .where(ParserProductOriginVariant.product_id == ParserProduct.id)
                .where(ParserProductOriginVariant.source_id == sid)
                .exists(),
            )
        )
    if vendor:
        if vendor == _NO_BRAND_FILTER_TOKEN:
            query = query.filter(
                or_(
                    ParserProduct.vendor.is_(None),
                    func.length(func.trim(ParserProduct.vendor)) == 0,
                )
            )
        else:
            normalized_vendor_key = "".join(
                ch
                for ch in unicodedata.normalize("NFKC", str(vendor or "")).casefold().strip()
                if ch.isalnum()
            )
            query = query.filter(normalized_vendor_expr == normalized_vendor_key)
    if product_type:
        query = query.filter(normalized_type_expr == str(product_type).strip().lower())
    if status_filter:
        query = query.filter(ParserProduct.status == status_filter)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                ParserProduct.title.ilike(pattern),
                mapped_vendor_expr.ilike(pattern),
                ParserProduct.product_type.ilike(pattern),
                ParserProduct.handle.ilike(pattern),
                ParserProduct.url.ilike(pattern),
            )
        )
    return query


def _project_admin_table_item(item: dict[str, Any]) -> dict[str, Any]:
    names = [str(value).strip() for value in list(item.get("internal_category_names") or []) if str(value or "").strip()]
    if names:
        internal_category_label = "; ".join(names) + ";"
    else:
        single_name = str(item.get("internal_category_name") or "").strip()
        internal_category_label = single_name or "Прочее"

    return {
        "id": int(item.get("id") or 0),
        "source_id": int(item.get("source_id") or 0),
        "title": str(item.get("title") or ""),
        "vendor": item.get("vendor"),
        "vendor_original": item.get("vendor_original"),
        "vendor_mapped": item.get("vendor_mapped"),
        "vendor_display": item.get("vendor_display"),
        "url": str(item.get("url") or ""),
        "product_type": item.get("product_type"),
        "status": str(item.get("status") or "hidden"),
        "image_count": int(item.get("image_count") or 0),
        "image_urls": list(item.get("image_urls") or []),
        "source_price": _safe_float(item.get("source_price")),
        "source_currency": item.get("source_currency"),
        "final_price": _safe_float(item.get("final_price")),
        "final_currency": item.get("final_currency"),
        "internal_category_name": internal_category_label,
    }


@router.get("/admin/products/table")
def get_admin_products_table(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
    search: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    vendor: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=_CATALOG_MAX_LIMIT),
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    selected_status = None
    if status_filter:
        selected_status = str(status_filter).strip().lower()
        if selected_status not in _ALLOWED_PRODUCT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые статусы: available, out_of_stock, hidden, unavailable",
            )

    normalized_search = (search or "").strip()
    normalized_vendor = (vendor or "").strip() or None
    normalized_type = (product_type or "").strip() or None
    decoded_cursor: tuple[datetime, int] | None = _decode_cursor(cursor) if cursor else None

    base_query = (
        _with_active_sources(db.query(ParserProduct))
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.status.in_(tuple(_ALLOWED_PRODUCT_STATUSES)))
    )
    base_query = _apply_admin_product_filters(
        base_query,
        search=normalized_search or None,
        source_id=source_id,
        vendor=normalized_vendor,
        product_type=normalized_type,
        status_filter=selected_status,
    )

    filtered_total = (
        base_query.with_entities(func.count(ParserProduct.id))
        .order_by(None)
        .scalar()
        or 0
    )
    overall_total = (
        _with_active_sources(db.query(func.count(ParserProduct.id)))
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.status.in_(tuple(_ALLOWED_PRODUCT_STATUSES)))
        .scalar()
        or 0
    )

    page_query = base_query
    if decoded_cursor is not None:
        page_query = page_query.filter(
            or_(
                ParserProduct.updated_at < decoded_cursor[0],
                (ParserProduct.updated_at == decoded_cursor[0]) & (ParserProduct.id < decoded_cursor[1]),
            )
        )

    rows = (
        page_query
        .order_by(ParserProduct.updated_at.desc(), ParserProduct.id.desc())
        .limit(int(limit))
        .all()
    )

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    default_show_description = bool(getattr(settings, "show_product_description", True))
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /admin/products/table; fallback to empty rules")
        weight_rules = []

    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False, allow_match_rebuild=False)
    flat_tree = _flatten_tree(category_tree)
    category_node_by_id = {int(node.id): node for node in flat_tree if bool(getattr(node, "is_enabled", True))}
    fallback_node = next((node for node in flat_tree if bool(getattr(node, "is_fallback", False))), None)
    manual_repo = ParserCategoryManualProductRepository(db)
    product_ids = {int(item.id) for item in rows}
    manual_map = manual_repo.get_grouped_by_product_ids(product_ids)
    indexed_category_ids = category_index_service.get_grouped_category_ids(product_ids)
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}
    favorite_manual_map = {
        int(product_id): [int(category_id) for category_id in category_ids if int(category_id) in favorite_category_ids]
        for product_id, category_ids in manual_map.items()
    }

    source_profile_map = _source_profile_map_for_products(db, product_ids={int(item.id) for item in rows})
    primary_source_by_product = _primary_source_ids_for_products(
        db,
        product_ids={int(item.id) for item in rows},
        fallback_source_ids_by_product={
            int(item.id): int(item.source_id)
            for item in rows
            if getattr(item, "source_id", None) is not None
        },
    )
    brand_mapping_service = BrandMappingService(db)
    mapping_by_key = brand_mapping_service.get_mapping_by_key()

    items: list[dict[str, Any]] = []
    for row in rows:
        raw_item = _product_row_to_item(row, default_show_description=default_show_description)
        _inject_effective_source_id(raw_item, product_id=int(row.id), primary_source_by_product=primary_source_by_product)
        _apply_brand_mapping_to_item(raw_item, brand_mapping_service, mapping_by_key)
        _apply_internal_categories_from_ids(
            raw_item,
            product_id=int(row.id),
            category_ids_by_product=indexed_category_ids,
            category_node_by_id=category_node_by_id,
            fallback_node=fallback_node,
        )
        try:
            enriched = _safe_apply_backend_pricing_to_item(
                item=raw_item,
                settings_service=settings_service,
                settings=settings,
                source_profile_map=source_profile_map,
                weight_rules=weight_rules,
                favorite_manual_category_ids_by_product=favorite_manual_map,
            )
        except InvalidProductStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Обнаружен недопустимый статус товара в базе: {exc}",
            ) from exc
        if not str(enriched.get("source_name") or "").strip():
            sid = _safe_int(enriched.get("source_id"))
            profile = source_profile_map.get(int(sid)) if sid is not None else None
            if profile is not None:
                enriched["source_name"] = str(getattr(profile, "name", "") or "")
        items.append(_project_admin_table_item(enriched))
    _sort_products_for_display(items)

    next_cursor = None
    has_more = False
    if len(rows) == int(limit):
        last_ts, last_id = _cursor_parts_for_product(rows[-1])
        next_cursor = _encode_cursor(last_ts, last_id)
        has_more = True

    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": int(limit),
        "total": int(filtered_total),
        "overall_total": int(overall_total),
    }


@router.get("/admin/products/table/facets")
def get_admin_products_table_facets(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
    search: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    vendor: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
) -> dict[str, Any]:
    selected_status = None
    if status_filter:
        selected_status = str(status_filter).strip().lower()
        if selected_status not in _ALLOWED_PRODUCT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые статусы: available, out_of_stock, hidden, unavailable",
            )

    normalized_search = (search or "").strip() or None
    normalized_vendor = (vendor or "").strip() or None
    normalized_type = (product_type or "").strip() or None

    base_all = (
        db.query(ParserProduct)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.status.in_(tuple(_ALLOWED_PRODUCT_STATUSES)))
    )
    filtered_query = _apply_admin_product_filters(
        base_all,
        search=normalized_search,
        source_id=source_id,
        vendor=normalized_vendor,
        product_type=normalized_type,
        status_filter=selected_status,
    )
    filtered_total = (
        filtered_query.with_entities(func.count(ParserProduct.id))
        .order_by(None)
        .scalar()
        or 0
    )
    overall_total = (
        db.query(func.count(ParserProduct.id))
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.status.in_(tuple(_ALLOWED_PRODUCT_STATUSES)))
        .scalar()
        or 0
    )

    vendor_query = _apply_admin_product_filters(
        base_all,
        search=normalized_search,
        source_id=source_id,
        vendor=None,
        product_type=normalized_type,
        status_filter=selected_status,
    )
    normalized_vendor_expr = _normalized_mapped_vendor_key_expr()
    mapped_vendor_expr = _mapped_vendor_label_expr()
    vendor_rows = (
        vendor_query
        .with_entities(
            normalized_vendor_expr.label("vendor_key"),
            func.min(func.trim(mapped_vendor_expr)).label("vendor_label"),
            func.count(ParserProduct.id).label("vendor_count"),
        )
        .group_by(normalized_vendor_expr)
        .order_by(normalized_vendor_expr.asc())
        .all()
    )

    vendors: list[dict[str, Any]] = []
    no_brand_count = 0
    for vendor_key, vendor_label, vendor_count in vendor_rows:
        vendor_key_normalized = str(vendor_key or "").strip()
        count_int = int(vendor_count or 0)
        if not vendor_key_normalized:
            no_brand_count += count_int
            continue
        display_name = str(vendor_label or "").strip() or vendor_key_normalized
        vendors.append({
            "value": vendor_key_normalized,
            "label": display_name,
            "count": count_int,
        })
    if no_brand_count > 0:
        vendors.insert(0, {
            "value": _NO_BRAND_FILTER_TOKEN,
            "label": "Без бренда",
            "count": int(no_brand_count),
        })

    type_query = _apply_admin_product_filters(
        base_all,
        search=normalized_search,
        source_id=source_id,
        vendor=normalized_vendor,
        product_type=None,
        status_filter=selected_status,
    )
    normalized_type_expr = func.lower(func.trim(func.coalesce(ParserProduct.product_type, "")))
    type_rows = (
        type_query
        .with_entities(
            normalized_type_expr.label("type_key"),
            func.min(func.trim(ParserProduct.product_type)).label("type_label"),
            func.count(ParserProduct.id).label("type_count"),
        )
        .filter(func.length(normalized_type_expr) > 0)
        .group_by(normalized_type_expr)
        .order_by(normalized_type_expr.asc())
        .all()
    )
    local_categories: list[dict[str, Any]] = []
    for raw_type_key, raw_type_label, raw_type_count in type_rows:
        type_key = str(raw_type_key or "").strip()
        if not type_key:
            continue
        type_label = str(raw_type_label or "").strip() or type_key
        local_categories.append({
            "value": type_key,
            "label": type_label,
            "count": int(raw_type_count or 0),
        })

    return {
        "vendors": vendors,
        "local_categories": local_categories,
        "total": int(filtered_total),
        "overall_total": int(overall_total),
    }


@router.get("/admin/brand-mapping", response_model=BrandMappingListResponse)
def get_admin_brand_mapping(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    return BrandMappingService(db).get_admin_brand_mapping_payload()


@router.put("/admin/brand-mapping", response_model=BrandMappingListResponse)
def put_admin_brand_mapping(
    payload: BrandMappingUpdateRequest,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    try:
        return BrandMappingService(db).save_admin_brand_mapping([item.model_dump() for item in payload.items])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/products")
async def get_products(request: Request, db: Session = Depends(get_db)) -> Response:
    upstream = forward_service_request(request=request, path="products", body=b"")
    if upstream.status_code >= 400:
        limit = max(1, min(200, _safe_int(request.query_params.get("limit")) or 100))
        offset = max(0, _safe_int(request.query_params.get("offset")) or 0)
        q = _with_active_sources(db.query(ParserProduct)).filter(ParserProduct.deleted_at.is_(None)).order_by(ParserProduct.id.desc())
        total = int(q.count())
        rows = q.offset(offset).limit(limit).all()
        primary_source_by_product = _primary_source_ids_for_products(
            db,
            product_ids={int(row.id) for row in rows},
            fallback_source_ids_by_product={
                int(row.id): int(row.source_id)
                for row in rows
                if getattr(row, "source_id", None) is not None
            },
        )
        normalized_items: list[dict[str, Any]] = []
        for row in rows:
            item = _product_row_to_item(row)
            _inject_effective_source_id(item, product_id=int(row.id), primary_source_by_product=primary_source_by_product)
            normalized_items.append(item)
        return JSONResponse(
            content={
                "items": normalized_items,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
            status_code=200,
        )

    payload = _upstream_json_or_none(upstream)
    if not isinstance(payload, dict):
        return upstream

    items = payload.get("items")
    if not isinstance(items, list):
        return upstream

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    default_show_description = bool(getattr(settings, "show_product_description", True))
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /products; fallback to empty rules")
        weight_rules = []
    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False, allow_match_rebuild=False)
    flat_tree = _flatten_tree(category_tree)
    category_node_by_id = {int(node.id): node for node in flat_tree if bool(getattr(node, "is_enabled", True))}
    fallback_node = next((node for node in flat_tree if bool(getattr(node, "is_fallback", False))), None)
    manual_repo = ParserCategoryManualProductRepository(db)
    product_ids = {
        product_id
        for product_id in (_safe_int(item.get("id")) for item in items if isinstance(item, dict))
        if product_id is not None
    }
    fallback_source_ids = {
        source_id
        for source_id in (_safe_int(item.get("source_id")) for item in items if isinstance(item, dict))
        if source_id is not None
    }
    source_profile_map = _source_profile_map_for_products(
        db,
        product_ids=product_ids,
        fallback_source_ids=fallback_source_ids,
    )
    primary_source_by_product = _primary_source_ids_for_products(
        db,
        product_ids=product_ids,
        fallback_source_ids_by_product={
            int(product_id): int(source_id)
            for product_id, source_id in (
                (
                    _safe_int(item.get("id")) if isinstance(item, dict) else None,
                    _safe_int(item.get("source_id")) if isinstance(item, dict) else None,
                )
                for item in items
            )
            if product_id is not None and source_id is not None
        },
    )
    local_product_map: dict[int, ParserProduct] = {}
    if product_ids:
        for local_product in (
            _with_active_sources(db.query(ParserProduct))
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.id.in_(list(product_ids)))
            .all()
        ):
            local_product_map[int(local_product.id)] = local_product
    manual_map = manual_repo.get_grouped_by_product_ids(product_ids)
    indexed_category_ids = category_index_service.get_grouped_category_ids(product_ids)
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}
    favorite_manual_map = {
        int(product_id): [int(category_id) for category_id in category_ids if int(category_id) in favorite_category_ids]
        for product_id, category_ids in manual_map.items()
    }
    brand_mapping_service = BrandMappingService(db)
    mapping_by_key = brand_mapping_service.get_mapping_by_key()

    try:
        enriched_items: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                enriched_items.append(item)
                continue
            product_id = _safe_int(item.get("id"))
            local_product = local_product_map.get(int(product_id)) if product_id is not None else None
            if local_product is not None:
                _apply_product_overrides_to_item(
                    item,
                    local_product,
                    default_show_description=default_show_description,
                )
            elif not default_show_description:
                item["description"] = None
            _inject_effective_source_id(item, product_id=product_id, primary_source_by_product=primary_source_by_product)
            _apply_brand_mapping_to_item(item, brand_mapping_service, mapping_by_key)
            if product_id is not None:
                _apply_internal_categories_from_ids(
                    item,
                    product_id=int(product_id),
                    category_ids_by_product=indexed_category_ids,
                    category_node_by_id=category_node_by_id,
                    fallback_node=fallback_node,
                )
            enriched_items.append(
                _safe_apply_backend_pricing_to_item(
                    item=item,
                    settings_service=settings_service,
                    settings=settings,
                    source_profile_map=source_profile_map,
                    weight_rules=weight_rules,
                    favorite_manual_category_ids_by_product=favorite_manual_map,
                )
            )
        _sort_products_for_display(enriched_items)
        payload["items"] = enriched_items
    except InvalidProductStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Обнаружен недопустимый статус товара в базе: {exc}",
        ) from exc
    return JSONResponse(
        content=payload,
        status_code=upstream.status_code,
        headers=_json_response_headers(upstream),
    )


@router.get(
    "/catalog/products",
    response_model=CatalogProductsResponse,
    summary="Список товаров витрины",
    description=(
        "Публичная выдача каталога для витрины. Поддерживает курсорную пагинацию, поиск, "
        "фильтрацию по источнику, категории и статусу."
    ),
    responses={
        200: {
            "description": "Страница каталога витрины.",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "id": 120045,
                                "source_id": 12,
                                "title": "Куртка утеплённая",
                                "vendor": "Stone Island",
                                "url": "https://example.com/products/120045",
                                "price": 18900.0,
                                "currency": "RUB",
                                "source_price": 165.0,
                                "source_currency": "USD",
                                "status": "available",
                                "image_count": 4,
                                "image_urls": [],
                                "buyout_price_rub": 15450.0,
                                "is_favorite": False,
                            }
                        ],
                        "next_cursor": "MjAyNi0wNC0xNlQxOTowODoyNS4wMDAwMDB8MTIwMDQ1",
                        "has_more": True,
                        "limit": 36,
                    }
                }
            },
        }
    },
)
def get_catalog_products(
    db: Session = Depends(get_db),
    category_slug: str | None = Query(default=None, description="Slug категории для фильтрации."),
    search: str | None = Query(default=None, description="Поиск по title/vendor/type/handle/url."),
    source_id: int | None = Query(default=None, description="ID источника товара."),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Фильтр по статусу: available | out_of_stock | hidden.",
    ),
    limit: int = Query(default=36, ge=1, le=_CATALOG_MAX_LIMIT, description="Размер страницы (до 120)."),
    cursor: str | None = Query(default=None, description="Курсор следующей страницы из previous ответа."),
) -> dict[str, Any]:
    selected_category_slug = (category_slug or "").strip().lower()
    selected_status = None
    if status_filter:
        selected_status = str(status_filter).strip().lower()
        if selected_status not in _PUBLIC_PRODUCT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые статусы: available, out_of_stock, hidden",
            )

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    default_show_description = bool(getattr(settings, "show_product_description", True))
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /catalog/products; fallback to empty rules")
        weight_rules = []

    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    categories = category_repo.get_all_active()
    category_tree = build_tree(categories, keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False, allow_match_rebuild=False)
    flat_tree = _flatten_tree(category_tree)
    category_node_by_id = {int(node.id): node for node in flat_tree if bool(getattr(node, "is_enabled", True))}
    fallback_node = next((node for node in flat_tree if bool(getattr(node, "is_fallback", False))), None)
    fallback_slug = str(fallback_node.slug) if fallback_node is not None else None
    descendant_slug_index = _build_descendant_slug_index(category_tree)
    allowed_slugs = descendant_slug_index.get(selected_category_slug) if selected_category_slug else None
    if selected_category_slug and allowed_slugs is None:
        return {
            "items": [],
            "next_cursor": None,
            "has_more": False,
            "limit": int(limit),
        }
    slug_to_enabled_category_id = {
        str(getattr(node, "slug", "")): int(node.id)
        for node in flat_tree
        if bool(getattr(node, "is_enabled", True))
    }
    allowed_category_ids: set[int] = set()
    if allowed_slugs is not None:
        allowed_category_ids = {
            int(category_id)
            for slug, category_id in slug_to_enabled_category_id.items()
            if slug in allowed_slugs
        }

    decoded_cursor: tuple[datetime, int] | None = _decode_cursor(cursor) if cursor else None
    product_repo = ParserProductRepository(db)
    manual_repo = ParserCategoryManualProductRepository(db)
    brand_mapping_service = BrandMappingService(db)
    mapping_by_key = brand_mapping_service.get_mapping_by_key()
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}

    normalized_search = (search or "").strip()
    accepted_items: list[dict[str, Any]] = []
    next_cursor: str | None = None
    scan_cursor = decoded_cursor

    for _ in range(_CATALOG_MAX_SCAN_PAGES):
        query = (
            _with_active_sources(product_repo.query())
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.status.in_(tuple(_PUBLIC_PRODUCT_STATUSES)))
        )
        if source_id is not None:
            sid = int(source_id)
            query = query.filter(
                or_(
                    ParserProduct.source_id == sid,
                    select(ParserProductOriginVariant.id)
                    .where(ParserProductOriginVariant.product_id == ParserProduct.id)
                    .where(ParserProductOriginVariant.source_id == sid)
                    .exists(),
                )
            )
        if normalized_search:
            pattern = f"%{normalized_search}%"
            mapped_vendor_expr = _mapped_vendor_label_expr()
            query = query.filter(
                or_(
                    ParserProduct.title.ilike(pattern),
                    mapped_vendor_expr.ilike(pattern),
                    ParserProduct.product_type.ilike(pattern),
                    ParserProduct.handle.ilike(pattern),
                    ParserProduct.url.ilike(pattern),
                )
            )
        if allowed_slugs is not None:
            if fallback_slug and selected_category_slug == fallback_slug:
                query = query.filter(
                    ~db.query(ParserProductCategoryMatch.id)
                    .join(ParserCategory, ParserCategory.id == ParserProductCategoryMatch.category_id)
                    .filter(ParserProductCategoryMatch.product_id == ParserProduct.id)
                    .filter(ParserCategory.deleted_at.is_(None))
                    .filter(ParserCategory.is_enabled.is_(True))
                    .filter(ParserCategory.is_fallback.is_(False))
                    .exists()
                )
            elif allowed_category_ids:
                query = query.filter(
                    db.query(ParserProductCategoryMatch.id)
                    .join(ParserCategory, ParserCategory.id == ParserProductCategoryMatch.category_id)
                    .filter(ParserProductCategoryMatch.product_id == ParserProduct.id)
                    .filter(ParserCategory.deleted_at.is_(None))
                    .filter(ParserCategory.is_enabled.is_(True))
                    .filter(ParserProductCategoryMatch.category_id.in_(list(allowed_category_ids)))
                    .exists()
                )
            else:
                return {
                    "items": [],
                    "next_cursor": None,
                    "has_more": False,
                    "limit": int(limit),
                }
        if scan_cursor is not None:
            query = query.filter(
                or_(
                    ParserProduct.updated_at < scan_cursor[0],
                    (ParserProduct.updated_at == scan_cursor[0]) & (ParserProduct.id < scan_cursor[1]),
                )
            )

        rows = (
            query.order_by(ParserProduct.updated_at.desc(), ParserProduct.id.desc())
            .limit(_CATALOG_SCAN_BATCH)
            .all()
        )
        if not rows:
            break
    
        product_ids = {int(item.id) for item in rows}
        manual_map = manual_repo.get_grouped_by_product_ids(product_ids)
        indexed_category_ids = category_index_service.get_grouped_category_ids(product_ids)
        favorite_manual_map = {
            int(product_id): [int(category_id) for category_id in category_ids if int(category_id) in favorite_category_ids]
            for product_id, category_ids in manual_map.items()
        }
        source_profile_map = _source_profile_map_for_products(
            db,
            product_ids={int(item.id) for item in rows},
        )
        primary_source_by_product = _primary_source_ids_for_products(
            db,
            product_ids={int(item.id) for item in rows},
            fallback_source_ids_by_product={
                int(item.id): int(item.source_id)
                for item in rows
                if getattr(item, "source_id", None) is not None
            },
        )

        for row in rows:
            raw_item = _product_row_to_item(row, default_show_description=default_show_description)
            _inject_effective_source_id(raw_item, product_id=int(row.id), primary_source_by_product=primary_source_by_product)
            _apply_brand_mapping_to_item(raw_item, brand_mapping_service, mapping_by_key)
            _apply_internal_categories_from_ids(
                raw_item,
                product_id=int(row.id),
                category_ids_by_product=indexed_category_ids,
                category_node_by_id=category_node_by_id,
                fallback_node=fallback_node,
            )
            if allowed_slugs is not None:
                matched_slugs = set(raw_item.get("internal_category_slugs") or [])
                if not matched_slugs and fallback_slug:
                    matched_slugs = {fallback_slug}
                if not matched_slugs.intersection(allowed_slugs):
                    continue
            try:
                enriched = _safe_apply_backend_pricing_to_item(
                    item=raw_item,
                    settings_service=settings_service,
                    settings=settings,
                    source_profile_map=source_profile_map,
                    weight_rules=weight_rules,
                    favorite_manual_category_ids_by_product=favorite_manual_map,
                )
            except InvalidProductStatusError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Обнаружен недопустимый статус товара в базе: {exc}",
                ) from exc
            if selected_status is not None and str(enriched.get("status") or "").strip().lower() != selected_status:
                continue
            accepted_items.append(_project_catalog_item(enriched))
            if len(accepted_items) >= int(limit):
                cursor_ts, cursor_id = _cursor_parts_for_product(row)
                next_cursor = _encode_cursor(cursor_ts, cursor_id)
                break

        if len(accepted_items) >= int(limit):
            break

        last_row = rows[-1]
        last_cursor_ts, last_cursor_id = _cursor_parts_for_product(last_row)
        scan_cursor = (last_cursor_ts, last_cursor_id)
        if len(rows) < _CATALOG_SCAN_BATCH:
            break
        next_cursor = _encode_cursor(last_cursor_ts, last_cursor_id)

    page_items = accepted_items[: int(limit)]
    _sort_products_for_display(page_items)
    return {
        "items": page_items,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
        "limit": int(limit),
    }


@router.get("/admin/products")
def get_admin_products(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
    search: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    vendor: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=_CATALOG_MAX_LIMIT),
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    selected_status = None
    if status_filter:
        selected_status = str(status_filter).strip().lower()
        if selected_status not in _ALLOWED_PRODUCT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые статусы: available, out_of_stock, hidden, unavailable",
            )

    normalized_search = (search or "").strip()
    decoded_cursor: tuple[datetime, int] | None = _decode_cursor(cursor) if cursor else None

    base_query = (
        _with_active_sources(db.query(ParserProduct))
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.status.in_(tuple(_ALLOWED_PRODUCT_STATUSES)))
    )
    base_query = _apply_admin_product_filters(
        base_query,
        search=normalized_search or None,
        source_id=source_id,
        vendor=(vendor or "").strip() or None,
        product_type=(product_type or "").strip() or None,
        status_filter=selected_status,
    )

    total = (
        base_query.with_entities(func.count(ParserProduct.id))
        .order_by(None)
        .scalar()
        or 0
    )

    page_query = base_query
    if decoded_cursor is not None:
        page_query = page_query.filter(
            or_(
                ParserProduct.updated_at < decoded_cursor[0],
                (ParserProduct.updated_at == decoded_cursor[0]) & (ParserProduct.id < decoded_cursor[1]),
            )
        )

    rows = (
        page_query
        .order_by(ParserProduct.updated_at.desc(), ParserProduct.id.desc())
        .limit(int(limit))
        .all()
    )

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    default_show_description = bool(getattr(settings, "show_product_description", True))
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /admin/products; fallback to empty rules")
        weight_rules = []

    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False, allow_match_rebuild=False)
    flat_tree = _flatten_tree(category_tree)
    category_node_by_id = {int(node.id): node for node in flat_tree if bool(getattr(node, "is_enabled", True))}
    fallback_node = next((node for node in flat_tree if bool(getattr(node, "is_fallback", False))), None)
    manual_repo = ParserCategoryManualProductRepository(db)
    product_ids = {int(item.id) for item in rows}
    manual_map = manual_repo.get_grouped_by_product_ids(product_ids)
    indexed_category_ids = category_index_service.get_grouped_category_ids(product_ids)
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}
    favorite_manual_map = {
        int(product_id): [int(category_id) for category_id in category_ids if int(category_id) in favorite_category_ids]
        for product_id, category_ids in manual_map.items()
    }

    source_profile_map = _source_profile_map_for_products(db, product_ids={int(item.id) for item in rows})
    primary_source_by_product = _primary_source_ids_for_products(
        db,
        product_ids={int(item.id) for item in rows},
        fallback_source_ids_by_product={
            int(item.id): int(item.source_id)
            for item in rows
            if getattr(item, "source_id", None) is not None
        },
    )
    brand_mapping_service = BrandMappingService(db)
    mapping_by_key = brand_mapping_service.get_mapping_by_key()

    items: list[dict[str, Any]] = []
    for row in rows:
        raw_item = _product_row_to_item(row, default_show_description=default_show_description)
        _inject_effective_source_id(raw_item, product_id=int(row.id), primary_source_by_product=primary_source_by_product)
        _apply_brand_mapping_to_item(raw_item, brand_mapping_service, mapping_by_key)
        _apply_internal_categories_from_ids(
            raw_item,
            product_id=int(row.id),
            category_ids_by_product=indexed_category_ids,
            category_node_by_id=category_node_by_id,
            fallback_node=fallback_node,
        )
        try:
            enriched = _safe_apply_backend_pricing_to_item(
                item=raw_item,
                settings_service=settings_service,
                settings=settings,
                source_profile_map=source_profile_map,
                weight_rules=weight_rules,
                favorite_manual_category_ids_by_product=favorite_manual_map,
            )
        except InvalidProductStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Обнаружен недопустимый статус товара в базе: {exc}",
            ) from exc
        items.append(enriched)
    _sort_products_for_display(items)

    next_cursor = None
    has_more = False
    if len(rows) == int(limit):
        last_ts, last_id = _cursor_parts_for_product(rows[-1])
        next_cursor = _encode_cursor(last_ts, last_id)
        has_more = True

    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": int(limit),
        "total": int(total),
    }


@router.get("/products/pricing-example", response_model=PricingExampleProductResponse)
def get_pricing_example_product(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    base_query = (
        _with_active_sources(db.query(ParserProduct))
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.status.in_(tuple(_ALLOWED_PRODUCT_STATUSES)))
        .filter(ParserProduct.status == "available")
    )
    total = (
        base_query.with_entities(func.count(ParserProduct.id))
        .order_by(None)
        .scalar()
        or 0
    )
    if total <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Нет доступных товаров для примера ценообразования",
        )

    random_index = random.randint(0, int(total) - 1)
    row = (
        base_query
        .order_by(ParserProduct.updated_at.desc(), ParserProduct.id.desc())
        .offset(int(random_index))
        .limit(1)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Не удалось выбрать товар для примера ценообразования",
        )

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    default_show_description = bool(getattr(settings, "show_product_description", True))
    raw_item = _product_row_to_item(row, default_show_description=default_show_description)
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /products/pricing-example; fallback to empty rules")
        weight_rules = []

    brand_mapping_service = BrandMappingService(db)
    mapping_by_key = brand_mapping_service.get_mapping_by_key()
    primary_source_by_product = _primary_source_ids_for_products(
        db,
        product_ids={int(row.id)},
        fallback_source_ids_by_product={int(row.id): int(row.source_id)} if getattr(row, "source_id", None) is not None else None,
    )
    source_profile_map = _source_profile_map_for_products(
        db,
        product_ids={int(row.id)},
        fallback_source_ids={int(row.source_id)} if getattr(row, "source_id", None) is not None else None,
    )
    _apply_brand_mapping_to_item(raw_item, brand_mapping_service, mapping_by_key)
    enriched = _safe_apply_backend_pricing_to_item(
        item=raw_item,
        settings_service=settings_service,
        settings=settings,
        source_profile_map=source_profile_map,
        weight_rules=weight_rules,
        favorite_manual_category_ids_by_product=None,
    )
    components = enriched.get("pricing_components")
    if not isinstance(components, dict):
        components = {}
    source_profile = source_profile_map.get(primary_source_by_product.get(int(row.id), -1))

    return {
        "product_id": int(row.id),
        "title": str(enriched.get("title") or row.title),
        "url": str(enriched.get("url") or row.url),
        "source_name": str(getattr(source_profile, "name", "") or "") or None,
        "image_url": _resolve_primary_image_url(enriched),
        "source_price": _safe_float(enriched.get("source_price")),
        "source_currency": str(enriched.get("source_currency") or "") or None,
        "final_price": _safe_float(enriched.get("final_price")),
        "components": components,
    }


@router.get(
    "/products/{product_id}",
    response_model=ShowcaseProductResponse,
    summary="Карточка товара",
    description="Детальная информация о товаре витрины по ID.",
    responses={
        200: {
            "description": "Детальная карточка товара витрины.",
            "content": {
                "application/json": {
                    "example": {
                        "id": 120045,
                        "source_id": 12,
                        "title": "Куртка утеплённая",
                        "vendor": "Stone Island",
                        "url": "https://example.com/products/120045",
                        "price": 18900.0,
                        "currency": "RUB",
                        "source_price": 165.0,
                        "source_currency": "USD",
                        "final_price": 18900.0,
                        "final_currency": "RUB",
                        "status": "available",
                        "image_urls": [],
                        "variants": [
                            {"title": "M", "available": True, "inventory_quantity": 2, "price": "165.0"},
                            {"title": "L", "available": False, "inventory_quantity": 0, "price": "165.0"},
                        ],
                        "internal_category_name": "Куртки",
                        "internal_category_names": ["Мужское", "Куртки"],
                        "description": "Технологичная утеплённая куртка.",
                    }
                }
            },
        }
    },
)
async def get_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    local_product = ParserProductRepository(db).get_active_by_id(product_id)
    if local_product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    if str(local_product.status or "").strip().lower() == "unavailable":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    payload = _product_row_to_item(local_product)
    payload["description"] = _extract_product_description(payload)
    brand_mapping_service = BrandMappingService(db)
    mapping_by_key = brand_mapping_service.get_mapping_by_key()
    _apply_brand_mapping_to_item(payload, brand_mapping_service, mapping_by_key)

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    default_show_description = bool(getattr(settings, "show_product_description", True))
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /products/{product_id}; fallback to empty rules")
        weight_rules = []
    source_id = _safe_int(payload.get("source_id"))
    primary_source_by_product = _primary_source_ids_for_products(
        db,
        product_ids={int(local_product.id)},
        fallback_source_ids_by_product={int(local_product.id): source_id} if source_id is not None else None,
    )
    effective_source_id = primary_source_by_product.get(int(local_product.id), source_id if source_id is not None else None)
    if effective_source_id is not None:
        payload["source_id"] = int(effective_source_id)
    source_profile_map = _source_profile_map_for_products(
        db,
        product_ids={int(local_product.id)},
        fallback_source_ids={int(effective_source_id)} if effective_source_id is not None else None,
    )
    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False, allow_match_rebuild=False)
    flat_tree = _flatten_tree(category_tree)
    category_node_by_id = {int(node.id): node for node in flat_tree if bool(getattr(node, "is_enabled", True))}
    fallback_node = next((node for node in flat_tree if bool(getattr(node, "is_fallback", False))), None)
    manual_repo = ParserCategoryManualProductRepository(db)
    manual_map = manual_repo.get_grouped_by_product_ids({int(product_id)})
    indexed_category_ids = category_index_service.get_grouped_category_ids({int(product_id)})
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}
    favorite_manual_map = {
        int(product_id): [int(category_id) for category_id in manual_map.get(int(product_id), []) if int(category_id) in favorite_category_ids]
    }
    if local_product is not None:
        if isinstance(local_product.image_urls, list) and local_product.image_urls:
            payload["image_urls"] = list(local_product.image_urls or [])
        _apply_product_overrides_to_item(
            payload,
            local_product,
            default_show_description=default_show_description,
            apply_visibility_rules=False,
        )
        _apply_brand_mapping_to_item(payload, brand_mapping_service, mapping_by_key)
    elif not default_show_description:
        payload["description"] = None
    _apply_internal_categories_from_ids(
        payload,
        product_id=int(product_id),
        category_ids_by_product=indexed_category_ids,
        category_node_by_id=category_node_by_id,
        fallback_node=fallback_node,
    )
    try:
        priced = _safe_apply_backend_pricing_to_item(
            item=payload,
            settings_service=settings_service,
            settings=settings,
            source_profile_map=source_profile_map,
            weight_rules=weight_rules,
            apply_source_visibility_rules=False,
            favorite_manual_category_ids_by_product=favorite_manual_map,
        )
    except InvalidProductStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Обнаружен недопустимый статус товара в базе: {exc}",
        ) from exc
    projected = _project_showcase_product_detail(priced)
    return JSONResponse(content=projected, status_code=200)


@router.patch("/products/{product_id}")
async def update_product(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный payload")

    reset_fields_raw = payload.get("reset_to_default")
    reset_fields: set[str] = set()
    if isinstance(reset_fields_raw, list):
        reset_fields = {
            str(item or "").strip().lower()
            for item in reset_fields_raw
            if str(item or "").strip().lower() in {"title", "description", "images", "description_visibility"}
        }

    next_status_raw = payload.get("status")
    next_status = None
    if next_status_raw is not None:
        next_status = str(next_status_raw).strip().lower()
        if next_status not in _PUBLIC_PRODUCT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые статусы: available, out_of_stock, hidden",
            )

    title_override_payload = payload.get("title") if "title" in payload else None
    has_title_override = "title" in payload
    description_override_payload = payload.get("description") if "description" in payload else None
    has_description_override = "description" in payload
    description_visible_override_payload = payload.get("description_visible") if "description_visible" in payload else None
    has_description_visibility_override = "description_visible" in payload
    image_patch = payload.get("images") if isinstance(payload.get("images"), dict) else None
    has_image_patch = image_patch is not None

    if (
        next_status is None
        and not reset_fields
        and not has_title_override
        and not has_description_override
        and not has_description_visibility_override
        and not has_image_patch
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет поддерживаемых полей для обновления")

    product_repo = ParserProductRepository(db)
    product = product_repo.get_active_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    if "title" in reset_fields:
        product.title_override = None
        product.title_sync_locked = False
    if "description" in reset_fields:
        product.description_override = None
        product.description_sync_locked = False
    if "description_visibility" in reset_fields:
        product.description_visible_override = None
    if "images" in reset_fields:
        product.images_sync_locked = False
        product.hidden_source_image_asset_ids = []
        product.manual_image_asset_ids = []
        product.manual_image_order = []

    if has_title_override:
        next_title = str(title_override_payload or "").strip()
        if not next_title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Название не может быть пустым")
        product.title_override = next_title
        product.title_sync_locked = True

    if has_description_override:
        if description_override_payload is None:
            next_description = ""
        else:
            next_description = str(description_override_payload).strip()
        product.description_override = next_description
        product.description_sync_locked = True

    if has_description_visibility_override:
        if description_visible_override_payload is None:
            product.description_visible_override = None
        else:
            product.description_visible_override = bool(description_visible_override_payload)

    if has_image_patch:
        hidden_source_urls = _normalize_image_urls(image_patch.get("hidden_source_image_urls"))
        manual_image_urls = _normalize_image_urls(image_patch.get("manual_image_urls"))
        manual_image_order = _normalize_image_order_tokens(image_patch.get("manual_image_order"))
        product.hidden_source_image_asset_ids = hidden_source_urls
        product.manual_image_asset_ids = manual_image_urls
        product.manual_image_order = manual_image_order
        product.images_sync_locked = True

    primary_source_by_product = _primary_source_ids_for_products(
        db,
        product_ids={int(product.id)},
        fallback_source_ids_by_product={int(product.id): int(product.source_id)} if getattr(product, "source_id", None) is not None else None,
    )
    effective_source_id = primary_source_by_product.get(int(product.id), int(product.source_id) if getattr(product, "source_id", None) is not None else None)
    source_profile_map = _source_profile_map_for_products(
        db,
        product_ids={int(product.id)},
        fallback_source_ids={int(effective_source_id)} if effective_source_id is not None else None,
    )
    source_profile = (
        source_profile_map.get(int(effective_source_id))
        if effective_source_id is not None
        else None
    )
    source_auto_hide = bool(getattr(source_profile, "hide_auto_added_products", False))
    is_auto_added = bool(getattr(product, "is_auto_added", True))
    is_force_visible = bool(getattr(product, "auto_hide_force_visible", False))
    current_stored_status = str(product.status or "").strip().lower()

    if next_status is not None:
        if next_status == "hidden":
            if source_auto_hide and is_auto_added and is_force_visible and current_stored_status != "hidden":
                # Return to source-level auto-hidden state (global rule), not a manual hidden lock.
                product.auto_hide_force_visible = False
            else:
                product.status = "hidden"
                product.auto_hide_force_visible = False
        else:
            product.status = next_status
            if source_auto_hide and is_auto_added:
                # User explicitly unhid auto-hidden product: keep visible until user hides it back.
                product.auto_hide_force_visible = True
            else:
                product.auto_hide_force_visible = False

    db.commit()
    effective_status = _effective_status_from_variants(
        str(product.status),
        list(product.variants or []),
        source_profile=source_profile,
        is_auto_added=bool(getattr(product, "is_auto_added", True)),
        auto_hide_force_visible=bool(getattr(product, "auto_hide_force_visible", False)),
    )

    patched_payload = {
        "id": int(product.id),
        "source_id": int(effective_source_id) if effective_source_id is not None else int(product.source_id),
        "handle": str(product.handle),
        "title": str(product.title),
        "description": product.description,
        "vendor": product.vendor,
        "vendor_original": product.vendor,
        "vendor_mapped": product.vendor,
        "vendor_display": product.vendor,
        "product_type": product.product_type,
        "url": str(product.url),
        "price": product.price,
        "currency": str(product.currency),
        "source_price": getattr(product, "source_price", None) if getattr(product, "source_price", None) is not None else product.price,
        "source_currency": str(getattr(product, "source_currency", None) or product.currency),
        "status": effective_status,
        "image_count": int(product.image_count or 0),
        "image_urls": list(product.image_urls or []),
        "variants": list(product.variants or []),
        "weight_grams": product.weight_grams,
        "weight_source": product.weight_source,
        "weight_match_keyword": product.weight_match_keyword,
        "weight_value": product.weight_value,
        "weight_unit": product.weight_unit,
        "created_at": product.created_at.isoformat() if product.created_at is not None else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at is not None else None,
    }
    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    _apply_product_overrides_to_item(
        patched_payload,
        product,
        default_show_description=bool(getattr(settings, "show_product_description", True)),
    )
    brand_mapping_service = BrandMappingService(db)
    _apply_brand_mapping_to_item(patched_payload, brand_mapping_service, brand_mapping_service.get_mapping_by_key())

    patched_payload.update(
        {
            "internal_category_id": None,
            "internal_category_name": None,
            "internal_category_slug": None,
            "internal_category_ids": [],
            "internal_category_names": [],
            "internal_category_slugs": [],
            "starred_category_ids": [],
            "is_favorite": False,
            "final_price": None,
            "final_currency": "RUB",
            "pricing_manual_required": True,
            "pricing_reason": "Status update response is lightweight; pricing is computed in catalog/detail endpoints.",
            "pricing_components": {
                "status": effective_status,
                "reason": "status-only-patch",
            },
        }
    )
    return JSONResponse(content=patched_payload)


@router.post("/products/upload-image")
async def upload_product_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
):
    image_asset_id = _save_manual_product_image(file, db)
    return {"ok": True, "image_asset_id": image_asset_id}


@router.post("/products/upload-image-by-url")
def upload_product_image_by_url(
    payload: ProductImageUrlPayload,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
):
    raw_url = str(payload.url or "").strip()
    if not raw_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL изображения не передан")
    try:
        req = UrlRequest(raw_url, headers={"User-Agent": "WardrobeImageFetcher/1.0", "Accept": "image/*"})
        with urlopen(req, timeout=20) as resp:  # noqa: S310
            content_type = str(resp.headers.get("Content-Type") or "").lower()
            content = resp.read()
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL не указывает на изображение")
        parsed = urlparse(raw_url)
        name = Path(parsed.path or "").name or "remote-image.jpg"
        image_asset_id = _save_manual_product_image_bytes(file_name=name, content=content, db=db)
        return {"ok": True, "image_asset_id": image_asset_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Не удалось загрузить изображение: {exc}") from exc


@router.post("/products/preview-by-url")
def preview_product_by_url(
    payload: ProductUrlPayload,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    normalized_input = _normalize_url_loose(payload.url)
    if not normalized_input:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректная ссылка")

    matched = (
        db.query(ParserProduct)
        .filter(ParserProduct.deleted_at.is_(None))
        .filter(ParserProduct.url == str(payload.url).strip())
        .first()
    )
    if matched is None:
        input_parts = [p for p in urlparse(normalized_input).path.split("/") if p]
        handle = ""
        if len(input_parts) >= 2 and input_parts[0].lower() == "products":
            handle = input_parts[1]
        if handle:
            candidates = (
                db.query(ParserProduct)
                .filter(ParserProduct.deleted_at.is_(None))
                .filter(ParserProduct.handle == handle)
                .order_by(ParserProduct.id.desc())
                .limit(25)
                .all()
            )
            target_host = _norm_host(payload.url)
            matched = next(
                (
                    item
                    for item in candidates
                    if _norm_host(getattr(getattr(item, "source", None), "url", "") or getattr(item, "url", "")) == target_host
                    or _norm_host(str(item.url or "")) == target_host
                    or _normalize_url_loose(str(item.url or "")) == normalized_input
                ),
                None,
            )
        if matched is None:
            product = (
                db.query(ParserProduct)
                .filter(ParserProduct.deleted_at.is_(None))
                .filter(ParserProduct.url.isnot(None))
                .all()
            )
            for item in product:
                if _normalize_url_loose(str(item.url or "")) == normalized_input:
                    matched = item
                    break
    if matched is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    source = db.query(ParserSource).filter(ParserSource.id == int(getattr(matched, "source_id"))).first()

    return {
        "id": int(getattr(matched, "id")),
        "source_id": int(getattr(matched, "source_id")),
        "source_name": str(getattr(source, "name", "") or ""),
        "status": str(getattr(matched, "status", "") or "available"),
        "handle": str(getattr(matched, "handle", "") or ""),
        "title": str(getattr(matched, "title", "") or ""),
        "description": str(getattr(matched, "description", "") or ""),
        "weight_grams": _safe_float(getattr(matched, "weight_grams", None)),
        "vendor": getattr(matched, "vendor", None),
        "product_type": getattr(matched, "product_type", None),
        "product_url": str(getattr(matched, "url", "") or ""),
        "price": _safe_float(getattr(matched, "price", None)),
        "currency": str(getattr(matched, "currency", "") or "USD"),
        "image_urls": list(getattr(matched, "image_urls", []) or []),
        "variants": list(getattr(matched, "variants", []) or []),
    }


@router.get("/products/starred-categories/options")
def get_starred_categories_options(
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    category_repo = ParserCategoryRepository(db)
    starred_categories = category_repo.get_favorites()
    return {
        "items": [
            {"id": int(category.id), "name": str(category.name), "slug": str(category.slug)}
            for category in starred_categories
        ]
    }


@router.post("/products/manual")
def create_manual_product(
    payload: CreateManualProductRequest,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    title = str(payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Название товара обязательно")

    manual_source = _get_or_create_manual_source(db)
    currency = str(payload.currency or "USD").strip().upper() or "USD"
    description = str(payload.description or "").strip() or None
    vendor = str(payload.vendor or "").strip() or None
    product_type = str(payload.product_type or "").strip() or None
    variants = _normalize_manual_variants(payload.variants)

    variant_prices = [
        _safe_float(item.get("price"))
        for item in variants
        if _safe_float(item.get("price")) is not None and _safe_float(item.get("price")) >= 0
    ]
    price = min(variant_prices) if variant_prices else None
    requested_status = str(payload.status or "").strip().lower()
    if requested_status in {"available", "out_of_stock", "hidden"}:
        status_value = requested_status
    else:
        status_value = "available" if any(bool(item.get("available")) for item in variants) else "out_of_stock"
    weight_grams = _safe_float(payload.weight_grams)
    if weight_grams is not None and weight_grams <= 0:
        weight_grams = None

    image_ids = _normalize_int_list(payload.manual_image_asset_ids)
    manual_image_urls: list[str] = []
    if image_ids:
        assets = (
            db.query(ImageAsset)
            .filter(ImageAsset.deleted_at.is_(None))
            .filter(ImageAsset.id.in_(image_ids))
            .all()
        )
        by_id = {int(asset.id): asset for asset in assets}
        for image_id in image_ids:
            asset = by_id.get(int(image_id))
            if asset is None:
                continue
            manual_image_urls.append(f"/api/v1/products/images/{int(asset.id)}")

    bound_source_url = str(payload.bind_source_product_url or "").strip() if payload.bind_source_product_url is not None else ""
    product_url_value = bound_source_url or f"manual://product/{int(datetime.now().timestamp() * 1000)}"

    product = ParserProduct(
        source_id=int(manual_source.id),
        handle=f"manual-{int(datetime.now().timestamp() * 1000)}-{random.randint(1000, 9999)}",
        title=title,
        description=description,
        vendor=vendor,
        product_type=product_type,
        url=product_url_value,
        price=price,
        currency=currency,
        status=status_value,
        image_count=len(manual_image_urls),
        image_urls=list(manual_image_urls),
        variants=variants,
        is_auto_added=False,
        images_sync_locked=True,
        manual_image_asset_ids=list(manual_image_urls),
        manual_image_order=[f"m:{url}" for url in manual_image_urls],
        hidden_source_image_asset_ids=[],
        weight_grams=weight_grams,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    if bool(payload.bind_sync) and payload.bind_source_id is not None and str(payload.bind_source_product_url or "").strip():
        source_id = int(payload.bind_source_id)
        source_product_url = str(payload.bind_source_product_url).strip()
        normalized_currency = currency
        for idx, variant in enumerate(variants, start=1):
            variant_title = str(variant.get("title") or "").strip() or f"Variant {idx}"
            variant_price = _safe_float(variant.get("price"))
            variant_available = bool(variant.get("available"))
            source_variant_id = f"manual-bound-{int(product.id)}-{idx}"
            origin_key = f"{source_id}:{source_product_url}:{source_variant_id}"
            existing = (
                db.query(ParserProductOriginVariant)
                .filter(ParserProductOriginVariant.origin_key == origin_key)
                .one_or_none()
            )
            if existing is not None:
                continue
            db.add(
                ParserProductOriginVariant(
                    origin_key=origin_key,
                    product_id=int(product.id),
                    source_id=source_id,
                    source_product_url=source_product_url,
                    source_variant_id=source_variant_id,
                    source_variant_title=variant_title,
                    price=variant_price,
                    currency=normalized_currency,
                    available=variant_available,
                    payload={"bound_from_manual_create": True},
                )
            )
        db.commit()

    return {"ok": True, "id": int(product.id)}


@router.patch("/products/manual/{product_id}")
def update_manual_product(
    product_id: int,
    payload: UpdateManualProductRequest,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    title = str(payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Название товара обязательно")

    product = (
        db.query(ParserProduct)
        .filter(ParserProduct.id == int(product_id))
        .filter(ParserProduct.deleted_at.is_(None))
        .one_or_none()
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    manual_source = _get_or_create_manual_source(db)
    if int(product.source_id) != int(manual_source.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Можно редактировать только товары личного каталога")

    currency = str(payload.currency or "USD").strip().upper() or "USD"
    description = str(payload.description or "").strip() or None
    vendor = str(payload.vendor or "").strip() or None
    product_type = str(payload.product_type or "").strip() or None
    variants = _normalize_manual_variants(payload.variants)
    variant_prices = [
        _safe_float(item.get("price"))
        for item in variants
        if _safe_float(item.get("price")) is not None and _safe_float(item.get("price")) >= 0
    ]
    price = min(variant_prices) if variant_prices else None
    requested_status = str(payload.status or "").strip().lower()
    if requested_status in {"available", "out_of_stock", "hidden"}:
        status_value = requested_status
    else:
        status_value = "available" if any(bool(item.get("available")) for item in variants) else "out_of_stock"
    weight_grams = _safe_float(payload.weight_grams)
    if weight_grams is not None and weight_grams <= 0:
        weight_grams = None

    image_ids = _normalize_int_list(payload.manual_image_asset_ids)
    manual_image_urls: list[str] = []
    if image_ids:
        assets = (
            db.query(ImageAsset)
            .filter(ImageAsset.deleted_at.is_(None))
            .filter(ImageAsset.id.in_(image_ids))
            .all()
        )
        by_id = {int(asset.id): asset for asset in assets}
        for image_id in image_ids:
            asset = by_id.get(int(image_id))
            if asset is None:
                continue
            manual_image_urls.append(f"/api/v1/products/images/{int(asset.id)}")

    bound_source_url = str(payload.bind_source_product_url or "").strip() if payload.bind_source_product_url is not None else ""
    if bool(payload.bind_sync) and payload.bind_source_id is not None and bound_source_url:
        product.url = bound_source_url
    elif str(product.url or "").strip().lower().startswith("manual://product/") or not str(product.url or "").strip():
        product.url = f"manual://product/{int(datetime.now().timestamp() * 1000)}"

    product.title = title
    product.description = description
    product.vendor = vendor
    product.product_type = product_type
    product.price = price
    product.currency = currency
    product.status = status_value
    product.weight_grams = weight_grams
    product.weight_source = "manual" if weight_grams is not None else None
    product.weight_match_keyword = None
    product.weight_value = None
    product.weight_unit = None
    product.image_count = len(manual_image_urls)
    product.image_urls = list(manual_image_urls)
    product.variants = variants
    product.images_sync_locked = True
    product.manual_image_asset_ids = list(manual_image_urls)
    product.manual_image_order = [f"m:{url}" for url in manual_image_urls]
    product.hidden_source_image_asset_ids = []

    db.query(ParserProductOriginVariant).filter(ParserProductOriginVariant.product_id == int(product.id)).delete(synchronize_session=False)
    if bool(payload.bind_sync) and payload.bind_source_id is not None and bound_source_url:
        source_id = int(payload.bind_source_id)
        for idx, variant in enumerate(variants, start=1):
            variant_title = str(variant.get("title") or "").strip() or f"Variant {idx}"
            variant_price = _safe_float(variant.get("price"))
            variant_available = bool(variant.get("available"))
            source_variant_id = f"manual-bound-{int(product.id)}-{idx}"
            origin_key = f"{source_id}:{bound_source_url}:{source_variant_id}"
            db.add(
                ParserProductOriginVariant(
                    origin_key=origin_key,
                    product_id=int(product.id),
                    source_id=source_id,
                    source_product_url=bound_source_url,
                    source_variant_id=source_variant_id,
                    source_variant_title=variant_title,
                    price=variant_price,
                    currency=currency,
                    available=variant_available,
                    payload={"bound_from_manual_edit": True},
                )
            )

    db.commit()
    return {"ok": True, "id": int(product.id)}


@router.delete("/products/manual/{product_id}")
def delete_manual_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    product = (
        db.query(ParserProduct)
        .filter(ParserProduct.id == int(product_id))
        .filter(ParserProduct.deleted_at.is_(None))
        .one_or_none()
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    manual_source = _get_or_create_manual_source(db)
    if int(product.source_id) != int(manual_source.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Удалять можно только товары личного каталога")

    db.query(ParserProductOriginVariant).filter(ParserProductOriginVariant.product_id == int(product.id)).delete(synchronize_session=False)
    db.query(ParserProductCategoryMatch).filter(ParserProductCategoryMatch.product_id == int(product.id)).delete(synchronize_session=False)
    db.query(ParserCategoryManualProduct).filter(ParserCategoryManualProduct.product_id == int(product.id)).delete(synchronize_session=False)
    db.query(ParserFavoriteProduct).filter(ParserFavoriteProduct.product_id == int(product.id)).delete(synchronize_session=False)
    db.query(ParserDedupDecision).filter(
        or_(
            ParserDedupDecision.left_product_id == int(product.id),
            ParserDedupDecision.right_product_id == int(product.id),
            ParserDedupDecision.merged_into_product_id == int(product.id),
        )
    ).delete(synchronize_session=False)
    product.deleted_at = datetime.now()
    db.commit()
    return {"ok": True, "id": int(product.id)}


@router.get("/products/images/{image_id}")
def get_product_manual_image(image_id: int, db: Session = Depends(get_db)):
    asset = db.query(ImageAsset).filter(ImageAsset.id == image_id, ImageAsset.deleted_at.is_(None)).one_or_none()
    if asset is None or asset.storage_mode != "stored_file" or not asset.stored_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    path = Path(asset.stored_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл изображения не найден")
    return FileResponse(path)


@router.get("/products/{product_id}/starred-categories")
def get_product_starred_categories(
    product_id: int, db: Session = Depends(get_db), _: object = Depends(require_admin_access)
) -> dict[str, Any]:
    product = ParserProductRepository(db).get_active_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    category_repo = ParserCategoryRepository(db)
    starred_categories = category_repo.get_favorites()
    starred_by_id = {int(category.id): category for category in starred_categories}

    manual_map = ParserCategoryManualProductRepository(db).get_grouped_by_product_ids({int(product_id)})
    assigned = sorted(
        int(category_id)
        for category_id in manual_map.get(int(product_id), [])
        if int(category_id) in starred_by_id
    )
    return {
        "product_id": int(product_id),
        "assigned_category_ids": assigned,
        "available_categories": [
            {
                "id": int(category.id),
                "name": str(category.name),
                "slug": str(category.slug),
                "parent_id": int(category.parent_id) if category.parent_id is not None else None,
            }
            for category in starred_categories
        ],
    }


@router.put("/products/{product_id}/starred-categories")
async def set_product_starred_categories(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: object = Depends(require_admin_access),
) -> dict[str, Any]:
    product = ParserProductRepository(db).get_active_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный payload")

    target_category_ids = _normalize_int_list(payload.get("category_ids"))

    category_repo = ParserCategoryRepository(db)
    starred_categories = category_repo.get_favorites()
    starred_by_id = {int(category.id): category for category in starred_categories}
    invalid_ids = [category_id for category_id in target_category_ids if category_id not in starred_by_id]
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Категории не отмечены как избранные в админке: {', '.join(str(value) for value in sorted(invalid_ids))}",
        )

    manual_repo = ParserCategoryManualProductRepository(db)
    current_manual_map = manual_repo.get_grouped_by_product_ids({int(product_id)})
    current_starred = {
        int(category_id)
        for category_id in current_manual_map.get(int(product_id), [])
        if int(category_id) in starred_by_id
    }
    target_starred = set(target_category_ids)

    for category_id in sorted(current_starred - target_starred):
        entity = manual_repo.get_exact(category_id=category_id, product_id=product_id)
        if entity is not None:
            db.delete(entity)

    for category_id in sorted(target_starred - current_starred):
        existing = manual_repo.get_exact(category_id=category_id, product_id=product_id)
        if existing is None:
            manual_repo.create(category_id=category_id, product_id=product_id)

    db.commit()
    CategoryIndexService(db).sync_manual_links_for_product(product_id=product_id)

    return {
        "ok": True,
        "message": "Избранные категории товара обновлены",
        "product_id": int(product_id),
        "assigned_category_ids": sorted(target_starred),
    }


@router.api_route("/products", methods=_PROXY_ROOT_METHODS)
async def proxy_products_root(request: Request, _: object = Depends(require_admin_access)) -> Response:
    body = await request.body()
    return forward_service_request(request=request, path="products", body=body)


@router.api_route("/products/{path:path}", methods=_PROXY_PATH_METHODS)
async def proxy_products_path(path: str, request: Request, _: object = Depends(require_admin_access)) -> Response:
    if request.method.upper() == "GET":
        try:
            int(path)
        except ValueError:
            body = await request.body()
            return forward_service_request(request=request, path=f"products/{path}", body=body)
        return Response(status_code=404)
    body = await request.body()
    return forward_service_request(request=request, path=f"products/{path}", body=body)
