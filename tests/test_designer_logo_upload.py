from __future__ import annotations

import base64
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api.v1.admin_editors as admin_editors_module
import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import Designer, DesignerSourceName, ImageAsset, Product, ProductListing, ProductListingMember, Source
from app.services.catalog.admin_editor_service import AdminEditorService
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.media_asset_service import MediaAssetService


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


def _create_source(db, *, key: str) -> Source:
    source = Source(
        key=key,
        name=key.replace("-", " ").title(),
        base_url=f"https://{key}.example.com",
        base_url_normalized=f"https://{key}.example.com",
        host_normalized=f"{key}.example.com",
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


def _create_image_asset(db, *, checksum: str) -> ImageAsset:
    asset = ImageAsset(
        storage_key=f"designers/{checksum}.png",
        mime_type="image/png",
        byte_size=128,
        checksum_sha256=checksum,
    )
    db.add(asset)
    db.flush()
    return asset


def test_admin_designers_logo_upload_endpoint_returns_asset_id(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)

    monkeypatch.setattr(
        admin_editors_module.MediaAssetService,
        "save_upload",
        lambda self, *, scope, upload: SimpleNamespace(id=987),
    )

    response = client.post(
        "/api/v1/admin/designers/logo/upload",
        files={"file": ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml")},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "image_asset_id": 987}


def test_media_asset_service_detects_svg_mime_type(monkeypatch, tmp_path) -> None:
    db = SessionLocal()
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")
    try:
        asset = MediaAssetService(db).save_bytes(
            scope="designers",
            file_name="logo.svg",
            content=b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'></svg>",
        )

        assert asset.mime_type == "image/svg+xml"
        assert asset.width_px is None
        assert asset.height_px is None
        assert MediaAssetService(db).resolve_file_path(asset).exists()
    finally:
        db.rollback()
        db.close()


def test_designer_editor_round_trips_logo_asset_id(monkeypatch, tmp_path) -> None:
    db = SessionLocal()
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")
    try:
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO3Zf8sAAAAASUVORK5CYII="
        )
        asset = MediaAssetService(db).save_bytes(scope="designers", file_name="logo.png", content=png_bytes)

        service = AdminEditorService(db)
        result = service.save_designer_editor_state(
            {
                "rows": [],
                "designers": [
                    {
                        "id": "designer-studio-c",
                        "name": "Studio C",
                        "description": "Brand logo",
                        "logo_image_asset_id": int(asset.id),
                    }
                ],
            }
        )

        designer = next(item for item in result["designers"] if item["name"] == "Studio C")
        assert designer["logo_image_asset_id"] == int(asset.id)

        persisted = db.query(Designer).filter(Designer.name == "Studio C").one()
        assert int(persisted.logo_image_asset_id or 0) == int(asset.id)
    finally:
        db.rollback()
        db.close()


def test_designer_editor_ignores_blank_draft_designer(monkeypatch, tmp_path) -> None:
    db = SessionLocal()
    monkeypatch.setattr(MediaAssetService, "_ROOT_DIR", tmp_path / "uploads")
    try:
        result = AdminEditorService(db).save_designer_editor_state(
            {
                "rows": [],
                "designers": [
                    {
                        "id": "designer-draft",
                        "name": "",
                        "description": "",
                        "logo_image_asset_id": None,
                    }
                ],
            }
        )

        assert all(designer["id"] != "designer-draft" for designer in result["designers"])
    finally:
        db.rollback()
        db.close()


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


def test_untouched_source_brand_is_removed_when_all_products_become_unavailable() -> None:
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

        assert db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).count() == 0
        assert db.query(Designer).filter(Designer.name == brand).count() == 0
        assert db.query(Product).filter(Product.id == int(product.id)).one().designer_id is None
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
    finally:
        db.rollback()
        db.close()


def test_source_brand_stays_after_admin_uploads_logo() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-epsilon")
        brand = "ZZ TEST Acronym"
        _, listing = _create_sync_product(db, source=source, brand=brand, suffix="acronym")
        asset = _create_image_asset(db, checksum="a" * 64)
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        service = AdminEditorService(db)
        state = service.list_designer_editor_state()
        designer = next(item for item in state["designers"] if item["name"] == brand)
        designer["logo_image_asset_id"] = int(asset.id)

        service.save_designer_editor_state({"rows": state["rows"], "designers": state["designers"]})
        listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        persisted_designer = db.query(Designer).filter(Designer.name == brand).one()
        assert int(persisted_designer.logo_image_asset_id or 0) == int(asset.id)
        assert persisted_designer.is_admin_touched is True
        assert db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).count() == 1
    finally:
        db.rollback()
        db.close()


def test_manual_designer_stays_without_source_brands() -> None:
    db = SessionLocal()
    try:
        service = AdminEditorService(db)
        result = service.save_designer_editor_state(
            {
                "rows": [],
                "designers": [
                    {
                        "id": "designer-manual",
                        "name": "Maison Margiela",
                        "description": "Manual catalog designer",
                        "logo_image_asset_id": None,
                    }
                ],
            }
        )

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        persisted_designer = db.query(Designer).filter(Designer.name == "Maison Margiela").one()
        assert persisted_designer.origin_kind == "manual"
        assert persisted_designer.is_admin_touched is True
        assert any(item["name"] == "Maison Margiela" for item in result["designers"])
    finally:
        db.rollback()
        db.close()


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


def test_source_brand_is_recreated_after_untouched_brand_disappears_and_returns() -> None:
    db = SessionLocal()
    try:
        source = _create_source(db, key="source-theta")
        brand = "ZZ TEST Visvim"
        _, listing = _create_sync_product(db, source=source, brand=brand, suffix="visvim")

        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
        listing.orderability_status = "unavailable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        assert db.query(Designer).filter(Designer.name == brand).count() == 0
        assert db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).count() == 0

        listing.orderability_status = "orderable"
        DesignerCatalogSyncService(db).reconcile(sync_product_links=True)

        recreated_mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        recreated_designer = db.query(Designer).filter(Designer.name == brand).one()
        assert recreated_mapping.is_admin_touched is False
        assert recreated_designer.origin_kind == "auto"
    finally:
        db.rollback()
        db.close()
