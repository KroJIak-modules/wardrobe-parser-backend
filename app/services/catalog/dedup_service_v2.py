from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.models import ProductPresentation
from app.repositories.catalog_dedup import CatalogDedupRepository
from app.repositories.catalog_products import CatalogProductRepository


class DedupServiceV2:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.decisions = CatalogDedupRepository(db)

    @staticmethod
    def _norm(value: str | None) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _candidate_score(self, left: dict, right: dict) -> tuple[float, list[str]]:
        left_title = self._norm(left.get("title"))
        right_title = self._norm(right.get("title"))
        ratio = SequenceMatcher(None, left_title, right_title).ratio()
        reasons: list[str] = []
        if ratio >= 0.9:
            reasons.append("same_title")
        elif ratio >= 0.75:
            reasons.append("close_title")
        left_vendor = self._norm(left.get("designer_name"))
        right_vendor = self._norm(right.get("designer_name"))
        if left_vendor and left_vendor == right_vendor:
            reasons.append("same_designer")
            ratio += 0.1
        if left.get("effective_weight_grams") and right.get("effective_weight_grams") and left["effective_weight_grams"] == right["effective_weight_grams"]:
            reasons.append("same_weight")
            ratio += 0.05
        return min(ratio, 0.99), reasons

    def list_candidates(self, *, limit: int, offset: int, payload_builder) -> dict:
        products = self.products.list_products(limit=500, offset=0)
        payloads = [payload_builder(product) for product in products if payload_builder(product).get("visibility_status") != "hidden"]
        decided_pair_keys = self._decided_pair_keys()
        candidates: list[dict] = []
        for index, left in enumerate(payloads):
            for right in payloads[index + 1 :]:
                score, reasons = self._candidate_score(left, right)
                if score < 0.75:
                    continue
                pair_key = f"{min(int(left['id']), int(right['id']))}:{max(int(left['id']), int(right['id']))}"
                if pair_key in decided_pair_keys:
                    continue
                candidates.append(
                    {
                        "pair_key": pair_key,
                        "score": round(score, 4),
                        "reasons": reasons,
                        "left": left,
                        "right": right,
                    }
                )
        candidates.sort(key=lambda item: (-float(item["score"]), item["pair_key"]))
        sliced = candidates[max(0, int(offset)) : max(0, int(offset)) + max(1, int(limit))]
        return {"items": sliced, "total": len(candidates), "limit": int(limit), "offset": int(offset)}

    def reject(self, *, product_ids: list[int]) -> None:
        decision = self.decisions.create_decision(decision_kind="reject", created_product_id=None)
        self.decisions.add_members(decision_id=int(decision.id), product_ids=product_ids)

    def merge(self, *, primary_product_id: int, duplicate_product_id: int, primary_listing_id: int | None = None) -> int:
        primary = self.products.get_product(primary_product_id)
        duplicate = self.products.get_product(duplicate_product_id)
        if primary is None or duplicate is None:
            raise ValidationError("Один из товаров не найден")
        if int(primary.id) == int(duplicate.id):
            raise ValidationError("Нельзя merge товар сам с собой")

        created = self.products.create_product(
            designer_id=primary.designer_id,
            gender=primary.gender,
            availability_mode=primary.availability_mode,
            manual_weight_grams=primary.manual_weight_grams,
            weight_rule_id=primary.weight_rule_id,
            lifecycle_status="active",
            visibility_status=primary.visibility_status,
        )
        primary_members = self.products.list_product_listings(int(primary.id))
        duplicate_members = self.products.list_product_listings(int(duplicate.id))
        merged_members = [*primary_members, *duplicate_members]
        for listing in merged_members:
            self.products.ensure_membership(product_id=int(created.id), listing_id=int(listing.id))
            for row in self.products.list_gallery_scope(product_id=int(primary.id), listing_id=int(listing.id)):
                row.product_id = int(created.id)
            for row in self.products.list_gallery_scope(product_id=int(duplicate.id), listing_id=int(listing.id)):
                row.product_id = int(created.id)

        allowed_primary_listing_ids = {int(listing.id) for listing in merged_members}
        if primary_listing_id is not None:
            if int(primary_listing_id) not in allowed_primary_listing_ids:
                raise ValidationError("primary_listing_id не входит в merged union")
            created.primary_listing_id = int(primary_listing_id)
        else:
            created.primary_listing_id = primary.primary_listing_id or (primary_members[0].id if primary_members else None)
        if primary.presentation is not None:
            self.db.add(
                ProductPresentation(
                    product_id=int(created.id),
                    title_override=primary.presentation.title_override,
                    description_text=primary.presentation.description_text,
                    description_html=primary.presentation.description_html,
                    description_visibility=primary.presentation.description_visibility,
                )
            )
        if primary.price_override is not None:
            self.products.upsert_price_override(
                product_id=int(created.id),
                manual_price_rub=float(primary.price_override.manual_price_rub),
                manual_compare_at_price_rub=(
                    float(primary.price_override.manual_compare_at_price_rub)
                    if primary.price_override.manual_compare_at_price_rub is not None
                    else None
                ),
            )

        primary.lifecycle_status = "merged"
        duplicate.lifecycle_status = "merged"
        decision = self.decisions.create_decision(decision_kind="merge", created_product_id=int(created.id))
        self.decisions.add_members(decision_id=int(decision.id), product_ids=[int(primary.id), int(duplicate.id)])
        self.db.flush()
        return int(created.id)
    def _decided_pair_keys(self) -> set[str]:
        decided: set[str] = set()
        for decision in self.decisions.list_decisions(limit=10_000, offset=0):
            member_ids = sorted({int(member.product_id) for member in getattr(decision, "members", [])})
            if len(member_ids) < 2:
                continue
            decided.add(f"{member_ids[0]}:{member_ids[1]}")
        return decided
