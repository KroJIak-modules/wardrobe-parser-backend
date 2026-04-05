"""Repository for manual favorite products."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ParserFavoriteProduct
from app.repositories.base import BaseRepository


class ParserFavoriteProductRepository(BaseRepository[ParserFavoriteProduct]):
    def __init__(self, session: Session):
        super().__init__(session, ParserFavoriteProduct)

    def get_by_product_id(self, product_id: int) -> ParserFavoriteProduct | None:
        return self.query().filter(ParserFavoriteProduct.product_id == product_id).first()

    def get_product_id_set(self) -> set[int]:
        rows = self.query().all()
        return {int(item.product_id) for item in rows}

    def get_product_id_set_for_ids(self, product_ids: set[int]) -> set[int]:
        if not product_ids:
            return set()
        rows = self.query().filter(ParserFavoriteProduct.product_id.in_(product_ids)).all()
        return {int(item.product_id) for item in rows}
