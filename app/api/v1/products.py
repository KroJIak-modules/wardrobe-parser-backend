"""Products API: backend is source of truth for read-side pricing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import (
    ParserCategoryKeywordRepository,
    ParserCategoryRepository,
    ParserFavoriteProductRepository,
    ParserProductRepository,
    ParserSourceRepository,
)
from app.services.catalog.category_assignment import CategoryAssigner
from app.services.catalog.category_tree_utils import build_tree
from app.services.proxy.service_api_proxy import forward_service_request
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.weight_rule_service import WeightRuleService


router = APIRouter(tags=["products"])
LOGGER = logging.getLogger(__name__)
_PROXY_ROOT_METHODS = ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_PROXY_PATH_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_JSON_UNSAFE_HEADERS = {"content-length", "content-encoding", "transfer-encoding"}


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
    parsed = _safe_float(raw_price)
    if parsed is None:
        return None
    normalized_currency = (currency or "").upper()
    if normalized_currency not in {"USD", "EUR", "GBP"}:
        return parsed
    if parsed < 10_000:
        return parsed
    if isinstance(raw_price, str):
        stripped = raw_price.strip().replace(",", ".")
        # Do not touch normal decimal representation, only integer-like legacy cents.
        if "." in stripped:
            return parsed
        if stripped.isdigit():
            return parsed / 100.0
        return parsed
    if isinstance(raw_price, (int, float)) and float(raw_price).is_integer():
        return parsed / 100.0
    return parsed


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
    category_assigner: CategoryAssigner | None = None,
    is_favorite: bool = False,
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
        seller_delivery_rub=(
            float(source_profile.seller_delivery_rub)
            if source_profile is not None and getattr(source_profile, "seller_delivery_rub", None) is not None
            else None
        ),
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
    if category_assigner is not None:
        matched = category_assigner.match(item, is_favorite=is_favorite)
        item["is_favorite"] = bool(is_favorite)
        item["internal_category_id"] = matched.category_id
        item["internal_category_name"] = matched.category_name
        item["internal_category_slug"] = matched.category_slug
    return item


def _safe_apply_backend_pricing_to_item(
    *,
    item: dict[str, Any],
    settings_service: PricingSettingsService,
    settings,
    source_profile_map: dict[int, Any],
    weight_rules: list[Any],
    category_assigner: CategoryAssigner | None = None,
    is_favorite: bool = False,
) -> dict[str, Any]:
    try:
        return _apply_backend_pricing_to_item(
            item=item,
            settings_service=settings_service,
            settings=settings,
            source_profile_map=source_profile_map,
            weight_rules=weight_rules,
            category_assigner=category_assigner,
            is_favorite=is_favorite,
        )
    except Exception:
        LOGGER.exception("Failed to enrich product item", extra={"product_id": item.get("id")})
        return item


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
    category_assigner = CategoryAssigner(category_tree)
    favorite_repo = ParserFavoriteProductRepository(db)
    product_ids = {
        product_id
        for product_id in (_safe_int(item.get("id")) for item in items if isinstance(item, dict))
        if product_id is not None
    }
    favorite_product_ids = favorite_repo.get_product_id_set_for_ids(product_ids)

    payload["items"] = [
        _safe_apply_backend_pricing_to_item(
            item=item,
            settings_service=settings_service,
            settings=settings,
            source_profile_map=source_profile_map,
            weight_rules=weight_rules,
            category_assigner=category_assigner,
            is_favorite=(_safe_int(item.get("id")) in favorite_product_ids if isinstance(item, dict) else False),
        )
        if isinstance(item, dict)
        else item
        for item in items
    ]
    return JSONResponse(
        content=payload,
        status_code=upstream.status_code,
        headers=_json_response_headers(upstream),
    )


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
    category_assigner = CategoryAssigner(category_tree)
    favorite_repo = ParserFavoriteProductRepository(db)
    favorite_product_ids = favorite_repo.get_product_id_set_for_ids({int(product_id)})
    priced = _safe_apply_backend_pricing_to_item(
        item=payload,
        settings_service=settings_service,
        settings=settings,
        source_profile_map=source_profile_map,
        weight_rules=weight_rules,
        category_assigner=category_assigner,
        is_favorite=int(product_id) in favorite_product_ids,
    )
    return JSONResponse(
        content=priced,
        status_code=upstream.status_code,
        headers=_json_response_headers(upstream),
    )


@router.api_route("/products", methods=_PROXY_ROOT_METHODS)
async def proxy_products_root(request: Request) -> Response:
    body = await request.body()
    return forward_service_request(request=request, path="products", body=body)


@router.post("/products/{product_id}/favorite")
def add_product_favorite(product_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    product_repo = ParserProductRepository(db)
    product = product_repo.get_active_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    favorite_repo = ParserFavoriteProductRepository(db)
    existing = favorite_repo.get_by_product_id(product_id)
    if existing is None:
        favorite_repo.create(product_id=product_id)
    product.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "product_id": product_id, "is_favorite": True}


@router.delete("/products/{product_id}/favorite")
def remove_product_favorite(product_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    product_repo = ParserProductRepository(db)
    product = product_repo.get_active_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    favorite_repo = ParserFavoriteProductRepository(db)
    existing = favorite_repo.get_by_product_id(product_id)
    if existing is not None:
        db.delete(existing)
    product.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "product_id": product_id, "is_favorite": False}


@router.api_route("/products/{path:path}", methods=_PROXY_PATH_METHODS)
async def proxy_products_path(path: str, request: Request) -> Response:
    if request.method.upper() == "GET":
        try:
            int(path)
        except ValueError:
            body = await request.body()
            return forward_service_request(request=request, path=f"products/{path}", body=body)
        return Response(status_code=404)
    body = await request.body()
    return forward_service_request(request=request, path=f"products/{path}", body=body)
