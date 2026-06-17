from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ProductDedupDecision, ProductDedupDecisionMember


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

    def add_members(self, *, decision_id: int, product_ids: list[int]) -> None:
        unique_product_ids = sorted({int(product_id) for product_id in product_ids})
        for product_id in unique_product_ids:
            self.session.add(ProductDedupDecisionMember(decision_id=int(decision_id), product_id=product_id))
        self.session.flush()

    def list_decisions(self, *, limit: int, offset: int) -> list[ProductDedupDecision]:
        return (
            self.session.query(ProductDedupDecision)
            .order_by(ProductDedupDecision.id.desc())
            .offset(max(0, int(offset)))
            .limit(max(1, int(limit)))
            .all()
        )
