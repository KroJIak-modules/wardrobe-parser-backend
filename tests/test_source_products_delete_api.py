from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.v1.auth as auth_module
from app.core.database import SessionLocal
from app.main import app
from app.models import Product, ProductListing, ProductListingMember, Source, SourceSetting
from app.services.catalog.dedup_service_v2 import DedupServiceV2


class DummyLimiter:
    def is_limited(self, _client_key: str) -> bool:
        return False

    def register_failed_attempt(self, _client_key: str) -> None:
        pass


def _authorized_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert response.status_code == 200
    return client


def _source(db, key: str) -> Source:
    source = Source(
        key=key,
        name=key,
        base_url=f"https://{key}",
        base_url_normalized=key,
        adapter_key="demo__v1",
        parser_config={"mode": "manual"},
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id)))
    db.flush()
    return source


def _listing(db, *, source_id: int, suffix: str) -> ProductListing:
    listing = ProductListing(
        source_id=source_id,
        url=f"https://example.test/{suffix}",
        url_normalized=f"example.test/{suffix}",
        host_normalized="example.test",
        source_title=suffix,
    )
    db.add(listing)
    db.flush()
    return listing


def test_delete_source_products_preserves_other_source_listing(monkeypatch) -> None:
    db = SessionLocal()
    client = _authorized_client(monkeypatch)
    marker = uuid4().hex[:10]
    selected = _source(db, f"selected-{marker}.test")
    other = _source(db, f"other-{marker}.test")
    try:
        selected_listing = _listing(db, source_id=int(selected.id), suffix="selected")
        other_listing = _listing(db, source_id=int(other.id), suffix="other")
        selected_listing_id = int(selected_listing.id)
        other_listing_id = int(other_listing.id)
        product = Product(
            gender="unisex",
            source_gender="unisex",
            availability_mode="by_order",
            lifecycle_status="active",
            visibility_status="visible",
        )
        db.add(product)
        db.flush()
        db.add_all(
            [
                ProductListingMember(product_id=int(product.id), listing_id=int(selected_listing.id)),
                ProductListingMember(product_id=int(product.id), listing_id=int(other_listing.id)),
            ]
        )
        db.flush()
        product.primary_listing_id = int(selected_listing.id)
        db.commit()

        response = client.delete(f"/api/v1/sources/{selected.key}/products")

        assert response.status_code == 200
        assert response.json()["deleted_listings"] == 1
        assert response.json()["deleted_products"] == 0
        db.expire_all()
        preserved = db.query(Product).filter(Product.id == int(product.id)).one()
        assert preserved.primary_listing_id == other_listing_id
        assert db.query(ProductListing).filter(ProductListing.id == selected_listing_id).one_or_none() is None
        assert db.query(ProductListing).filter(ProductListing.id == other_listing_id).one_or_none() is not None
        assert db.query(Source).filter(Source.id == int(selected.id)).one().parser_config == {"mode": "manual"}
    finally:
        db.query(ProductListingMember).filter(ProductListingMember.product_id == int(product.id)).delete(synchronize_session=False)
        db.query(Product).filter(Product.id == int(product.id)).delete(synchronize_session=False)
        db.query(ProductListing).filter(ProductListing.source_id.in_([int(selected.id), int(other.id)])).delete(synchronize_session=False)
        db.query(SourceSetting).filter(SourceSetting.source_id.in_([int(selected.id), int(other.id)])).delete(synchronize_session=False)
        db.query(Source).filter(Source.id.in_([int(selected.id), int(other.id)])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_delete_source_products_rebuilds_dedup_without_selected_source(monkeypatch) -> None:
    db = SessionLocal()
    client = _authorized_client(monkeypatch)
    marker = uuid4().hex[:10]
    selected = _source(db, f"dedup-selected-{marker}.test")
    other = _source(db, f"dedup-other-{marker}.test")
    try:
        selected_listing = _listing(db, source_id=int(selected.id), suffix="selected")
        other_listing = _listing(db, source_id=int(other.id), suffix="other")
        product_ids: list[int] = []
        for listing in (selected_listing, other_listing):
            product = Product(
                gender="unisex",
                source_gender="unisex",
                availability_mode="by_order",
                lifecycle_status="active",
                visibility_status="visible",
            )
            db.add(product)
            db.flush()
            db.add(ProductListingMember(product_id=int(product.id), listing_id=int(listing.id)))
            db.flush()
            product.primary_listing_id = int(listing.id)
            product_ids.append(int(product.id))
        db.flush()
        combined_id = DedupServiceV2(db).merge(product_ids=product_ids, merge_mode="combine")
        db.commit()

        response = client.delete(f"/api/v1/sources/{selected.key}/products")

        assert response.status_code == 200
        db.expire_all()
        assert db.query(Product).filter(Product.id == product_ids[0]).one_or_none() is None
        preserved = db.query(Product).filter(Product.id == product_ids[1]).one()
        assert preserved.dedup_target_product_id is None
        assert db.query(Product).filter(Product.id == combined_id).one_or_none() is None
        assert db.query(ProductListing).filter(ProductListing.id == int(other_listing.id)).one_or_none() is not None
    finally:
        db.rollback()
        db.close()


def test_delete_source_products_removes_source_only_product(monkeypatch) -> None:
    db = SessionLocal()
    client = _authorized_client(monkeypatch)
    marker = uuid4().hex[:10]
    source = _source(db, f"source-only-{marker}.test")
    try:
        listing = _listing(db, source_id=int(source.id), suffix="only")
        listing_id = int(listing.id)
        product = Product(
            gender="unisex",
            source_gender="unisex",
            availability_mode="by_order",
            lifecycle_status="active",
            visibility_status="visible",
        )
        db.add(product)
        db.flush()
        db.add(ProductListingMember(product_id=int(product.id), listing_id=int(listing.id)))
        db.flush()
        product.primary_listing_id = int(listing.id)
        product_id = int(product.id)
        db.commit()

        response = client.delete(f"/api/v1/sources/{source.key}/products")

        assert response.status_code == 200
        assert response.json()["deleted_listings"] == 1
        assert response.json()["deleted_products"] == 1
        db.expire_all()
        assert db.query(Product).filter(Product.id == product_id).one_or_none() is None
        assert db.query(ProductListing).filter(ProductListing.id == listing_id).one_or_none() is None
    finally:
        db.query(ProductListingMember).filter(ProductListingMember.product_id == int(product_id)).delete(synchronize_session=False)
        db.query(Product).filter(Product.id == int(product_id)).delete(synchronize_session=False)
        db.query(ProductListing).filter(ProductListing.source_id == int(source.id)).delete(synchronize_session=False)
        db.query(SourceSetting).filter(SourceSetting.source_id == int(source.id)).delete(synchronize_session=False)
        db.query(Source).filter(Source.id == int(source.id)).delete(synchronize_session=False)
        db.commit()
        db.close()
