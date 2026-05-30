"""Service layer for dedup candidates and moderation actions."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ParserProductOriginVariant
from app.repositories import ParserDedupDecisionRepository, ParserProductRepository, ParserSourceRepository
from app.schemas.parser import (
    DedupCandidateListResponse,
    DedupCombineRequest,
    DedupCandidateResponse,
    DedupDecisionListResponse,
    DedupDecisionResponse,
    DedupMergeRequest,
    DedupRejectRequest,
    DedupUndoRequest,
    ProductResponse,
)
from app.services.moderation.dedup_decision import (
    upsert_combine_decision,
    upsert_merge_decision,
    upsert_reject_decision,
)
from app.services.moderation.dedup_scoring import (
    build_candidate_keys,
    candidate_score,
    extract_variant_fingerprints,
    normalize_text,
    pair_key,
)
from app.services.settings.pricing_service import PricingSettingsService


class DedupService:
    """Encapsulates duplicate detection and moderation business rules."""

    def __init__(self, db: Session):
        self.db = db
        self.product_repo = ParserProductRepository(db)
        self.decision_repo = ParserDedupDecisionRepository(db)
        self.source_repo = ParserSourceRepository(db)

    @staticmethod
    def _normalize_source_price(price: float | None, currency: str | None) -> float | None:
        _ = currency
        if price is None:
            return None
        return float(price)

    @staticmethod
    def _derive_currency_from_variants(variants: Any) -> str | None:
        parsed = variants if isinstance(variants, list) else []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            normalized = str(item.get("currency") or "").strip().upper()[:3]
            if len(normalized) == 3:
                return normalized
        return None

    def _build_effective_prices(self, products: list[Any]) -> dict[int, float | None]:
        if not products:
            return {}

        settings_service = PricingSettingsService(self.db)
        pricing_settings = settings_service.get_settings(refresh_bybit=False)
        product_ids = {int(item.id) for item in products if getattr(item, "id", None) is not None}
        origin_rows = (
            self.db.query(
                ParserProductOriginVariant.product_id.label("product_id"),
                func.min(ParserProductOriginVariant.source_id).label("source_id"),
            )
            .filter(ParserProductOriginVariant.product_id.in_(list(product_ids)))
            .group_by(ParserProductOriginVariant.product_id)
            .all()
        )
        source_id_by_product = {
            int(row.product_id): int(row.source_id)
            for row in origin_rows
            if row.product_id is not None and row.source_id is not None
        }
        source_ids = set(source_id_by_product.values())
        source_ids.update(int(item.source_id) for item in products if getattr(item, "source_id", None) is not None)
        source_profile_map = {
            int(source.id): source
            for source in self.source_repo.get_active_by_ids(source_ids)
        }

        effective_prices: dict[int, float | None] = {}
        for product in products:
            source_id = source_id_by_product.get(int(product.id))
            if source_id is None and getattr(product, "source_id", None) is not None:
                source_id = int(product.source_id)
            source_profile = source_profile_map.get(int(source_id)) if source_id is not None else None
            variants = product.variants if isinstance(product.variants, list) else []
            source_currency = self._derive_currency_from_variants(variants)
            source_price = self._normalize_source_price(product.price, source_currency)
            pricing = settings_service.calculate_for_product(
                source_price=source_price,
                source_currency=source_currency,
                weight_grams=product.weight_grams,
                supplier_id=(
                    int(source_profile.supplier_id)
                    if source_profile is not None and source_profile.supplier_id is not None
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
                variants=variants,
                settings=pricing_settings,
            )
            if pricing.final_price_rub is not None:
                effective_prices[int(product.id)] = float(pricing.final_price_rub)
            else:
                effective_prices[int(product.id)] = source_price
        return effective_prices

    @staticmethod
    def _product_response_with_effective_price(product: Any, effective_price_rub: float | None) -> ProductResponse:
        payload = DedupService._to_product_response(product)
        if effective_price_rub is not None:
            payload.price = float(round(effective_price_rub, 2))
            payload.currency = "RUB"
        return payload

    @classmethod
    def _to_product_response(cls, product: Any) -> ProductResponse:
        variants = list(getattr(product, "variants", []) or [])
        currency = cls._derive_currency_from_variants(variants) or "USD"
        return ProductResponse(
            id=int(getattr(product, "id")),
            source_id=int(getattr(product, "source_id")),
            handle=str(getattr(product, "handle", "") or ""),
            title=str(getattr(product, "title", "") or ""),
            vendor=getattr(product, "vendor", None),
            product_type=getattr(product, "product_type", None),
            url=str(getattr(product, "url", "") or ""),
            price=cls._safe_float(getattr(product, "price", None)),
            currency=currency,
            status=str(getattr(product, "status", "available") or "available"),
            image_count=int(getattr(product, "image_count", 0) or 0),
            image_urls=list(getattr(product, "image_urls", []) or []),
            weight_grams=cls._safe_float(getattr(product, "weight_grams", None)),
            weight_source=getattr(product, "weight_source", None),
            weight_match_keyword=getattr(product, "weight_match_keyword", None),
            weight_value=cls._safe_float(getattr(product, "weight_value", None)),
            weight_unit=getattr(product, "weight_unit", None),
            variants=variants,
            description=getattr(product, "description", None),
            created_at=getattr(product, "created_at", None),
            updated_at=getattr(product, "updated_at", None),
        )

    @staticmethod
    def _unique_list(values: Iterable[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for item in values:
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
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

    @staticmethod
    def _variant_is_available(variant: dict[str, Any]) -> bool:
        raw_available = variant.get("available")
        if isinstance(raw_available, bool):
            if raw_available:
                return True
        elif raw_available is not None:
            if str(raw_available).strip().lower() in {"1", "true", "yes", "y", "in_stock"}:
                return True
        inventory = variant.get("inventory_quantity")
        if inventory is not None:
            try:
                return float(inventory) > 0
            except (TypeError, ValueError):
                pass
        return False

    @classmethod
    def _resolve_status_from_variants(cls, variants: list[dict[str, Any]], fallback_status: str) -> str:
        if str(fallback_status or "").strip().lower() == "hidden":
            return "hidden"
        if not variants:
            return "out_of_stock"
        if any(cls._variant_is_available(item) for item in variants if isinstance(item, dict)):
            return "available"
        return "out_of_stock"

    @classmethod
    def _resolve_price_from_variants(
        cls,
        *,
        variants: list[dict[str, Any]],
        fallback_price: float | None,
    ) -> float | None:
        available_prices: list[float] = []
        any_prices: list[float] = []
        for item in variants:
            if not isinstance(item, dict):
                continue
            parsed = cls._safe_float(item.get("price"))
            if parsed is None:
                continue
            any_prices.append(parsed)
            if cls._variant_is_available(item):
                available_prices.append(parsed)
        if available_prices:
            return min(available_prices)
        if any_prices:
            return min(any_prices)
        return fallback_price

    @staticmethod
    def _merge_variants(primary_variants: list[dict[str, Any]], secondary_variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()

        def variant_key(variant: dict[str, Any]) -> str:
            values = [
                normalize_text(str(variant.get("source_key") or "")),
                normalize_text(str(variant.get("source_product_url") or "")),
                normalize_text(str(variant.get("source_variant_id") or "")),
                normalize_text(str(variant.get("id") or "")),
                normalize_text(str(variant.get("sku") or "")),
                normalize_text(str(variant.get("title") or "")),
                normalize_text(str(variant.get("option1") or "")),
                normalize_text(str(variant.get("option2") or "")),
                normalize_text(str(variant.get("option3") or "")),
            ]
            return "|".join(values)

        for item in [*primary_variants, *secondary_variants]:
            if not isinstance(item, dict):
                continue
            key = variant_key(item)
            if not key:
                key = normalize_text(str(item))
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(item))
        return result

    @staticmethod
    def _choose_primary_for_combine(left: Any, right: Any) -> tuple[Any, Any]:
        left_variants = left.variants if isinstance(left.variants, list) else []
        right_variants = right.variants if isinstance(right.variants, list) else []
        left_score = (
            len(left_variants),
            int(left.image_count or 0),
            len(left.image_urls or []),
            int(left.id),
        )
        right_score = (
            len(right_variants),
            int(right.image_count or 0),
            len(right.image_urls or []),
            int(right.id),
        )
        if left_score >= right_score:
            return left, right
        return right, left

    def _resolve_pair_or_404(self, left_id: int, right_id: int) -> tuple[Any, Any]:
        left = self.product_repo.get_active_by_id(left_id)
        right = self.product_repo.get_active_by_id(right_id)
        if not left or not right:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Одна из карточек не найдена или уже обработана",
            )
        return left, right

    @staticmethod
    def _derive_unavailable_fallback_status(variants: list[dict[str, Any]] | None) -> str:
        parsed = variants if isinstance(variants, list) else []
        if any(DedupService._variant_is_available(item) for item in parsed if isinstance(item, dict)):
            return "available"
        return "out_of_stock"

    @staticmethod
    def _serialize_datetime(value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None

    def _snapshot_product(self, product: Any) -> dict[str, Any]:
        return {
            "id": int(product.id),
            "source_id": int(product.source_id),
            "source_external_id": str(product.source_external_id or "").strip() or None,
            "canonical_url": str(product.canonical_url or "").strip() or None,
            "handle": str(product.handle or "").strip(),
            "title": str(product.title or "").strip(),
            "description": product.description,
            "vendor": str(product.vendor or "").strip() or None,
            "product_type": str(product.product_type or "").strip() or None,
            "url": str(product.url or "").strip(),
            "price": self._safe_float(product.price),
            "status": str(product.status or "unavailable"),
            "image_count": int(product.image_count or 0),
            "image_urls": list(product.image_urls or []),
            "image_asset_ids": list(product.image_asset_ids or []),
            "variants": list(product.variants or []),
            "deleted_at": self._serialize_datetime(getattr(product, "deleted_at", None)),
            "updated_at": self._serialize_datetime(getattr(product, "updated_at", None)),
            "title_override": getattr(product, "title_override", None),
            "description_override": getattr(product, "description_override", None),
            "title_sync_locked": bool(getattr(product, "title_sync_locked", False)),
            "description_sync_locked": bool(getattr(product, "description_sync_locked", False)),
            "description_visible_override": getattr(product, "description_visible_override", None),
            "images_sync_locked": bool(getattr(product, "images_sync_locked", False)),
            "hidden_source_image_asset_ids": list(getattr(product, "hidden_source_image_asset_ids", []) or []),
            "manual_image_asset_ids": list(getattr(product, "manual_image_asset_ids", []) or []),
            "manual_image_order": list(getattr(product, "manual_image_order", []) or []),
            "is_auto_added": bool(getattr(product, "is_auto_added", True)),
            "auto_hide_force_visible": bool(getattr(product, "auto_hide_force_visible", False)),
            "weight_grams": self._safe_float(getattr(product, "weight_grams", None)),
            "weight_source": str(getattr(product, "weight_source", "") or "").strip() or None,
            "weight_match_keyword": str(getattr(product, "weight_match_keyword", "") or "").strip() or None,
            "weight_value": self._safe_float(getattr(product, "weight_value", None)),
            "weight_unit": str(getattr(product, "weight_unit", "") or "").strip() or None,
        }

    def _restore_product(self, product: Any, snapshot: dict[str, Any]) -> None:
        product.source_id = int(snapshot.get("source_id") or product.source_id)
        product.source_external_id = snapshot.get("source_external_id")
        product.canonical_url = snapshot.get("canonical_url")
        product.handle = str(snapshot.get("handle") or product.handle)
        product.title = str(snapshot.get("title") or product.title)
        product.description = snapshot.get("description")
        product.vendor = snapshot.get("vendor")
        product.product_type = snapshot.get("product_type")
        product.url = str(snapshot.get("url") or product.url)
        product.price = self._safe_float(snapshot.get("price"))
        product.status = str(snapshot.get("status") or product.status)
        product.image_count = int(snapshot.get("image_count") or 0)
        product.image_urls = list(snapshot.get("image_urls") or [])
        product.image_asset_ids = list(snapshot.get("image_asset_ids") or [])
        product.variants = list(snapshot.get("variants") or [])
        product.deleted_at = self._parse_datetime(snapshot.get("deleted_at"))
        product.title_override = snapshot.get("title_override")
        product.description_override = snapshot.get("description_override")
        product.title_sync_locked = bool(snapshot.get("title_sync_locked", False))
        product.description_sync_locked = bool(snapshot.get("description_sync_locked", False))
        product.description_visible_override = snapshot.get("description_visible_override")
        product.images_sync_locked = bool(snapshot.get("images_sync_locked", False))
        product.hidden_source_image_asset_ids = list(snapshot.get("hidden_source_image_asset_ids") or [])
        product.manual_image_asset_ids = list(snapshot.get("manual_image_asset_ids") or [])
        product.manual_image_order = list(snapshot.get("manual_image_order") or [])
        product.is_auto_added = bool(snapshot.get("is_auto_added", True))
        product.auto_hide_force_visible = bool(snapshot.get("auto_hide_force_visible", False))
        product.weight_grams = self._safe_float(snapshot.get("weight_grams"))
        product.weight_source = snapshot.get("weight_source")
        product.weight_match_keyword = snapshot.get("weight_match_keyword")
        product.weight_value = self._safe_float(snapshot.get("weight_value"))
        product.weight_unit = snapshot.get("weight_unit")

    def _origin_rows_for_products(self, product_ids: set[int]) -> list[ParserProductOriginVariant]:
        if not product_ids:
            return []
        return (
            self.db.query(ParserProductOriginVariant)
            .filter(ParserProductOriginVariant.product_id.in_(list(product_ids)))
            .order_by(ParserProductOriginVariant.id.asc())
            .all()
        )

    @staticmethod
    def _variant_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        return (
            0 if bool(item.get("available")) else 1,
            str(item.get("title") or item.get("source_variant_title") or item.get("id") or "").strip().lower(),
        )

    def _materialize_variants_from_origin_rows(self, origin_rows: list[ParserProductOriginVariant]) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        for row in origin_rows:
            payload = dict(row.payload) if isinstance(row.payload, dict) else {}
            payload_currency = str(payload.get("currency") or "").strip().upper()[:3]
            item: dict[str, Any] = {
                "id": str(row.source_variant_id or "").strip() or str(payload.get("id") or "").strip() or None,
                "title": str(row.source_variant_title or "").strip() or str(payload.get("title") or "").strip() or None,
                "sku": str(row.sku or "").strip() or str(payload.get("sku") or "").strip() or None,
                "price": self._safe_float(row.price),
                "currency": str(row.currency or "").strip().upper() or (payload_currency if len(payload_currency) == 3 else None),
                "available": bool(row.available),
                "source_key": str(payload.get("source_key") or "").strip() or None,
                "source_product_url": str(row.source_product_url or "").strip() or str(payload.get("source_product_url") or "").strip() or None,
                "source_variant_id": str(row.source_variant_id or "").strip() or None,
                "source_variant_title": str(row.source_variant_title or "").strip() or None,
            }
            for key, value in payload.items():
                if key not in item:
                    item[key] = value
            variants.append(item)
        variants.sort(key=self._variant_sort_key)
        return variants

    def _assign_variants_and_status_from_origins(self, product: Any) -> None:
        rows = self._origin_rows_for_products({int(product.id)})
        variants = self._materialize_variants_from_origin_rows(rows)
        product.variants = variants
        product.price = self._resolve_price_from_variants(variants=variants, fallback_price=self._safe_float(product.price))
        product.status = self._resolve_status_from_variants(variants, str(product.status or "out_of_stock"))

    def _build_created_product(
        self,
        *,
        left: Any,
        right: Any,
        pair_key_value: str,
    ) -> Any:
        product_model = self.product_repo.model_class
        merged_title = str(left.title or "").strip() or str(right.title or "").strip() or f"Dedup {pair_key_value}"
        merged_description = left.description if left.description else right.description
        merged_vendor = left.vendor if left.vendor else right.vendor
        merged_type = left.product_type if left.product_type else right.product_type
        merged_images = self._unique_list([*(left.image_urls or []), *(right.image_urls or [])])
        merged_image_ids = self._unique_list([*(left.image_asset_ids or []), *(right.image_asset_ids or [])])
        dedup_product = product_model(
            source_id=int(left.source_id),
            source_external_id=None,
            canonical_url=None,
            handle=f"dedup-{pair_key_value}",
            title=merged_title,
            description=merged_description,
            vendor=merged_vendor,
            product_type=merged_type,
            url=f"dedup://{pair_key_value}",
            price=None,
            status="out_of_stock",
            image_count=max(len(merged_images), int(left.image_count or 0), int(right.image_count or 0)),
            image_urls=merged_images,
            image_asset_ids=merged_image_ids,
            variants=[],
            is_auto_added=True,
        )
        self.db.add(dedup_product)
        self.db.flush()
        return dedup_product

    def _decision_undo_block_reason(self, decision: Any) -> str | None:
        action = str(getattr(decision, "action", "") or "").strip().lower()
        if action == "reject":
            return None
        if action not in {"merge", "combine"}:
            return "Это решение нельзя отменить"
        merged_into_id = getattr(decision, "merged_into_product_id", None)
        if merged_into_id is None:
            return "Нет данных для отката решения"
        decided_at = getattr(decision, "decided_at", None)
        if decided_at is None:
            return "Нет времени решения для проверки зависимостей"

        has_dependents = (
            self.decision_repo.query()
            .filter(self.decision_repo.model_class.id != int(decision.id))
            .filter(self.decision_repo.model_class.decided_at > decided_at)
            .filter(
                (
                    (self.decision_repo.model_class.left_product_id == int(merged_into_id))
                    | (self.decision_repo.model_class.right_product_id == int(merged_into_id))
                    | (self.decision_repo.model_class.merged_into_product_id == int(merged_into_id))
                )
            )
            .first()
            is not None
        )
        if has_dependents:
            return "Есть более поздние решения, завязанные на эту пару"
        return None

    def _dedup_only_available_enabled(self) -> bool:
        # Product decision: dedup candidates are always built only from available products.
        return True

    def get_candidates(
        self,
        limit: int = settings.dedup_candidates_default_limit,
        offset: int = 0,
    ) -> DedupCandidateListResponse:
        only_available = self._dedup_only_available_enabled()
        allowed_statuses = {"available"} if only_available else {"available", "out_of_stock"}
        safe_limit = max(1, min(int(limit), int(settings.dedup_candidates_max_limit)))
        safe_offset = max(0, int(offset))
        products = [
            item
            for item in self.product_repo.filter_products(limit=settings.dedup_scan_limit)
            if str(getattr(item, "status", "") or "").lower() in allowed_statuses
        ]
        product_by_id = {int(item.id): item for item in products}
        if len(product_by_id) < 2:
            return DedupCandidateListResponse(items=[], total=0, limit=safe_limit, offset=safe_offset)

        blocked_pair_keys = self.decision_repo.list_pair_keys()

        buckets: dict[str, list[int]] = {}
        for product in products:
            for key in build_candidate_keys(product):
                bucket = buckets.setdefault(key, [])
                bucket.append(int(product.id))

        candidate_pairs: set[str] = set()
        for product_ids in buckets.values():
            unique_ids = list(dict.fromkeys(product_ids))
            if len(unique_ids) < 2:
                continue
            bounded_ids = unique_ids[: settings.dedup_bucket_product_cap]
            for left_id, right_id in combinations(bounded_ids, 2):
                key = pair_key(left_id, right_id)
                if key in blocked_pair_keys:
                    continue
                candidate_pairs.add(key)
                if len(candidate_pairs) >= settings.dedup_pair_scan_cap:
                    break
            if len(candidate_pairs) >= settings.dedup_pair_scan_cap:
                break

        candidates: list[DedupCandidateResponse] = []
        for raw_key in sorted(candidate_pairs):
            left_id_raw, right_id_raw = raw_key.split(":")
            left = product_by_id.get(int(left_id_raw))
            right = product_by_id.get(int(right_id_raw))
            if not left or not right:
                continue

            score, reasons = candidate_score(
                left,
                right,
                left_price=self._safe_float(getattr(left, "price", None)),
                right_price=self._safe_float(getattr(right, "price", None)),
            )
            if score < settings.dedup_score_threshold:
                continue
            candidates.append(
                DedupCandidateResponse(
                    pair_key=raw_key,
                    score=score,
                    reasons=reasons,
                    left=self._to_product_response(left),
                    right=self._to_product_response(right),
                )
            )
        candidates.sort(key=lambda item: item.score, reverse=True)
        total = len(candidates)
        sliced = candidates[safe_offset : safe_offset + safe_limit]

        # Expensive final price (RUB) calculation only for current page,
        # not for full scan dataset.
        page_product_ids: set[int] = set()
        for item in sliced:
            page_product_ids.add(int(item.left.id))
            page_product_ids.add(int(item.right.id))
        if page_product_ids:
            page_products = [p for p in products if int(p.id) in page_product_ids]
            effective_prices = self._build_effective_prices(page_products)
            for item in sliced:
                left_product = product_by_id.get(int(item.left.id))
                right_product = product_by_id.get(int(item.right.id))
                if left_product is not None:
                    item.left = self._product_response_with_effective_price(
                        left_product,
                        effective_prices.get(int(item.left.id)),
                    )
                if right_product is not None:
                    item.right = self._product_response_with_effective_price(
                        right_product,
                        effective_prices.get(int(item.right.id)),
                    )
        return DedupCandidateListResponse(items=sliced, total=total, limit=safe_limit, offset=safe_offset)

    def merge_duplicate(self, payload: DedupMergeRequest) -> dict:
        if payload.primary_product_id == payload.duplicate_product_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        primary, duplicate = self._resolve_pair_or_404(payload.primary_product_id, payload.duplicate_product_id)
        primary_before = self._snapshot_product(primary)
        duplicate_before = self._snapshot_product(duplicate)
        origin_rows = self._origin_rows_for_products({int(duplicate.id)})
        moved_origins = [
            {
                "origin_id": int(row.id),
                "from_product_id": int(row.product_id),
                "to_product_id": int(primary.id),
            }
            for row in origin_rows
        ]

        if not primary.vendor and duplicate.vendor:
            primary.vendor = duplicate.vendor
        if not primary.product_type and duplicate.product_type:
            primary.product_type = duplicate.product_type
        if (primary.image_count or 0) < (duplicate.image_count or 0):
            primary.image_count = duplicate.image_count
            if duplicate.image_urls:
                primary.image_urls = duplicate.image_urls
            if duplicate.image_asset_ids:
                primary.image_asset_ids = duplicate.image_asset_ids
        primary_variants = primary.variants if isinstance(primary.variants, list) else []
        primary.price = self._resolve_price_from_variants(
            variants=primary_variants,
            fallback_price=self._safe_float(getattr(primary, "price", None)),
        )

        for row in origin_rows:
            row.product_id = int(primary.id)
        self.db.flush()
        self._assign_variants_and_status_from_origins(primary)

        duplicate.status = "unavailable"
        duplicate.deleted_at = None
        duplicate.variants = []
        duplicate.price = None
        key = pair_key(primary.id, duplicate.id)
        upsert_merge_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(primary.id, duplicate.id),
            right_product_id=max(primary.id, duplicate.id),
            merged_into_product_id=primary.id,
            snapshot_payload={
                "left_before": primary_before if int(primary.id) == min(primary.id, duplicate.id) else duplicate_before,
                "right_before": duplicate_before if int(duplicate.id) == max(primary.id, duplicate.id) else primary_before,
            },
            restore_payload={
                "mode": "merge",
                "kept_product_id": int(primary.id),
                "disabled_product_id": int(duplicate.id),
                "moved_origins": moved_origins,
            },
        )

        self.db.commit()
        return {"ok": True, "merged_into_product_id": primary.id, "disabled_product_id": duplicate.id}

    def reject_duplicate(self, payload: DedupRejectRequest) -> dict:
        if payload.product_a_id == payload.product_b_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        left, right = self._resolve_pair_or_404(payload.product_a_id, payload.product_b_id)

        key = pair_key(left.id, right.id)
        upsert_reject_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(left.id, right.id),
            right_product_id=max(left.id, right.id),
        )
        self.db.commit()
        return {"ok": True, "pair_key": key}

    def get_decisions(self, limit: int = 200, offset: int = 0) -> DedupDecisionListResponse:
        safe_limit = max(1, min(int(limit), 1000))
        safe_offset = max(0, int(offset))
        decisions = self.decision_repo.list_recent(limit=safe_limit, offset=safe_offset)
        total = int(self.decision_repo.query().count())
        product_ids: set[int] = set()
        for item in decisions:
            product_ids.add(int(item.left_product_id))
            product_ids.add(int(item.right_product_id))

        products_by_id = {
            int(product.id): product
            for product in (
                self.product_repo.query()
                .filter(self.product_repo.model_class.id.in_(list(product_ids) if product_ids else [-1]))
                .all()
            )
        }

        items: list[DedupDecisionResponse] = []
        for decision in decisions:
            left = products_by_id.get(int(decision.left_product_id))
            right = products_by_id.get(int(decision.right_product_id))
            if left is None or right is None:
                continue
            undo_block_reason = self._decision_undo_block_reason(decision)
            items.append(
                DedupDecisionResponse(
                    pair_key=str(decision.pair_key),
                    action=str(decision.action or ""),
                    decided_at=decision.decided_at,
                    can_undo=undo_block_reason is None,
                    undo_block_reason=undo_block_reason,
                    left=self._to_product_response(left),
                    right=self._to_product_response(right),
                )
            )
        return DedupDecisionListResponse(items=items, total=total, limit=safe_limit, offset=safe_offset)

    def undo_decision(self, payload: DedupUndoRequest) -> dict:
        pair_key_value = str(payload.pair_key or "").strip()
        if not pair_key_value:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pair_key обязателен")

        decision = self.decision_repo.get_by_pair_key(pair_key_value)
        if decision is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Решение не найдено")

        block_reason = self._decision_undo_block_reason(decision)
        if block_reason is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=block_reason)

        action = str(decision.action or "").strip().lower()
        decision_deleted = False
        if action in {"merge", "combine"}:
            snapshot_payload = decision.snapshot_payload if isinstance(decision.snapshot_payload, dict) else {}
            restore_payload = decision.restore_payload if isinstance(decision.restore_payload, dict) else {}
            left_snapshot = snapshot_payload.get("left_before") if isinstance(snapshot_payload.get("left_before"), dict) else None
            right_snapshot = snapshot_payload.get("right_before") if isinstance(snapshot_payload.get("right_before"), dict) else None
            if left_snapshot is None or right_snapshot is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Нет snapshot для отката решения")

            left_product = self.product_repo.get_by_id(int(decision.left_product_id))
            right_product = self.product_repo.get_by_id(int(decision.right_product_id))
            if left_product is None or right_product is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Невозможно откатить: товар из пары не найден")
            self._restore_product(left_product, left_snapshot)
            self._restore_product(right_product, right_snapshot)

            moved_origins = restore_payload.get("moved_origins") if isinstance(restore_payload.get("moved_origins"), list) else []
            if moved_origins:
                origin_ids = [
                    int(item.get("origin_id"))
                    for item in moved_origins
                    if isinstance(item, dict) and (isinstance(item.get("origin_id"), int) or str(item.get("origin_id")).isdigit())
                ]
                rows = (
                    self.db.query(ParserProductOriginVariant)
                    .filter(ParserProductOriginVariant.id.in_(origin_ids))
                    .all()
                )
                from_map = {
                    int(item.get("origin_id")): int(item.get("from_product_id"))
                    for item in moved_origins
                    if isinstance(item, dict)
                    and (isinstance(item.get("origin_id"), int) or str(item.get("origin_id")).isdigit())
                    and (isinstance(item.get("from_product_id"), int) or str(item.get("from_product_id")).isdigit())
                }
                for row in rows:
                    row.product_id = int(from_map.get(int(row.id), decision.left_product_id))

            mode = str(restore_payload.get("mode") or "").strip().lower()
            if mode == "combine":
                created_product_id = int(restore_payload.get("created_product_id") or 0)
                if created_product_id > 0:
                    self.db.delete(decision)
                    self.db.flush()
                    decision_deleted = True
                    self.db.query(ParserProductOriginVariant).filter(
                        ParserProductOriginVariant.product_id == int(created_product_id)
                    ).delete(synchronize_session=False)
                    self.db.query(self.product_repo.model_class).filter(
                        self.product_repo.model_class.id == int(created_product_id)
                    ).delete(synchronize_session=False)

        if not decision_deleted:
            self.db.delete(decision)
        self.db.commit()
        return {"ok": True, "pair_key": pair_key_value}

    def combine_duplicate(self, payload: DedupCombineRequest) -> dict:
        if payload.product_a_id == payload.product_b_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        left, right = self._resolve_pair_or_404(payload.product_a_id, payload.product_b_id)
        left_before = self._snapshot_product(left)
        right_before = self._snapshot_product(right)
        key = pair_key(left.id, right.id)
        combined = self._build_created_product(left=left, right=right, pair_key_value=key)

        origin_rows = self._origin_rows_for_products({int(left.id), int(right.id)})
        moved_origins = [
            {
                "origin_id": int(row.id),
                "from_product_id": int(row.product_id),
                "to_product_id": int(combined.id),
            }
            for row in origin_rows
        ]
        for row in origin_rows:
            row.product_id = int(combined.id)
        self.db.flush()
        self._assign_variants_and_status_from_origins(combined)

        left.status = "unavailable"
        left.deleted_at = None
        right.status = "unavailable"
        right.deleted_at = None
        upsert_combine_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(left.id, right.id),
            right_product_id=max(left.id, right.id),
            merged_into_product_id=int(combined.id),
            snapshot_payload={
                "left_before": left_before if int(left.id) == min(left.id, right.id) else right_before,
                "right_before": right_before if int(right.id) == max(left.id, right.id) else left_before,
            },
            restore_payload={
                "mode": "combine",
                "created_product_id": int(combined.id),
                "moved_origins": moved_origins,
            },
        )

        self.db.commit()
        return {
            "ok": True,
            "mode": "combine",
            "merged_into_product_id": int(combined.id),
            "disabled_product_id": int(right.id),
            "disabled_product_ids": [int(left.id), int(right.id)],
            "merged_variants_count": len(combined.variants if isinstance(combined.variants, list) else []),
            "primary_variant_fingerprints": len(extract_variant_fingerprints(combined)),
        }
