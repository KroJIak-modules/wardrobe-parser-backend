from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete as sa_delete, func, or_
from sqlalchemy.orm import Session, aliased, joinedload

from app.models import Product, ProductListing, ProductDedupCandidate, ProductDedupDecision, ProductDedupDecisionMember, SourceSetting


class CatalogDedupRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_decision(self, *, decision_kind: str, created_product_id: int | None) -> ProductDedupDecision:
        entity = ProductDedupDecision(
            decision_kind=str(decision_kind),
            created_product_id=(int(created_product_id) if created_product_id is not None else None),
        )
        self.session.add(entity)
        self.session.flush()
        return entity

    def _eligible_candidates(self):
        from app.services.catalog.public_product_policy import effective_public_orderability_expression

        left = aliased(Product)
        right = aliased(Product)
        left_listing = aliased(ProductListing)
        right_listing = aliased(ProductListing)
        left_setting = aliased(SourceSetting)
        right_setting = aliased(SourceSetting)
        return (
            self.session.query(ProductDedupCandidate)
            .join(left, left.id == ProductDedupCandidate.left_product_id)
            .join(right, right.id == ProductDedupCandidate.right_product_id)
            .join(left_listing, left_listing.id == left.primary_listing_id)
            .join(right_listing, right_listing.id == right.primary_listing_id)
            .outerjoin(left_setting, left_setting.source_id == left_listing.source_id)
            .outerjoin(right_setting, right_setting.source_id == right_listing.source_id)
            .filter(effective_public_orderability_expression(left, left_listing, left_setting) == "orderable")
            .filter(effective_public_orderability_expression(right, right_listing, right_setting) == "orderable")
            .filter(left.lifecycle_status == "active", right.lifecycle_status == "active")
            .filter(left.visibility_status == "visible", right.visibility_status == "visible")
            .filter(left.dedup_status == "independent", right.dedup_status == "independent")
            .filter(left_listing.orderability_status == "orderable", right_listing.orderability_status == "orderable")
        )

    def list_candidates(self, *, limit: int, offset: int) -> list[ProductDedupCandidate]:
        return (
            self._eligible_candidates()
            .order_by(
                ProductDedupCandidate.score.desc(),
                ProductDedupCandidate.right_product_id.desc(),
                ProductDedupCandidate.left_product_id.desc(),
                ProductDedupCandidate.id.desc(),
            )
            .offset(max(0, int(offset)))
            .limit(max(1, int(limit)))
            .all()
        )

    def count_candidates(self) -> int:
        return int(self._eligible_candidates().count())

    def replace_candidates(self, *, candidates: list[dict]) -> None:
        self.session.execute(sa_delete(ProductDedupCandidate))
        if candidates:
            self.session.bulk_insert_mappings(ProductDedupCandidate, candidates)
        self.session.flush()

    def get_candidate_by_pair(self, *, product_ids: Iterable[int]) -> ProductDedupCandidate | None:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if len(normalized_ids) != 2:
            return None
        return (
            self.session.query(ProductDedupCandidate)
            .filter(ProductDedupCandidate.left_product_id == int(normalized_ids[0]))
            .filter(ProductDedupCandidate.right_product_id == int(normalized_ids[1]))
            .one_or_none()
        )

    def restore_candidate(self, *, left_product_id: int, right_product_id: int, score: float, reasons: list[str]) -> None:
        left_id, right_id = sorted((int(left_product_id), int(right_product_id)))
        if left_id <= 0 or right_id <= 0 or left_id == right_id:
            return
        existing = (
            self.session.query(ProductDedupCandidate)
            .filter(ProductDedupCandidate.left_product_id == left_id)
            .filter(ProductDedupCandidate.right_product_id == right_id)
            .one_or_none()
        )
        if existing is not None:
            existing.score = float(score)
            existing.reasons = list(reasons or [])
            self.session.flush()
            return
        self.session.add(
            ProductDedupCandidate(
                left_product_id=left_id,
                right_product_id=right_id,
                score=float(score),
                reasons=list(reasons or []),
            )
        )
        self.session.flush()

    def delete_candidate_by_pair(self, *, product_ids: Iterable[int]) -> None:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if len(normalized_ids) != 2:
            return
        (
            self.session.query(ProductDedupCandidate)
            .filter(ProductDedupCandidate.left_product_id == int(normalized_ids[0]))
            .filter(ProductDedupCandidate.right_product_id == int(normalized_ids[1]))
            .delete(synchronize_session=False)
        )
        self.session.flush()

    def candidates_for_product_ids(self, *, product_ids: Iterable[int]) -> list[ProductDedupCandidate]:
        ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not ids:
            return []
        return self.session.query(ProductDedupCandidate).filter(
            or_(ProductDedupCandidate.left_product_id.in_(ids), ProductDedupCandidate.right_product_id.in_(ids))
        ).all()

    def delete_candidates_for_product_ids(self, *, product_ids: Iterable[int]) -> None:
        normalized_ids = sorted({int(product_id) for product_id in product_ids if int(product_id) > 0})
        if not normalized_ids:
            return
        (
            self.session.query(ProductDedupCandidate)
            .filter(
                or_(
                    ProductDedupCandidate.left_product_id.in_(normalized_ids),
                    ProductDedupCandidate.right_product_id.in_(normalized_ids),
                )
            )
            .delete(synchronize_session=False)
        )
        self.session.flush()

    def add_members(self, *, decision_id: int, product_ids: list[int]) -> None:
        unique_product_ids = sorted({int(product_id) for product_id in product_ids})
        for product_id in unique_product_ids:
            self.session.add(ProductDedupDecisionMember(decision_id=int(decision_id), product_id=product_id))
        self.session.flush()

    def list_decisions(self, *, limit: int, offset: int) -> list[ProductDedupDecision]:
        return (
            self.session.query(ProductDedupDecision)
            .options(joinedload(ProductDedupDecision.members))
            .order_by(ProductDedupDecision.id.desc())
            .offset(max(0, int(offset)))
            .limit(max(1, int(limit)))
            .all()
        )

    def count_decisions(self) -> int:
        return int(self.session.query(ProductDedupDecision.id).count())

    def get_dedup_fingerprint(self) -> tuple[int, int | None]:
        count, max_id = self.session.query(
            func.count(ProductDedupDecision.id),
            func.max(ProductDedupDecision.id),
        ).one()
        return int(count or 0), (int(max_id) if max_id is not None else None)

    def get_decision(self, decision_id: int) -> ProductDedupDecision | None:
        return (
            self.session.query(ProductDedupDecision)
            .options(joinedload(ProductDedupDecision.members))
            .filter(ProductDedupDecision.id == int(decision_id))
            .one_or_none()
        )

    def has_dependent_decisions(self, *, created_product_id: int, exclude_decision_id: int) -> bool:
        return (
            self.session.query(ProductDedupDecisionMember)
            .filter(ProductDedupDecisionMember.product_id == int(created_product_id))
            .filter(ProductDedupDecisionMember.decision_id != int(exclude_decision_id))
            .first()
            is not None
        )

    def delete_decision(self, decision: ProductDedupDecision) -> None:
        self.session.delete(decision)
        self.session.flush()
