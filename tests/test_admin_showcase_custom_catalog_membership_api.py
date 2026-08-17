from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import CustomCatalog, CustomCatalogProduct, Product, ProductListing, ProductListingMember, Source, SourceSetting
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


def _create_product(db, marker: str) -> tuple[Product, Source]:
    source = Source(
        key=f"showcase-catalog-membership-{marker}.example",
        name=f"Showcase catalog membership {marker}",
        base_url=f"https://showcase-catalog-membership-{marker}.example",
        base_url_normalized=f"showcase-catalog-membership-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True, show_images=True, description_mode="text"))
    ProductIngestService(db).apply_batch(
        source_id=int(source.id),
        items=[
            {
                "url": f"https://showcase-catalog-membership-{marker}.example/products/item",
                "handle": f"showcase-catalog-membership-{marker}",
                "title": "Showcase catalog membership",
                "description": "",
                "designer": "Showcase test designer",
                "category": "Outerwear",
                "tags": [],
                "gender": "unisex",
                "source_weight_grams": 500,
                "orderability_status": "orderable",
                "variants": [{"title": "UNI", "price": 120.0, "currency": "USD", "available": True}],
                "images": [],
            }
        ],
    )
    db.flush()
    listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id)).one()
    product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()
    return product, source


def test_showcase_custom_catalog_membership_is_targeted_and_idempotent(monkeypatch) -> None:
    client = _authorized_client(monkeypatch)
    db = SessionLocal()
    marker = uuid4().hex[:12]
    product_id: int | None = None
    source_id: int | None = None
    listing_id: int | None = None
    catalog_ids: list[int] = []
    try:
        product, source = _create_product(db, marker)
        product_id = int(product.id)
        source_id = int(source.id)
        listing_id = int(product.primary_listing_id or 0)
        first = CustomCatalog(title="First catalog", slug=f"first-{marker}", is_enabled=True)
        second = CustomCatalog(title="Second catalog", slug=f"second-{marker}", is_enabled=True)
        db.add_all([first, second])
        db.flush()
        catalog_ids = [int(first.id), int(second.id)]
        db.add(CustomCatalogProduct(catalog_id=int(second.id), product_id=product_id))
        db.commit()

        listed = client.get(f"/api/v1/admin/showcase/products/{product_id}/custom-catalogs")
        assert listed.status_code == 200
        assert listed.json()["items"] == [
            {"slug": f"first-{marker}", "label": "First catalog", "is_assigned": False},
            {"slug": f"second-{marker}", "label": "Second catalog", "is_assigned": True},
        ]

        assigned = client.patch(
            f"/api/v1/admin/showcase/products/{product_id}/custom-catalogs/first-{marker}",
            json={"is_assigned": True},
        )
        assert assigned.status_code == 200
        assert assigned.json() == {"slug": f"first-{marker}", "is_assigned": True}

        db.expire_all()
        memberships = db.query(CustomCatalogProduct.catalog_id).filter(CustomCatalogProduct.product_id == product_id).all()
        assert {int(row.catalog_id) for row in memberships} == set(catalog_ids)

        removed = client.patch(
            f"/api/v1/admin/showcase/products/{product_id}/custom-catalogs/first-{marker}",
            json={"is_assigned": False},
        )
        assert removed.status_code == 200
        db.expire_all()
        memberships = db.query(CustomCatalogProduct.catalog_id).filter(CustomCatalogProduct.product_id == product_id).all()
        assert [int(row.catalog_id) for row in memberships] == [int(second.id)]

        missing = client.patch(
            f"/api/v1/admin/showcase/products/{product_id}/custom-catalogs/missing-{marker}",
            json={"is_assigned": True},
        )
        assert missing.status_code == 400
    finally:
        if product_id is not None:
            db.query(CustomCatalogProduct).filter(CustomCatalogProduct.product_id == product_id).delete(synchronize_session=False)
        if catalog_ids:
            db.query(CustomCatalog).filter(CustomCatalog.id.in_(catalog_ids)).delete(synchronize_session=False)
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
