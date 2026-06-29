from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Source, SourceSetting, Supplier, SupplierShippingRate
from app.services.settings.default_admin_settings import DefaultAdminSettingsLoader


_MANUAL_SOURCE_KEY = "manual.local"


class CatalogDefaultsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure(self) -> None:
        seed = DefaultAdminSettingsLoader.load()
        suppliers, seeded = self.ensure_canonical_suppliers()
        if seeded:
            self.ensure_default_source_suppliers(default_supplier=suppliers.get(seed.default_source_supplier_key))

    def ensure_canonical_suppliers(self) -> tuple[dict[str, Supplier], bool]:
        seed = DefaultAdminSettingsLoader.load()
        existing = {
            str(entity.key or "").strip().lower(): entity
            for entity in self.db.query(Supplier).all()
            if str(entity.key or "").strip()
        }
        if existing:
            return existing, False

        for item in seed.suppliers:
            entity = existing.get(item.key)
            if entity is None:
                entity = Supplier(
                    key=item.key,
                    name=item.name,
                    provider_kind=item.provider_kind,
                    rate_currency=item.rate_currency,
                    is_enabled=bool(item.is_enabled),
                )
                self.db.add(entity)
                self.db.flush()
                existing[item.key] = entity

        for item in seed.suppliers:
            entity = existing[item.key]
            parent = existing.get(item.parent_supplier_key) if item.parent_supplier_key else None
            entity.parent_supplier_id = int(parent.id) if parent is not None else None
            for rate in item.rates:
                self.db.add(
                    SupplierShippingRate(
                        supplier_id=int(entity.id),
                        min_weight_kg=float(rate.min_kg),
                        max_weight_kg=(float(rate.max_kg) if rate.max_kg is not None else None),
                        price_rub=float(rate.rub),
                    )
                )

        self.db.flush()
        return existing, True

    def ensure_default_source_suppliers(
        self,
        *,
        default_supplier: Supplier | None,
        source_ids: set[int] | None = None,
    ) -> int:
        if default_supplier is None:
            return 0

        query = (
            self.db.query(SourceSetting)
            .join(Source, Source.id == SourceSetting.source_id)
            .filter(Source.key != _MANUAL_SOURCE_KEY)
            .filter(SourceSetting.supplier_id.is_(None))
        )
        if source_ids:
            query = query.filter(SourceSetting.source_id.in_(sorted(int(source_id) for source_id in source_ids)))

        updated = 0
        for setting in query.all():
            setting.supplier_id = int(default_supplier.id)
            updated += 1
        if updated:
            self.db.flush()
        return updated
