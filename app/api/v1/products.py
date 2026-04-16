"""Products API: backend is source of truth for read-side pricing."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import random
import unicodedata
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ImageAsset, ParserCategory, ParserProduct, ParserProductCategoryMatch
from app.repositories import (
    ParserCategoryKeywordRepository,
    ParserCategoryManualProductRepository,
    ParserCategoryRepository,
    ParserProductRepository,
    ParserSourceRepository,
)
from app.schemas.parser import PricingExampleProductResponse
from app.services.catalog.category_index_service import CategoryIndexService
from app.services.catalog.category_tree_utils import build_tree
from app.services.proxy.service_api_proxy import forward_service_request
from app.services.auth.admin_auth_service import require_admin_access
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


def _effective_status_from_variants(stored_status: str, variants: Any) -> str:
    if stored_status in {"hidden", "unavailable"}:
        return stored_status
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


def _resolve_image_asset_ids_for_products(db: Session, products: list[ParserProduct]) -> dict[int, list[int]]:
    """Resolve image ids for response without mutating parser_product rows."""
    if not products:
        return {}

    targets: list[tuple[int, list[str], list[int]]] = []
    all_urls: set[str] = set()
    for product in products:
        existing_ids = _normalize_int_list(product.image_asset_ids)
        urls = _normalize_image_urls(product.image_urls)
        if existing_ids:
            targets.append((int(product.id), urls, existing_ids))
            continue
        if not urls:
            continue
        targets.append((int(product.id), urls, []))
        all_urls.update(urls)

    if not targets:
        return {}

    asset_by_url: dict[str, ImageAsset] = {}
    if all_urls:
        existing_assets = (
            db.query(ImageAsset)
            .filter(ImageAsset.deleted_at.is_(None))
            .filter(ImageAsset.source_url.in_(list(all_urls)))
            .all()
        )
        asset_by_url = {str(asset.source_url): asset for asset in existing_assets}
    created_any = False

    for url in all_urls:
        if url in asset_by_url:
            continue
        asset = ImageAsset(source_url=url, storage_mode="proxy")
        db.add(asset)
        asset_by_url[url] = asset
        created_any = True

    if created_any:
        db.flush()
        db.commit()

    resolved_by_product_id: dict[int, list[int]] = {}
    for product_id, urls, existing_ids in targets:
        if existing_ids:
            resolved_by_product_id[product_id] = existing_ids
            continue
        resolved_ids = [int(asset_by_url[url].id) for url in urls if asset_by_url.get(url) is not None and asset_by_url[url].id is not None]
        if resolved_ids:
            resolved_by_product_id[product_id] = resolved_ids
    return resolved_by_product_id


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
        "title": str(item.get("title") or ""),
        "vendor": item.get("vendor"),
        "url": str(item.get("url") or ""),
        "price": _safe_float(item.get("price")),
        "currency": str(item.get("currency") or "RUB"),
        "source_price": _safe_float(item.get("source_price")),
        "source_currency": item.get("source_currency"),
        "status": str(item.get("status") or "hidden"),
        "image_count": int(item.get("image_count") or 0),
        "image_urls": list(item.get("image_urls") or []),
        "image_ids": _normalize_int_list(item.get("image_ids")),
        "buyout_price_rub": _extract_buyout_price_rub(item),
        "is_favorite": bool(item.get("is_favorite")),
    }


def _resolve_primary_image_url(item: dict[str, Any]) -> str | None:
    image_ids = _normalize_int_list(item.get("image_ids"))
    if image_ids:
        return f"/api/v1/images/{int(image_ids[0])}"
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


def _normalize_legacy_source_price(raw_price: Any, currency: str | None) -> float | None:
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
    source_price = _normalize_legacy_source_price(raw_source_price, source_currency)
    return source_price, source_currency


def _apply_backend_pricing_to_item(
    *,
    item: dict[str, Any],
    settings_service: PricingSettingsService,
    settings,
    source_profile_map: dict[int, Any],
    weight_rules: list[Any],
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
    item["status"] = _effective_status_from_variants(normalized_status, item.get("variants"))
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
    favorite_manual_category_ids_by_product: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    try:
        return _apply_backend_pricing_to_item(
            item=item,
            settings_service=settings_service,
            settings=settings,
            source_profile_map=source_profile_map,
            weight_rules=weight_rules,
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

    item["internal_category_ids"] = [int(node.id) for node in matched_nodes]
    item["internal_category_names"] = [str(node.name) for node in matched_nodes]
    item["internal_category_slugs"] = [str(node.slug) for node in matched_nodes]
    if matched_nodes:
        item["internal_category_id"] = int(matched_nodes[0].id)
        item["internal_category_name"] = str(matched_nodes[0].name)
        item["internal_category_slug"] = str(matched_nodes[0].slug)
    else:
        item["internal_category_id"] = None
        item["internal_category_name"] = None
        item["internal_category_slug"] = None


def _product_row_to_item(product: ParserProduct) -> dict[str, Any]:
    return {
        "id": int(product.id),
        "source_id": int(product.source_id),
        "handle": str(product.handle),
        "title": str(product.title),
        "vendor": product.vendor,
        "product_type": product.product_type,
        "url": str(product.url),
        "price": product.price,
        "currency": str(product.currency),
        "status": str(product.status),
        "image_count": int(product.image_count or 0),
        "image_urls": list(product.image_urls or []),
        "image_ids": list(product.image_asset_ids or []),
        "variants": list(product.variants or []),
        "weight_grams": product.weight_grams,
        "weight_source": product.weight_source,
        "weight_match_keyword": product.weight_match_keyword,
        "weight_value": product.weight_value,
        "weight_unit": product.weight_unit,
        "created_at": product.created_at.isoformat() if product.created_at is not None else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at is not None else None,
    }


def _apply_admin_product_filters(
    query,
    *,
    search: str | None,
    source_id: int | None,
    vendor: str | None,
    product_type: str | None,
    status_filter: str | None,
):
    normalized_vendor_expr = func.regexp_replace(
        func.lower(func.trim(func.coalesce(ParserProduct.vendor, ""))),
        "[^[:alnum:]]+",
        "",
        "g",
    )
    normalized_type_expr = func.lower(func.trim(func.coalesce(ParserProduct.product_type, "")))
    if source_id is not None:
        query = query.filter(ParserProduct.source_id == int(source_id))
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
                ParserProduct.vendor.ilike(pattern),
                ParserProduct.product_type.ilike(pattern),
                ParserProduct.handle.ilike(pattern),
                ParserProduct.url.ilike(pattern),
            )
        )
    return query


def _project_admin_table_item(item: dict[str, Any]) -> dict[str, Any]:
    names = [str(value).strip() for value in list(item.get("internal_category_names") or []) if str(value or "").strip()]
    if names:
        internal_category_label = ", ".join(names)
    else:
        single_name = str(item.get("internal_category_name") or "").strip()
        internal_category_label = single_name or "Прочее"

    return {
        "id": int(item.get("id") or 0),
        "source_id": int(item.get("source_id") or 0),
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "product_type": item.get("product_type"),
        "status": str(item.get("status") or "hidden"),
        "image_count": int(item.get("image_count") or 0),
        "image_urls": list(item.get("image_urls") or []),
        "image_ids": _normalize_int_list(item.get("image_ids")),
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
        db.query(ParserProduct)
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
        db.query(func.count(ParserProduct.id))
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
    resolved_image_ids = _resolve_image_asset_ids_for_products(db, rows)

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /admin/products/table; fallback to empty rules")
        weight_rules = []

    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False)
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

    source_repo = ParserSourceRepository(db)
    source_profile_map = {
        int(source.id): source
        for source in source_repo.get_active_by_ids({int(item.source_id) for item in rows})
    }

    items: list[dict[str, Any]] = []
    for row in rows:
        raw_item = _product_row_to_item(row)
        if int(row.id) in resolved_image_ids:
            raw_item["image_ids"] = list(resolved_image_ids[int(row.id)])
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
        items.append(_project_admin_table_item(enriched))

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
    normalized_vendor_expr = func.regexp_replace(
        func.lower(func.trim(func.coalesce(ParserProduct.vendor, ""))),
        "[^[:alnum:]]+",
        "",
        "g",
    )
    vendor_rows = (
        vendor_query
        .with_entities(
            normalized_vendor_expr.label("vendor_key"),
            func.min(func.trim(ParserProduct.vendor)).label("vendor_label"),
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


@router.get("/products")
async def get_products(request: Request, db: Session = Depends(get_db)) -> Response:
    upstream = forward_service_request(request=request, path="products", body=b"")
    if upstream.status_code >= 400:
        return upstream

    payload = _upstream_json_or_none(upstream)
    if not isinstance(payload, dict):
        return upstream

    items = payload.get("items")
    if not isinstance(items, list):
        return upstream

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /products; fallback to empty rules")
        weight_rules = []
    source_repo = ParserSourceRepository(db)
    source_ids = {
        source_id
        for source_id in (_safe_int(item.get("source_id")) for item in items if isinstance(item, dict))
        if source_id is not None
    }
    source_profile_map = {
        int(source.id): source
        for source in source_repo.get_active_by_ids(source_ids)
    }
    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False)
    flat_tree = _flatten_tree(category_tree)
    category_node_by_id = {int(node.id): node for node in flat_tree if bool(getattr(node, "is_enabled", True))}
    fallback_node = next((node for node in flat_tree if bool(getattr(node, "is_fallback", False))), None)
    manual_repo = ParserCategoryManualProductRepository(db)
    product_ids = {
        product_id
        for product_id in (_safe_int(item.get("id")) for item in items if isinstance(item, dict))
        if product_id is not None
    }
    manual_map = manual_repo.get_grouped_by_product_ids(product_ids)
    indexed_category_ids = category_index_service.get_grouped_category_ids(product_ids)
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}
    favorite_manual_map = {
        int(product_id): [int(category_id) for category_id in category_ids if int(category_id) in favorite_category_ids]
        for product_id, category_ids in manual_map.items()
    }

    try:
        enriched_items: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                enriched_items.append(item)
                continue
            product_id = _safe_int(item.get("id"))
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


@router.get("/catalog/products")
def get_catalog_products(
    db: Session = Depends(get_db),
    category_slug: str | None = Query(default=None),
    search: str | None = Query(default=None),
    source_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=36, ge=1, le=_CATALOG_MAX_LIMIT),
    cursor: str | None = Query(default=None),
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
    category_index_service.ensure_fresh(require_counts=False)
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
    source_repo = ParserSourceRepository(db)
    favorite_category_ids = {int(category.id) for category in category_repo.get_favorites()}

    normalized_search = (search or "").strip()
    accepted_items: list[dict[str, Any]] = []
    next_cursor: str | None = None
    scan_cursor = decoded_cursor

    for _ in range(_CATALOG_MAX_SCAN_PAGES):
        query = (
            product_repo.query()
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.status.in_(tuple(_PUBLIC_PRODUCT_STATUSES)))
        )
        if source_id is not None:
            query = query.filter(ParserProduct.source_id == int(source_id))
        if normalized_search:
            pattern = f"%{normalized_search}%"
            query = query.filter(
                or_(
                    ParserProduct.title.ilike(pattern),
                    ParserProduct.vendor.ilike(pattern),
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
        resolved_image_ids = _resolve_image_asset_ids_for_products(db, rows)

        product_ids = {int(item.id) for item in rows}
        manual_map = manual_repo.get_grouped_by_product_ids(product_ids)
        indexed_category_ids = category_index_service.get_grouped_category_ids(product_ids)
        favorite_manual_map = {
            int(product_id): [int(category_id) for category_id in category_ids if int(category_id) in favorite_category_ids]
            for product_id, category_ids in manual_map.items()
        }
        source_profile_map = {
            int(source.id): source
            for source in source_repo.get_active_by_ids({int(item.source_id) for item in rows})
        }

        for row in rows:
            raw_item = _product_row_to_item(row)
            if int(row.id) in resolved_image_ids:
                raw_item["image_ids"] = list(resolved_image_ids[int(row.id)])
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

    return {
        "items": accepted_items[: int(limit)],
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
        db.query(ParserProduct)
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
    resolved_image_ids = _resolve_image_asset_ids_for_products(db, rows)

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /admin/products; fallback to empty rules")
        weight_rules = []

    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False)
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

    source_repo = ParserSourceRepository(db)
    source_profile_map = {
        int(source.id): source
        for source in source_repo.get_active_by_ids({int(item.source_id) for item in rows})
    }

    items: list[dict[str, Any]] = []
    for row in rows:
        raw_item = _product_row_to_item(row)
        if int(row.id) in resolved_image_ids:
            raw_item["image_ids"] = list(resolved_image_ids[int(row.id)])
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
        db.query(ParserProduct)
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

    resolved_image_ids = _resolve_image_asset_ids_for_products(db, [row])
    raw_item = _product_row_to_item(row)
    if int(row.id) in resolved_image_ids:
        raw_item["image_ids"] = list(resolved_image_ids[int(row.id)])

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /products/pricing-example; fallback to empty rules")
        weight_rules = []

    source_repo = ParserSourceRepository(db)
    source_profile_map = {
        int(source.id): source
        for source in source_repo.get_active_by_ids({int(row.source_id)})
    }
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
    source_profile = source_profile_map.get(int(row.source_id))

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


@router.get("/products/{product_id}")
async def get_product(product_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    upstream = forward_service_request(request=request, path=f"products/{product_id}", body=b"")
    if upstream.status_code >= 400:
        return upstream

    payload = _upstream_json_or_none(upstream)
    if not isinstance(payload, dict):
        return upstream

    settings_service = PricingSettingsService(db)
    settings = settings_service.get_settings(refresh_bybit=False)
    try:
        weight_rules = WeightRuleService(db).get_matching_rules()
    except Exception:
        LOGGER.exception("Failed to load weight rules for /products/{product_id}; fallback to empty rules")
        weight_rules = []
    source_repo = ParserSourceRepository(db)
    source_id = _safe_int(payload.get("source_id"))
    source_profile = source_repo.get_active_by_id(source_id) if source_id is not None else None
    source_profile_map = {int(source_profile.id): source_profile} if source_profile is not None else {}
    category_repo = ParserCategoryRepository(db)
    keyword_repo = ParserCategoryKeywordRepository(db)
    category_tree = build_tree(category_repo.get_all_active(), keyword_repo)
    category_index_service = CategoryIndexService(db)
    category_index_service.ensure_fresh(require_counts=False)
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
    local_product = ParserProductRepository(db).get_active_by_id(product_id)
    if local_product is not None and str(local_product.status or "").strip().lower() == "unavailable":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    if local_product is not None:
        resolved_single = _resolve_image_asset_ids_for_products(db, [local_product])
        payload["image_ids"] = list(resolved_single.get(int(local_product.id), list(local_product.image_asset_ids or [])))
        if isinstance(local_product.image_urls, list) and local_product.image_urls:
            payload["image_urls"] = list(local_product.image_urls or [])
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
            favorite_manual_category_ids_by_product=favorite_manual_map,
        )
    except InvalidProductStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Обнаружен недопустимый статус товара в базе: {exc}",
        ) from exc
    return JSONResponse(
        content=priced,
        status_code=upstream.status_code,
        headers=_json_response_headers(upstream),
    )


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

    next_status_raw = payload.get("status")
    next_status = None
    if next_status_raw is not None:
        next_status = str(next_status_raw).strip().lower()
        if next_status not in _PUBLIC_PRODUCT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые статусы: available, out_of_stock, hidden",
            )

    if next_status is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет поддерживаемых полей для обновления")

    product_repo = ParserProductRepository(db)
    product = product_repo.get_active_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    product.status = next_status
    db.commit()
    effective_status = _effective_status_from_variants(str(product.status), list(product.variants or []))

    patched_payload = {
        "id": int(product.id),
        "source_id": int(product.source_id),
        "handle": str(product.handle),
        "title": str(product.title),
        "vendor": product.vendor,
        "product_type": product.product_type,
        "url": str(product.url),
        "price": product.price,
        "currency": str(product.currency),
        "source_price": getattr(product, "source_price", None) if getattr(product, "source_price", None) is not None else product.price,
        "source_currency": str(getattr(product, "source_currency", None) or product.currency),
        "status": effective_status,
        "image_count": int(product.image_count or 0),
        "image_urls": list(product.image_urls or []),
        "image_ids": list(product.image_asset_ids or []),
        "variants": list(product.variants or []),
        "weight_grams": product.weight_grams,
        "weight_source": product.weight_source,
        "weight_match_keyword": product.weight_match_keyword,
        "weight_value": product.weight_value,
        "weight_unit": product.weight_unit,
        "created_at": product.created_at.isoformat() if product.created_at is not None else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at is not None else None,
    }

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
