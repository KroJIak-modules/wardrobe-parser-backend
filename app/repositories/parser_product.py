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
