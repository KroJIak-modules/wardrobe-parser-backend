from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import Product, ProductListing, ProductListingMember, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService


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


def _create_source(db, marker: str) -> Source:
    source = Source(
        key=f"product-patch-{marker}.example",
        name=f"Product Patch {marker}",
        base_url=f"https://product-patch-{marker}.example",
        base_url_normalized=f"product-patch-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True, show_images=True, description_mode="text"))
    db.flush()
    return source


def _ingest_unavailable_product(db, *, source_id: int, marker: str, orderability_status: str = "unavailable") -> Product:
    handle = f"unavailable-{marker}"
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://product-patch-{marker}.example/products/{handle}",
                "handle": handle,
                "title": "Patch Visibility Test",
                "description": "",
                "designer": "Patch Visibility Designer",
                "category": "Outerwear",
                "tags": ["patch-visibility"],
                "gender": "unisex",
                "source_weight_grams": 500,
                "orderability_status": orderability_status,
                "status_reason": "source_removed" if orderability_status == "unavailable" else None,
                "variants": [{"title": "UNI", "price": 120.0, "currency": "USD", "available": orderability_status == "orderable"}],
                "images": [],
            }
        ],
    )
    db.flush()
    listing = db.query(ProductListing).filter(ProductListing.source_id == int(source_id), ProductListing.handle == handle).one()
    return db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()


def test_source_visibility_patch_preserves_manual_product_hidden_state(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    product_id: int | None = None
    listing_id: int | None = None
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_unavailable_product(db, source_id=source_id, marker=marker, orderability_status="orderable")
        product_id = int(product.id)
        listing_id = int(product.primary_listing_id or 0)
        db.commit()

        hide_source = client.patch(
            f"/api/v1/sources/{source.key}/hide-auto-added-products",
            json={"hide_auto_added_products": True},
        )
        assert hide_source.status_code == 200
        db.expire_all()
        assert db.query(Product).filter(Product.id == product_id).one().visibility_status == "hidden"

        restore_source = client.patch(
            f"/api/v1/sources/{source.key}/hide-auto-added-products",
            json={"hide_auto_added_products": False},
        )
        assert restore_source.status_code == 200
        db.expire_all()
        assert db.query(Product).filter(Product.id == product_id).one().visibility_status == "visible"

        manual_hide = client.patch(f"/api/v1/products/{product_id}", json={"visibility_status": "hidden"})
        assert manual_hide.status_code == 200
        assert client.patch(
            f"/api/v1/sources/{source.key}/hide-auto-added-products",
            json={"hide_auto_added_products": True},
        ).status_code == 200
        assert client.patch(
            f"/api/v1/sources/{source.key}/hide-auto-added-products",
            json={"hide_auto_added_products": False},
        ).status_code == 200
        db.expire_all()
        assert db.query(Product).filter(Product.id == product_id).one().visibility_status == "hidden"

        manual_show = client.patch(f"/api/v1/products/{product_id}", json={"visibility_status": "visible"})
        assert manual_show.status_code == 200
        db.expire_all()
        assert db.query(Product).filter(Product.id == product_id).one().visibility_status == "visible"
    finally:
        if product_id is not None:
            db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
            db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        if listing_id is not None:
            db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_patch_hidden_unavailable_product_returns_admin_payload(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    marker = uuid4().hex[:12]
    source_id: int | None = None
    product_id: int | None = None
    listing_id: int | None = None
    try:
        source = _create_source(db, marker)
        source_id = int(source.id)
        product = _ingest_unavailable_product(db, source_id=source_id, marker=marker)
        product_id = int(product.id)
        listing_id = int(product.primary_listing_id or 0)
        db.commit()

        response = client.patch(
            f"/api/v1/products/{product_id}",
            json={"visibility_status": "hidden"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == product_id
        assert payload["visibility_status"] == "hidden"
        assert payload["orderability_status"] == "unavailable"
        assert payload["status_reason"] == "source_removed"

        db.expire_all()
        persisted = db.query(Product).filter(Product.id == product_id).one()
        assert persisted.visibility_status == "hidden"

        admin_get_response = client.get(f"/api/v1/admin/products/{product_id}")
        assert admin_get_response.status_code == 200
        assert admin_get_response.json()["id"] == product_id
        assert admin_get_response.json()["orderability_status"] == "unavailable"

        public_get_response = client.get(f"/api/v1/products/{product_id}")
        assert public_get_response.status_code == 404
    finally:
        if product_id is not None:
            db.query(ProductListingMember).filter(ProductListingMember.product_id == product_id).delete(synchronize_session=False)
            db.query(Product).filter(Product.id == product_id).delete(synchronize_session=False)
        if listing_id is not None:
            db.query(ProductListing).filter(ProductListing.id == listing_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()
