"""Repository for parser products."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ParserProduct, ParserProductOriginVariant
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
        return self.list_distinct_vendors_with_min_products(min_products=1)

    def list_distinct_vendors_with_min_products(self, *, min_products: int) -> list[str]:
        safe_min = max(1, int(min_products))
        normalized_vendor = func.lower(func.trim(ParserProduct.vendor))
        rows = (
            self.query()
            .with_entities(func.min(func.trim(ParserProduct.vendor)).label("vendor"), func.count(ParserProduct.id).label("cnt"))
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.vendor.isnot(None))
            .filter(func.trim(ParserProduct.vendor) != "")
            .group_by(normalized_vendor)
            .having(func.count(ParserProduct.id) >= safe_min)
            .order_by(normalized_vendor.asc())
            .all()
        )
        result: list[str] = []
        for (raw_vendor, _) in rows:
            vendor = str(raw_vendor or "").strip()
            if vendor:
                result.append(vendor)
        return result

    def list_distinct_vendors_with_sources(self, *, min_products: int) -> list[tuple[str, list[int]]]:
        safe_min = max(1, int(min_products))
        normalized_vendor = func.lower(func.trim(ParserProduct.vendor))
        rows = (
            self.query()
            .with_entities(
                func.min(func.trim(ParserProduct.vendor)).label("vendor"),
                func.array_agg(func.distinct(ParserProductOriginVariant.source_id)).label("source_ids"),
                func.count(ParserProduct.id).label("cnt"),
            )
            .join(ParserProductOriginVariant, ParserProductOriginVariant.product_id == ParserProduct.id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.vendor.isnot(None))
            .filter(func.trim(ParserProduct.vendor) != "")
            .group_by(normalized_vendor)
            .having(func.count(ParserProduct.id) >= safe_min)
            .order_by(normalized_vendor.asc())
            .all()
        )
        result: list[tuple[str, list[int]]] = []
        for raw_vendor, raw_source_ids, _ in rows:
            vendor = str(raw_vendor or "").strip()
            if not vendor:
                continue
            source_ids = sorted({int(item) for item in (raw_source_ids or []) if item is not None})
            result.append((vendor, source_ids))
        return result

    def list_vendor_source_counts(self, *, min_products: int) -> list[tuple[str, int, int]]:
        safe_min = max(1, int(min_products))
        normalized_vendor = func.lower(func.trim(ParserProduct.vendor))
        vendor_totals_subq = (
            self.query()
            .with_entities(
                normalized_vendor.label("vendor_key"),
                func.count(ParserProduct.id).label("total_count"),
            )
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.vendor.isnot(None))
            .filter(func.trim(ParserProduct.vendor) != "")
            .group_by(normalized_vendor)
            .subquery()
        )
        rows = (
            self.query()
            .with_entities(
                func.min(func.trim(ParserProduct.vendor)).label("vendor"),
                ParserProductOriginVariant.source_id.label("source_id"),
                func.count(ParserProduct.id).label("source_count"),
                vendor_totals_subq.c.total_count.label("total_count"),
            )
            .join(ParserProductOriginVariant, ParserProductOriginVariant.product_id == ParserProduct.id)
            .join(vendor_totals_subq, vendor_totals_subq.c.vendor_key == normalized_vendor)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProduct.vendor.isnot(None))
            .filter(func.trim(ParserProduct.vendor) != "")
            .group_by(normalized_vendor, ParserProductOriginVariant.source_id, vendor_totals_subq.c.total_count)
            .having(vendor_totals_subq.c.total_count >= safe_min)
            .order_by(normalized_vendor.asc(), ParserProductOriginVariant.source_id.asc())
            .all()
        )
        result: list[tuple[str, int, int]] = []
        for raw_vendor, raw_source_id, raw_source_count, _ in rows:
            vendor = str(raw_vendor or "").strip()
            if not vendor or raw_source_id is None:
                continue
            result.append((vendor, int(raw_source_id), int(raw_source_count or 0)))
        return result
