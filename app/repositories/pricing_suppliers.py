"""Repositories for supplier entities used in pricing formula."""

from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from app.models import ParserSupplier, ParserSupplierShippingRate
from app.repositories.base import BaseRepository


class ParserSupplierRepository(BaseRepository[ParserSupplier]):
    def __init__(self, session: Session):
        super().__init__(session, ParserSupplier)

    def get_by_key(self, key: str) -> ParserSupplier | None:
        return self.query().filter(ParserSupplier.key == key).first()

    def list_all_with_rates(self) -> list[ParserSupplier]:
        return (
            self.query()
            .options(joinedload(ParserSupplier.shipping_rates))
            .order_by(
                ParserSupplier.parent_supplier_id.asc().nullsfirst(),
                ParserSupplier.alt_position.asc(),
                ParserSupplier.id.asc(),
            )
            .all()
        )

    def replace_ranges(self, *, supplier_id: int, ranges: list[dict]) -> None:
        self.session.query(ParserSupplierShippingRate).filter(
            ParserSupplierShippingRate.supplier_id == supplier_id
        ).delete(synchronize_session=False)
        for row in ranges:
            self.session.add(
                ParserSupplierShippingRate(
                    supplier_id=supplier_id,
                    min_kg=float(row["min_kg"]),
                    max_kg=(float(row["max_kg"]) if row.get("max_kg") is not None else None),
                    rate_rub=float(row["rub"]),
                )
            )
