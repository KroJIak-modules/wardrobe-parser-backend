from types import SimpleNamespace

from app.core.database import SessionLocal
from app.models import Product, ProductListing, ProductListingMember, Source, SourceSetting
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.source_gender_policy import (
    effective_gender_for_product,
    source_gender_overrides,
)


def test_source_override_is_effective_without_mutating_product_value() -> None:
    product = SimpleNamespace(
        gender="unisex",
        primary_listing=SimpleNamespace(source=SimpleNamespace(key="misssixty.com")),
    )

    assert source_gender_overrides()["misssixty.com"] == "female"
    assert effective_gender_for_product(product) == "female"
    assert product.gender == "unisex"


def test_source_without_override_keeps_stored_gender() -> None:
    product = SimpleNamespace(
        gender="male",
        primary_listing=SimpleNamespace(source=SimpleNamespace(key="example.test")),
    )

    assert effective_gender_for_product(product) == "male"
    assert product.gender == "male"


def test_effective_gender_filter_uses_config_without_mutating_stored_product() -> None:
    db = SessionLocal()
    try:
        source = Source(
            key="misssixty.com",
            name="Miss Sixty",
            base_url="https://misssixty.com/",
            base_url_normalized="misssixty.com",
        )
        db.add(source)
        db.flush()
        db.add(SourceSetting(source_id=int(source.id)))
        listing = ProductListing(
            source_id=int(source.id),
            url="https://misssixty.com/products/test-effective-gender",
            url_normalized="https://misssixty.com/products/test-effective-gender",
            host_normalized="misssixty.com",
            source_title="Test",
            orderability_status="orderable",
            ingest_mode="sync",
        )
        db.add(listing)
        db.flush()
        product = Product(
            gender="unisex",
            source_gender="unisex",
            lifecycle_status="active",
            visibility_status="visible",
        )
        db.add(product)
        db.flush()
        db.add(ProductListingMember(product_id=int(product.id), listing_id=int(listing.id)))
        db.flush()
        product.primary_listing_id = int(listing.id)
        db.flush()

        female_ids = {
            int(row[0])
            for row in db.query(
                ProductQueryService(db).filtered_product_ids_subquery(gender="female").c.product_id
            ).all()
        }
        male_ids = {
            int(row[0])
            for row in db.query(
                ProductQueryService(db).filtered_product_ids_subquery(gender="male").c.product_id
            ).all()
        }

        assert int(product.id) in female_ids
        assert int(product.id) not in male_ids
        assert product.gender == "unisex"
        assert product.source_gender == "unisex"
    finally:
        db.rollback()
        db.close()
