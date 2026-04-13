"""Service layer for dedup candidates and moderation actions."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories import ParserDedupDecisionRepository, ParserProductRepository, ParserSourceRepository
from app.schemas.parser import (
    DedupCandidateListResponse,
    DedupCandidateResponse,
    DedupMergeRequest,
    DedupRejectRequest,
    ProductResponse,
)
from app.services.moderation.dedup_decision import upsert_merge_decision, upsert_reject_decision
from app.services.moderation.dedup_scoring import candidate_score, normalize_title, normalize_vendor, pair_key
from app.services.settings.pricing_service import PricingSettingsService


class DedupService:
    """Encapsulates duplicate detection and moderation business rules."""

    def __init__(self, db: Session):
        self.db = db
        self.product_repo = ParserProductRepository(db)
        self.decision_repo = ParserDedupDecisionRepository(db)
        self.source_repo = ParserSourceRepository(db)

    @staticmethod
    def _normalize_legacy_source_price(price: float | None, currency: str | None) -> float | None:
        if price is None:
            return None
        normalized_currency = (currency or "").upper()
        normalized_price = float(price)
        # Legacy guard: historical bug could persist non-RUB prices in cents (e.g. 43140 instead of 431.40).
        if (
            normalized_currency in {"USD", "EUR", "GBP"}
            and normalized_price >= 10_000
            and normalized_price.is_integer()
        ):
            return normalized_price / 100.0
        return normalized_price

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
            source_price = self._normalize_legacy_source_price(product.price, product.currency)
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

    def get_candidates(self, limit: int = settings.dedup_candidates_default_limit) -> DedupCandidateListResponse:
        products = self.product_repo.filter_products(limit=settings.dedup_scan_limit)
        effective_prices = self._build_effective_prices(products)
        buckets: dict[tuple[str, str], list] = {}
        for product in products:
            key = (normalize_title(product.title), normalize_vendor(product.vendor))
            buckets.setdefault(key, []).append(product)

        candidates: list[DedupCandidateResponse] = []
        for bucket_items in buckets.values():
            if len(bucket_items) < 2:
                continue
            for left, right in combinations(bucket_items, 2):
                key = pair_key(left.id, right.id)
                if self.decision_repo.get_by_pair_key(key):
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
                        pair_key=key,
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
                    return DedupCandidateListResponse(items=candidates, total=len(candidates), limit=limit)
        return DedupCandidateListResponse(items=candidates, total=len(candidates), limit=limit)

    def merge_duplicate(self, payload: DedupMergeRequest) -> dict:
        if payload.primary_product_id == payload.duplicate_product_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        primary = self.product_repo.get_by_id(payload.primary_product_id)
        duplicate = self.product_repo.get_by_id(payload.duplicate_product_id)
        if not primary or primary.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Primary product не найден")
        if not duplicate or duplicate.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Duplicate product не найден")

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

        duplicate.deleted_at = datetime.now(timezone.utc)
        key = pair_key(primary.id, duplicate.id)
        upsert_merge_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(primary.id, duplicate.id),
            right_product_id=max(primary.id, duplicate.id),
            merged_into_product_id=primary.id,
        )

        self.db.commit()
        return {"ok": True, "merged_into_product_id": primary.id, "removed_product_id": duplicate.id}

    def reject_duplicate(self, payload: DedupRejectRequest) -> dict:
        if payload.product_a_id == payload.product_b_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="IDs должны отличаться")

        left = self.product_repo.get_by_id(payload.product_a_id)
        right = self.product_repo.get_by_id(payload.product_b_id)
        if not left or left.deleted_at is not None or not right or right.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Одна из карточек не найдена")

        key = pair_key(left.id, right.id)
        upsert_reject_decision(
            self.decision_repo,
            pair_key_value=key,
            left_product_id=min(left.id, right.id),
            right_product_id=max(left.id, right.id),
        )
        self.db.commit()
        return {"ok": True, "pair_key": key}
