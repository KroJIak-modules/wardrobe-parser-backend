from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import Designer, DesignerSourceName, Product, ProductListing, ProductListingMember, Source
from app.services.catalog.admin_editor_service import AdminEditorService
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.product_write_service import ProductWriteService


def _create_source(db, *, key: str) -> Source:
    source = Source(
        key=key,
        name=key.replace("-", " ").title(),
        base_url=f"https://{key}.example.com",
        base_url_normalized=f"https://{key}.example.com",
    )
    db.add(source)
    db.flush()
    return source


def _create_sync_product(db, *, source: Source, brand: str, suffix: str, status: str = "orderable") -> tuple[Product, ProductListing]:
    product = Product(
        gender="unisex",
        availability_mode="by_order",
        lifecycle_status="active",
        visibility_status="visible",
    )
    db.add(product)
    db.flush()

    listing = ProductListing(
        source_id=int(source.id),
        url=f"https://{source.key}.example.com/products/{suffix}",
        url_normalized=f"https://{source.key}.example.com/products/{suffix}",
        host_normalized=f"{source.key}.example.com",
        handle=f"product-{suffix}",
        source_title=f"Product {suffix}",
        source_designer_raw=brand,
        orderability_status=status,
        ingest_mode="sync",
    )
    db.add(listing)
    db.flush()

    db.add(ProductListingMember(product_id=int(product.id), listing_id=int(listing.id)))
    db.flush()
    product.primary_listing_id = int(listing.id)
    db.flush()
    return product, listing


class DummyLimiter:
    def __init__(self) -> None:
        self.failed: dict[str, int] = {}

    def is_limited(self, client_key: str) -> bool:
        return self.failed.get(client_key, 0) >= 2

    def register_failed_attempt(self, client_key: str) -> None:
        self.failed[client_key] = self.failed.get(client_key, 0) + 1


def _authorized_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert login.status_code == 200
    return client


def test_active_source_brand_creates_catalog_designer_automatically() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-alpha")
        brand = "ZZ TEST 1017 ALYX 9SM"
        _create_sync_product(db, source=source, brand=brand, suffix="alyx")
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        result = AdminEditorService(db).list_designer_editor_state()

        row = next(item for item in result["rows"] if item["source_brand"] == brand)
        assert row["include_in_designers"] is True
        assert row["designer_name"] == brand
        assert row["source_product_count"] == 1
        assert row["source_unavailable_product_count"] == 0
        assert row["source_public_product_count"] == 1

        persisted_mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        persisted_designer = db.query(Designer).filter(Designer.name == brand).one()

        assert persisted_mapping.is_enabled is True
        assert persisted_mapping.is_admin_touched is False
        assert persisted_mapping.designer_name == brand
        assert int(persisted_mapping.designer_id or 0) == int(persisted_designer.id)
        assert persisted_designer.origin_kind == "auto"
        assert persisted_designer.is_admin_touched is False
    finally:
        db.rollback()
        db.close()


def test_designer_editor_counts_unavailable_products_separately_from_public_products() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-counts")
        brand = "ZZ TEST Availability Counts"
        _create_sync_product(db, source=source, brand=brand, suffix="available")
        _create_sync_product(db, source=source, brand=brand, suffix="sold-out", status="sold_out")
        _create_sync_product(db, source=source, brand=brand, suffix="unavailable", status="unavailable")

        state = AdminEditorService(db).list_designer_editor_state()
        row = next(item for item in state["rows"] if item["source_brand"] == brand)

        assert row["source_product_count"] == 3
        assert row["source_unavailable_product_count"] == 2
        assert row["source_public_product_count"] == 1
    finally:
        db.rollback()
        db.close()


def test_untouched_designer_auto_disables_when_all_products_are_not_orderable_and_reenables() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-auto-availability")
        brand = "ZZ TEST Automatic Availability"
        _, listing = _create_sync_product(db, source=source, brand=brand, suffix="availability")
        sync = DesignerCatalogSyncService(db)

        sync.reconcile(sync_product_links=True)
        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        assert mapping.is_enabled is True

        listing.orderability_status = "sold_out"
        sync.reconcile(sync_product_links=True)
        assert mapping.is_enabled is False

        listing.orderability_status = "unavailable"
        sync.reconcile(sync_product_links=True)
        assert mapping.is_enabled is False

        listing.orderability_status = "orderable"
        sync.reconcile(sync_product_links=True)
        assert mapping.is_enabled is True
    finally:
        db.rollback()
        db.close()


def test_untouched_source_brand_is_disabled_when_all_products_become_unavailable() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-beta")
        brand = "ZZ TEST JULIUS"
        product, listing = _create_sync_product(db, source=source, brand=brand, suffix="julius")

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        assert db.query(Designer).filter(Designer.name == brand).count() == 1
        assert int(db.query(Product).filter(Product.id == int(product.id)).one().designer_id or 0) > 0

        listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        assert mapping.is_enabled is False
        assert db.query(Designer).filter(Designer.name == brand).count() == 1
        assert db.query(Product).filter(Product.id == int(product.id)).one().designer_id is None
    finally:
        db.rollback()
        db.close()


def test_source_enabled_patch_updates_case_variant_mappings(monkeypatch) -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-case-variants")
        _create_sync_product(db, source=source, brand="ZZ TEST Case Brand", suffix="case-variant")
        first = DesignerSourceName(
            source_name="ZZ TEST Case Brand",
            designer_name="ZZ TEST Case Brand",
            is_enabled=False,
            is_admin_touched=True,
        )
        second = DesignerSourceName(
            source_name="ZZ TEST CASE BRAND",
            designer_name="ZZ TEST CASE BRAND",
            is_enabled=True,
            is_admin_touched=False,
        )
        db.add_all([first, second])
        db.flush()

        monkeypatch.setattr(DesignerCatalogSyncService, "reconcile", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected reconcile")))
        result = AdminEditorService(db).set_designer_source_enabled(
            source_brand="ZZ TEST Case Brand",
            include_in_designers=True,
        )

        assert result == {"source_brand": "ZZ TEST Case Brand", "include_in_designers": True}
        assert first.is_enabled is True
        assert second.is_enabled is True
        assert first.is_admin_touched is True
        assert second.is_admin_touched is True
    finally:
        db.rollback()
        db.close()


def test_source_enabled_patch_updates_only_the_target_mapping(monkeypatch) -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-fast-toggle")
        brand = "ZZ TEST Fast Toggle"
        _create_sync_product(db, source=source, brand=brand, suffix="fast-toggle")
        sync = DesignerCatalogSyncService(db)
        sync.reconcile(sync_product_links=True)

        monkeypatch.setattr(DesignerCatalogSyncService, "reconcile", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected reconcile")))
        result = AdminEditorService(db).set_designer_source_enabled(source_brand=brand, include_in_designers=False)

        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        assert result == {"source_brand": brand, "include_in_designers": False}
        assert mapping.is_enabled is False
        assert mapping.is_admin_touched is True
    finally:
        db.rollback()
        db.close()


def test_disabled_source_brand_is_excluded_from_public_designer_directory() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-disabled-directory")
        brand = "ZZ TEST Disabled Directory"
        _create_sync_product(db, source=source, brand=brand, suffix="disabled-directory")

        service = AdminEditorService(db)
        state = service.list_designer_editor_state()
        row = next(item for item in state["rows"] if item["source_brand"] == brand)
        row["include_in_designers"] = False
        service.save_designer_editor_state({"rows": state["rows"], "designers": state["designers"]})

        directory = service.list_taxonomy_editor_state()["designer_directory"]
        assert all(item["label"] != brand for item in directory)
    finally:
        db.rollback()
        db.close()


def test_source_brand_stays_after_admin_toggles_it() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-gamma")
        brand = "ZZ TEST Rick Owens DRKSHDW"
        _, listing = _create_sync_product(db, source=source, brand=brand, suffix="drkshdw")

        service = AdminEditorService(db)
        state = service.list_designer_editor_state()
        row = next(item for item in state["rows"] if item["source_brand"] == brand)
        row["include_in_designers"] = False

        service.save_designer_editor_state({"rows": state["rows"], "designers": state["designers"]})
        listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        designer = db.query(Designer).filter(Designer.name == brand).one()
        assert mapping.is_admin_touched is True
        assert mapping.is_enabled is False
        assert designer.origin_kind == "auto"
        assert designer.is_admin_touched is False

        editor_state = AdminEditorService(db).list_designer_editor_state()
        row = next(item for item in editor_state["rows"] if item["source_brand"] == brand)
        assert row["source_product_count"] == 1
        assert row["source_unavailable_product_count"] == 1
        assert row["source_public_product_count"] == 0
        assert any(item["name"] == brand for item in editor_state["designers"])
    finally:
        db.rollback()
        db.close()


def test_source_brand_stays_after_admin_changes_description() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-delta")
        brand = "ZZ TEST 424"
        _, listing = _create_sync_product(db, source=source, brand=brand, suffix="424")
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        service = AdminEditorService(db)
        state = service.list_designer_editor_state()
        designer = next(item for item in state["designers"] if item["name"] == brand)
        designer["description"] = "Los Angeles streetwear label"

        service.save_designer_editor_state({"rows": state["rows"], "designers": state["designers"]})
        listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        persisted_designer = db.query(Designer).filter(Designer.name == brand).one()
        assert persisted_designer.description == "Los Angeles streetwear label"
        assert persisted_designer.is_admin_touched is True
        assert db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).count() == 1

        editor_state = AdminEditorService(db).list_designer_editor_state()
        row = next(item for item in editor_state["rows"] if item["source_brand"] == brand)
        assert row["source_product_count"] == 1
        assert row["source_unavailable_product_count"] == 1
        assert row["source_public_product_count"] == 0
        assert any(item["name"] == brand for item in editor_state["designers"])
    finally:
        db.rollback()
        db.close()


def test_product_brand_override_survives_reconcile_and_creates_designer_if_missing() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-override")
        raw_brand = "ZZ TEST Raw Brand"
        override_brand = "ZZ TEST Manual Override"
        product, listing = _create_sync_product(db, source=source, brand=raw_brand, suffix="override")

        ProductWriteService(db).update_product(
            product_id=int(product.id),
            payload={"brand_override_name": override_brand},
        )

        db.refresh(product)
        db.refresh(listing)
        assert product.presentation is not None
        assert product.presentation.brand_override_name == override_brand
        assert db.query(Designer).filter(Designer.name == override_brand).count() == 1

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        db.refresh(product)
        assert product.presentation is not None
        assert product.presentation.brand_override_name == override_brand
        assert db.query(Designer).filter(Designer.name == override_brand).count() == 1
    finally:
        db.rollback()
        db.close()


def test_manual_designer_stays_without_source_brands() -> None:
    db = SessionLocal()
    try:
        designer_name = "ZZ TEST Manual Maison Margiela"
        service = AdminEditorService(db)
        result = service.save_designer_editor_state(
            {
                "rows": [],
                "designers": [
                    {
                        "id": "designer-manual",
                        "name": designer_name,
                        "description": "Manual catalog designer",
                    }
                ],
            }
        )

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        persisted_designer = db.query(Designer).filter(Designer.name == designer_name).one()
        assert persisted_designer.origin_kind == "manual"
        assert persisted_designer.is_admin_touched is True
        assert any(item["name"] == designer_name for item in result["designers"])
    finally:
        db.rollback()
        db.close()


def test_admin_designer_editor_endpoint_returns_200(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    response = client.get("/api/v1/admin/designers/editor")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("rows"), list)
    assert isinstance(payload.get("designers"), list)


def test_multiple_source_brands_can_share_one_designer_and_unused_auto_designer_is_removed() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-zeta")
        main_brand = "ZZ TEST 424"
        collab_brand = "ZZ TEST 424 x Hoorsenbuhs"
        _create_sync_product(db, source=source, brand=main_brand, suffix="424-main")
        _create_sync_product(db, source=source, brand=collab_brand, suffix="424-collab")

        service = AdminEditorService(db)
        state = service.list_designer_editor_state()
        for row in state["rows"]:
            if row["source_brand"] == collab_brand:
                row["designer_name"] = main_brand

        service.save_designer_editor_state({"rows": state["rows"], "designers": state["designers"]})

        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == collab_brand).one()
        shared_designer = db.query(Designer).filter(Designer.name == main_brand).one()
        assert int(mapping.designer_id or 0) == int(shared_designer.id)
        assert mapping.is_admin_touched is True
        assert db.query(Designer).filter(Designer.name == collab_brand).count() == 0

        collab_listing = (
            db.query(ProductListing)
            .filter(ProductListing.source_id == int(source.id))
            .filter(ProductListing.source_designer_raw == collab_brand)
            .one()
        )
        collab_listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        assert db.query(Designer).filter(Designer.name == main_brand).count() == 1
        assert db.query(DesignerSourceName).filter(DesignerSourceName.source_name == collab_brand).count() == 1
    finally:
        db.rollback()
        db.close()


def test_untouched_source_brand_reenables_after_an_orderable_product_returns() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-theta")
        brand = "ZZ TEST Visvim"
        _, listing = _create_sync_product(db, source=source, brand=brand, suffix="visvim")

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        designer = db.query(Designer).filter(Designer.name == brand).one()
        assert mapping.is_enabled is False
        assert designer.origin_kind == "auto"

        listing.orderability_status = "orderable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        assert mapping.is_enabled is True
        assert mapping.is_admin_touched is False
    finally:
        db.rollback()
        db.close()
