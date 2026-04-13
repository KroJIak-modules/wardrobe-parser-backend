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

    def get_default_supplier(self) -> ParserSupplier:
        supplier = self.get_by_key("default")
        if supplier:
            return supplier
        supplier = self.create(
            id=1,
            key="default",
            name="Default Supplier",
            category="main",
        )
        self.flush()
        return supplier

    def list_all_with_rates(self) -> list[ParserSupplier]:
        return (
            self.query()
            .options(joinedload(ParserSupplier.shipping_rates))
            .order_by(ParserSupplier.id.asc())
            .all()
        )

    def ensure_linear_rates(self, *, supplier_id: int, per_500g_rub: float, max_step_500g: int) -> None:
        rates = (
            self.session.query(ParserSupplierShippingRate)
            .filter(ParserSupplierShippingRate.supplier_id == supplier_id)
            .all()
        )
        by_step = {item.step_500g: item for item in rates}
        target_steps = max(1, int(max_step_500g))

        for step in range(1, target_steps + 1):
            value = float(step) * float(per_500g_rub)
            existing = by_step.get(step)
            if existing:
                existing.rate_rub = value
            else:
                self.session.add(
                    ParserSupplierShippingRate(
                        supplier_id=supplier_id,
                        step_500g=step,
                        rate_rub=value,
                    )
                )
        for step, item in by_step.items():
            if step > target_steps:
                self.session.delete(item)
