from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Source, SourceSetting, Supplier, SupplierShippingRate


@dataclass(frozen=True, slots=True)
class CanonicalSupplierSeed:
    key: str
    name: str
    provider_kind: str
    parent_key: str | None
    rate_currency: str
    rates: tuple[tuple[float, float | None, float], ...]


_MANUAL_SOURCE_KEY = "manual.local"
_DEFAULT_SUPPLIER_KEY = "eu"
_CANONICAL_SUPPLIERS: tuple[CanonicalSupplierSeed, ...] = (
    CanonicalSupplierSeed(
        key="usa",
        name="США",
        provider_kind="main",
        parent_key=None,
        rate_currency="RUB",
        rates=((0.0, 0.5, 1400.0), (0.5, 1.0, 1650.0), (1.0, 1.5, 2250.0), (1.5, 2.0, 2900.0), (2.0, 2.5, 3500.0), (2.5, None, 4100.0)),
    ),
    CanonicalSupplierSeed(
        key="usa-alt-1",
        name="ALT 1 США",
        provider_kind="alternate",
        parent_key="usa",
        rate_currency="RUB",
        rates=((0.0, 0.5, 1700.0), (0.5, 1.0, 3350.0), (1.0, 1.5, 4100.0), (1.5, 2.0, 4950.0), (2.0, 2.5, 5650.0), (2.5, None, 6500.0)),
    ),
    CanonicalSupplierSeed(
        key="eu",
        name="ЕС",
        provider_kind="main",
        parent_key=None,
        rate_currency="RUB",
        rates=((0.0, 0.5, 1100.0), (0.5, 1.0, 1500.0), (1.0, 1.5, 1900.0), (1.5, 2.0, 2300.0), (2.0, 2.5, 2700.0), (2.5, None, 3150.0)),
    ),
    CanonicalSupplierSeed(
        key="eu-alt-1",
        name="ALT 1 ЕС",
        provider_kind="alternate",
        parent_key="eu",
        rate_currency="RUB",
        rates=((0.0, 0.5, 2300.0), (0.5, 1.0, 2750.0), (1.0, 1.5, 3750.0), (1.5, 2.0, 4800.0), (2.0, 2.5, 5800.0), (2.5, None, 6800.0)),
    ),
    CanonicalSupplierSeed(
        key="uk",
        name="Великобритания",
        provider_kind="main",
        parent_key=None,
        rate_currency="RUB",
        rates=((0.0, 0.5, 3400.0), (0.5, 1.0, 3900.0), (1.0, 1.5, 4400.0), (1.5, 2.0, 4900.0), (2.0, 2.5, 5450.0), (2.5, None, 5950.0)),
    ),
)


class CatalogDefaultsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure(self) -> None:
        suppliers = self.ensure_canonical_suppliers()
        self.ensure_default_source_suppliers(default_supplier=suppliers.get(_DEFAULT_SUPPLIER_KEY))

    def ensure_canonical_suppliers(self) -> dict[str, Supplier]:
        existing = {
            str(entity.key or "").strip().lower(): entity
            for entity in self.db.query(Supplier).all()
            if str(entity.key or "").strip()
        }

        for seed in _CANONICAL_SUPPLIERS:
            entity = existing.get(seed.key)
            if entity is None:
                entity = Supplier(
                    key=seed.key,
                    name=seed.name,
                    provider_kind=seed.provider_kind,
                    rate_currency=seed.rate_currency,
                    is_enabled=True,
                )
                self.db.add(entity)
                self.db.flush()
                existing[seed.key] = entity
            else:
                if not str(entity.name or "").strip():
                    entity.name = seed.name
                if not str(entity.provider_kind or "").strip():
                    entity.provider_kind = seed.provider_kind
                if not str(entity.rate_currency or "").strip():
                    entity.rate_currency = seed.rate_currency
                if getattr(entity, "is_enabled", None) is None:
                    entity.is_enabled = True

        for seed in _CANONICAL_SUPPLIERS:
            entity = existing[seed.key]
            if seed.parent_key is not None:
                parent = existing.get(seed.parent_key)
                if parent is not None and entity.parent_supplier_id is None:
                    entity.parent_supplier_id = int(parent.id)
            elif entity.parent_supplier_id is None:
                entity.parent_supplier_id = None

            if not list(entity.shipping_rates or []):
                for min_kg, max_kg, rub in seed.rates:
                    self.db.add(
                        SupplierShippingRate(
                            supplier_id=int(entity.id),
                            min_weight_kg=float(min_kg),
                            max_weight_kg=(float(max_kg) if max_kg is not None else None),
                            price_rub=float(rub),
                        )
                    )

        self.db.flush()
        return existing

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
