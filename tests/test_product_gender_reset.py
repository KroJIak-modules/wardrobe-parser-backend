from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_write_service import ProductWriteService


def test_product_gender_reset_restores_source_gender_and_clears_manual_flag() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = Source(
            key=f"gender-reset-{marker}.example",
            name=f"Gender Reset {marker}",
            base_url=f"https://gender-reset-{marker}.example",
            base_url_normalized=f"gender-reset-{marker}.example",
        )
        db.add(source)
        db.flush()
        db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        ProductIngestService(db).apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": f"https://gender-reset-{marker}.example/products/test-item",
                    "handle": "test-item",
                    "title": "Gender Reset Product",
                    "description": "",
                    "designer": "Gender Reset Designer",
                    "category": "Belts",
                    "tags": [],
                    "gender": "male",
                    "source_weight_grams": 400,
                    "orderability_status": "orderable",
                    "variants": [{"title": "UNI", "price": 100.0, "currency": "USD", "available": True}],
                    "images": [],
                }
            ],
        )
        db.flush()

        listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id), ProductListing.handle == "test-item").one()
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()

        assert product.gender == "male"
        assert product.source_gender == "male"
        assert product.gender_is_manual is False

        ProductWriteService(db).update_product(
            product_id=int(product.id),
            payload={"gender": "female"},
        )
        db.flush()
        db.refresh(product)

        assert product.gender == "female"
        assert product.source_gender == "male"
        assert product.gender_is_manual is True

        ProductWriteService(db).update_product(
            product_id=int(product.id),
            payload={"reset_to_default": ["gender"]},
        )
        db.flush()
        db.refresh(product)

        assert product.gender == "male"
        assert product.source_gender == "male"
        assert product.gender_is_manual is False
    finally:
        db.rollback()
        db.close()
