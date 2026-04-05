"""Repository for parser products."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserProduct
from app.repositories.base import BaseRepository


class ParserProductRepository(BaseRepository[ParserProduct]):
    def __init__(self, session: Session):
        super().__init__(session, ParserProduct)

    def filter_products(self, *, limit: int) -> list[ParserProduct]:
        return (
            self.query()
            .filter(ParserProduct.deleted_at.is_(None))
            .order_by(ParserProduct.updated_at.desc(), ParserProduct.id.desc())
            .limit(limit)
            .all()
        )

    def get_active_by_id(self, product_id: int) -> ParserProduct | None:
        return (
            self.query()
            .filter(ParserProduct.id == product_id)
            .filter(ParserProduct.deleted_at.is_(None))
            .first()
        )

    def list_active_for_category_counts(self) -> list[ParserProduct]:
        return (
            self.query()
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.status == "available")
            .all()
        )
