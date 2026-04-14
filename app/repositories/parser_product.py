"""Repository for parser products."""

from __future__ import annotations

from sqlalchemy import func
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

    def list_distinct_vendors(self) -> list[str]:
        normalized_vendor = func.lower(func.trim(ParserProduct.vendor))
        rows = (
            self.query()
            .with_entities(func.min(func.trim(ParserProduct.vendor)).label("vendor"))
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.vendor.isnot(None))
            .group_by(normalized_vendor)
            .order_by(normalized_vendor.asc())
            .all()
        )
        result: list[str] = []
        for (raw_vendor,) in rows:
            vendor = str(raw_vendor or "").strip()
            if vendor:
                result.append(vendor)
        return result
