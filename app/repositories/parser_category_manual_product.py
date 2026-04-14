"""Repository for manual category-product assignments."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserCategoryManualProduct
from app.repositories.base import BaseRepository


class ParserCategoryManualProductRepository(BaseRepository[ParserCategoryManualProduct]):
    def __init__(self, session: Session):
        super().__init__(session, ParserCategoryManualProduct)

    def get_exact(self, category_id: int, product_id: int) -> ParserCategoryManualProduct | None:
        return (
            self.query()
            .filter(ParserCategoryManualProduct.category_id == category_id)
            .filter(ParserCategoryManualProduct.product_id == product_id)
            .first()
        )

    def get_by_category(self, category_id: int) -> list[ParserCategoryManualProduct]:
        return (
            self.query()
            .filter(ParserCategoryManualProduct.category_id == category_id)
            .order_by(ParserCategoryManualProduct.id.asc())
            .all()
        )

    def get_grouped_by_product_ids(self, product_ids: set[int]) -> dict[int, list[int]]:
        if not product_ids:
            return {}
        rows = (
            self.query()
            .filter(ParserCategoryManualProduct.product_id.in_(list(product_ids)))
            .order_by(ParserCategoryManualProduct.id.asc())
            .all()
        )
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(int(row.product_id), []).append(int(row.category_id))
        return result
