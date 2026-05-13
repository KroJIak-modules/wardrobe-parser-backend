"""Service layer for dedup candidates and moderation actions."""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
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

    def _build_effective_prices(self, products: list[Any]) -> dict[int, float | None]:
        if not products:
            return {}

        settings_service = PricingSettingsService(self.db)
        pricing_settings = settings_service.get_settings(refresh_bybit=False)
        source_ids = {int(item.source_id) for item in products if getattr(item, "source_id", None) is not None}
        source_profile_map = {
            int(source.id): source
            for source in self.source_repo.get_active_by_ids(source_ids)
        }

        effective_prices: dict[int, float | None] = {}
        for product in products:
            source_profile = source_profile_map.get(int(product.source_id))
            source_price = self._normalize_source_price(product.price, product.currency)
            source_currency = str(product.currency or "").upper() or None
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
                variants=product.variants if isinstance(product.variants, list) else [],
                settings=pricing_settings,
            )
            if pricing.final_price_rub is not None:
                effective_prices[int(product.id)] = float(pricing.final_price_rub)
            else:
                effective_prices[int(product.id)] = source_price
        return effective_prices

    @staticmethod
    def _product_response_with_effective_price(product: Any, effective_price_rub: float | None) -> ProductResponse:
        payload = ProductResponse.model_validate(product)
        if effective_price_rub is not None:
            payload.price = float(round(effective_price_rub, 2))
            payload.currency = "RUB"
        return payload

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

    def get_candidates(self, limit: int = settings.dedup_candidates_default_limit) -> DedupCandidateListResponse:
        only_available = self._dedup_only_available_enabled()
        allowed_statuses = {"available"} if only_available else {"available", "out_of_stock"}
        products = [
            item
            for item in self.product_repo.filter_products(limit=settings.dedup_scan_limit)
            if str(getattr(item, "status", "") or "").lower() in allowed_statuses
        ]
        product_by_id = {int(item.id): item for item in products}
        if len(product_by_id) < 2:
            return DedupCandidateListResponse(items=[], total=0, limit=limit)

        effective_prices = self._build_effective_prices(products)
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
            bounded_ids = unique_ids[:80]
            for left_id, right_id in combinations(bounded_ids, 2):
                key = pair_key(left_id, right_id)
                if key in blocked_pair_keys:
                    continue
                candidate_pairs.add(key)

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
                left_price=effective_prices.get(int(left.id)),
                right_price=effective_prices.get(int(right.id)),
            )
            if score < settings.dedup_score_threshold:
                continue
            candidates.append(
                DedupCandidateResponse(
                    pair_key=raw_key,
                    score=score,
                    reasons=reasons,
                    left=self._product_response_with_effective_price(
                        left,
                        effective_prices.get(int(left.id)),
                    ),
                    right=self._product_response_with_effective_price(
                        right,
                        effective_prices.get(int(right.id)),
                    ),
                )
            )
            if len(candidates) >= limit:
                break

        candidates.sort(key=lambda item: item.score, reverse=True)
        sliced = candidates[:limit]
        return DedupCandidateListResponse(items=sliced, total=len(sliced), limit=limit)

    def merge_duplicate(self, payload: DedupMergeRequest) -> dict:
        if payload.primary_product_id == payload.duplicate_product_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        primary, duplicate = self._resolve_pair_or_404(payload.primary_product_id, payload.duplicate_product_id)

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

        duplicate.status = "unavailable"
        duplicate.deleted_at = None
        key = pair_key(primary.id, duplicate.id)
        upsert_merge_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(primary.id, duplicate.id),
            right_product_id=max(primary.id, duplicate.id),
            merged_into_product_id=primary.id,
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

    def get_decisions(self, limit: int = 200) -> DedupDecisionListResponse:
        decisions = self.decision_repo.list_recent(limit=limit)
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
                    left=ProductResponse.model_validate(left),
                    right=ProductResponse.model_validate(right),
                )
            )
        return DedupDecisionListResponse(items=items, total=len(items), limit=max(1, min(int(limit), 1000)))

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
        if action in {"merge", "combine"}:
            merged_into_id = int(decision.merged_into_product_id or 0)
            left_id = int(decision.left_product_id)
            right_id = int(decision.right_product_id)
            if merged_into_id not in {left_id, right_id}:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Невозможно определить отключенный товар")
            disabled_id = right_id if merged_into_id == left_id else left_id
            disabled_product = self.product_repo.get_by_id(disabled_id)
            if disabled_product is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отключенный товар не найден")
            disabled_product.status = self._derive_unavailable_fallback_status(
                disabled_product.variants if isinstance(disabled_product.variants, list) else []
            )
            disabled_product.deleted_at = None

        self.db.delete(decision)
        self.db.commit()
        return {"ok": True, "pair_key": pair_key_value}

    def combine_duplicate(self, payload: DedupCombineRequest) -> dict:
        if payload.product_a_id == payload.product_b_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        left, right = self._resolve_pair_or_404(payload.product_a_id, payload.product_b_id)
        primary, duplicate = self._choose_primary_for_combine(left, right)

        primary_variants = primary.variants if isinstance(primary.variants, list) else []
        duplicate_variants = duplicate.variants if isinstance(duplicate.variants, list) else []
        merged_variants = self._merge_variants(primary_variants, duplicate_variants)

        primary_images = primary.image_urls if isinstance(primary.image_urls, list) else []
        duplicate_images = duplicate.image_urls if isinstance(duplicate.image_urls, list) else []
        merged_images = self._unique_list([*primary_images, *duplicate_images])

        primary_image_ids = primary.image_asset_ids if isinstance(primary.image_asset_ids, list) else []
        duplicate_image_ids = duplicate.image_asset_ids if isinstance(duplicate.image_asset_ids, list) else []
        merged_image_ids = self._unique_list([*primary_image_ids, *duplicate_image_ids])

        if not primary.vendor and duplicate.vendor:
            primary.vendor = duplicate.vendor
        if not primary.product_type and duplicate.product_type:
            primary.product_type = duplicate.product_type

        primary.variants = merged_variants
        primary.image_urls = merged_images
        primary.image_asset_ids = merged_image_ids
        primary.image_count = max(int(primary.image_count or 0), len(merged_images), int(duplicate.image_count or 0))
        primary.price = self._resolve_price_from_variants(
            variants=merged_variants,
            fallback_price=self._safe_float(getattr(primary, "price", None)),
        )
        primary.status = self._resolve_status_from_variants(
            merged_variants,
            str(primary.status or duplicate.status or "out_of_stock"),
        )

        duplicate.status = "unavailable"
        duplicate.deleted_at = None
        key = pair_key(primary.id, duplicate.id)
        upsert_combine_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(primary.id, duplicate.id),
            right_product_id=max(primary.id, duplicate.id),
            merged_into_product_id=primary.id,
        )

        self.db.commit()
        return {
            "ok": True,
            "mode": "combine",
            "merged_into_product_id": primary.id,
            "disabled_product_id": duplicate.id,
            "merged_variants_count": len(merged_variants),
            "primary_variant_fingerprints": len(extract_variant_fingerprints(primary)),
        }
