from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Source, SourceSetting, Supplier
from app.services.catalog.catalog_defaults_service import CatalogDefaultsService


def test_catalog_defaults_service_seeds_canonical_suppliers_and_assigns_default_source_supplier() -> None:
    db = SessionLocal()
    source_key = f"defaults-{uuid4().hex[:12]}.example"
    try:
        source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            host_normalized=source_key,
        )
        db.add(source)
        db.flush()
        setting = SourceSetting(source_id=int(source.id), supplier_id=None)
        db.add(setting)
        db.flush()

        CatalogDefaultsService(db).ensure()
        db.flush()

        eu_supplier = db.query(Supplier).filter(Supplier.key == "eu").one()
        uk_supplier = db.query(Supplier).filter(Supplier.key == "uk").one()
        db.refresh(setting)

        assert int(setting.supplier_id or 0) == int(eu_supplier.id)
        assert len(list(eu_supplier.shipping_rates or [])) > 0
        assert len(list(uk_supplier.shipping_rates or [])) > 0
    finally:
        db.rollback()
        db.close()


def test_catalog_defaults_service_does_not_assign_supplier_to_manual_source() -> None:
    db = SessionLocal()
    try:
        source = db.query(Source).filter(Source.key == "manual.local").one_or_none()
        if source is None:
            source = Source(
                key="manual.local",
                name="Manual",
                base_url="manual://catalog",
                base_url_normalized="manual.local",
                host_normalized="manual.local",
            )
            db.add(source)
            db.flush()
        setting = db.query(SourceSetting).filter(SourceSetting.source_id == int(source.id)).one_or_none()
        if setting is None:
            setting = SourceSetting(source_id=int(source.id), supplier_id=None)
            db.add(setting)
            db.flush()
        setting.supplier_id = None

        CatalogDefaultsService(db).ensure()
        db.refresh(setting)

        assert setting.supplier_id is None
    finally:
        db.rollback()
        db.close()
