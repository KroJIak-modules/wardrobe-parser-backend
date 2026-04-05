"""Service layer for dedup candidates and moderation actions."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories import ParserDedupDecisionRepository, ParserProductRepository
from app.schemas.parser import (
    DedupCandidateListResponse,
    DedupCandidateResponse,
    DedupMergeRequest,
    DedupRejectRequest,
    ProductResponse,
)
from app.services.moderation.dedup_decision import upsert_merge_decision, upsert_reject_decision
from app.services.moderation.dedup_scoring import candidate_score, normalize_title, normalize_vendor, pair_key


class DedupService:
    """Encapsulates duplicate detection and moderation business rules."""

    def __init__(self, db: Session):
        self.db = db
        self.product_repo = ParserProductRepository(db)
        self.decision_repo = ParserDedupDecisionRepository(db)

    def get_candidates(self, limit: int = settings.dedup_candidates_default_limit) -> DedupCandidateListResponse:
        products = self.product_repo.filter_products(limit=settings.dedup_scan_limit)
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
                score, reasons = candidate_score(left, right)
                if score < settings.dedup_score_threshold:
                    continue
                candidates.append(
                    DedupCandidateResponse(
                        pair_key=key,
                        score=score,
                        reasons=reasons,
                        left=ProductResponse.model_validate(left),
                        right=ProductResponse.model_validate(right),
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
