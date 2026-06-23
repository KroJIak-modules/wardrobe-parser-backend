from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService


def test_product_ingest_service_preserves_existing_optional_source_snapshot_fields_when_sync_payload_is_blank() -> None:
    db = SessionLocal()
    source_key = f"ingest-{uuid4().hex[:12]}.example"
    product_url = f"https://{source_key}/products/test-item"
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
        db.add(SourceSetting(source_id=int(source.id)))
        db.flush()

        service = ProductIngestService(db)
        service.apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": product_url,
                    "handle": "test-item",
                    "title": "Test Designer Jacket",
                    "description": "Initial description",
                    "designer": "Test Designer",
                    "category": "Outerwear",
                    "gender": "male",
                    "source_weight_grams": 720,
                    "orderability_status": "orderable",
                    "variants": [{"title": "Default", "price": 120.0, "currency": "USD", "available": True}],
                    "images": ["https://cdn.example.com/test-item.jpg"],
                }
            ],
        )
        db.flush()

        listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id)).one()
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()

        assert listing.source_designer_raw == "Test Designer"
        assert listing.source_category_raw == "Outerwear"
        assert int(listing.source_weight_grams or 0) == 720

        service.apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": product_url,
                    "handle": "test-item",
                    "title": "Test Designer Jacket",
                    "description": "Updated description",
                    "designer": "",
                    "category": "",
                    "gender": "male",
                    "source_weight_grams": 0,
                    "orderability_status": "orderable",
                    "variants": [{"title": "Default", "price": 130.0, "currency": "USD", "available": True}],
                    "images": ["https://cdn.example.com/test-item.jpg"],
                }
            ],
        )
        db.flush()
        db.refresh(listing)
        db.refresh(product)

        assert listing.source_designer_raw == "Test Designer"
        assert listing.source_category_raw == "Outerwear"
        assert int(listing.source_weight_grams or 0) == 720
        assert listing.orderability_status == "orderable"
        assert listing.status_reason is None
    finally:
        db.rollback()
        db.close()


def test_product_ingest_service_matches_weight_rule_from_handle() -> None:
    db = SessionLocal()
    source_key = f"ingest-{uuid4().hex[:12]}.example"
    product_url = f"https://{source_key}/products/test-item"
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
        db.add(SourceSetting(source_id=int(source.id)))
        db.flush()

        service = ProductIngestService(db)
        service.apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": product_url,
                    "handle": "charm-garnet-piercing",
                    "title": "Garnet Piercing",
                    "description": "",
                    "designer": "Test Designer",
                    "category": "",
                    "gender": "unisex",
                    "source_weight_grams": 0,
                    "orderability_status": "orderable",
                    "variants": [{"title": "Default", "price": 120.0, "currency": "USD", "available": True}],
                    "images": ["https://cdn.example.com/test-item.jpg"],
                }
            ],
        )
        db.flush()

        listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id)).one()
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()

        assert product.weight_rule_id is not None
        assert listing.orderability_status == "orderable"
        assert listing.status_reason is None
    finally:
        db.rollback()
        db.close()


def test_product_ingest_service_accepts_service_variant_shape() -> None:
    db = SessionLocal()
    source_key = f"ingest-{uuid4().hex[:12]}.example"
    product_url = f"https://{source_key}/products/test-item"
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
        db.add(SourceSetting(source_id=int(source.id)))
        db.flush()

        service = ProductIngestService(db)
        service.apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": product_url,
                    "handle": "test-item",
                    "title": "Service Variant Shape",
                    "description": "",
                    "designer": "Test Designer",
                    "category": "",
                    "gender": "unisex",
                    "source_weight_grams": 500,
                    "orderability_status": "orderable",
                    "variants": [
                        {
                            "source_ref": {"id": "var-1", "sku": "SKU-1"},
                            "title": "UNI",
                            "price_amount": "600.00",
                            "compare_at_price_amount": "700.00",
                            "currency_code": "EUR",
                            "available": False,
                        }
                    ],
                    "images": [],
                }
            ],
        )
        db.flush()

        listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id)).one()
        variant = listing.variants[0]

        assert str(variant.price_amount) == "600.00"
        assert str(variant.compare_at_price_amount) == "700.00"
        assert variant.currency_code == "EUR"
        assert variant.is_orderable is False
    finally:
        db.rollback()
        db.close()
