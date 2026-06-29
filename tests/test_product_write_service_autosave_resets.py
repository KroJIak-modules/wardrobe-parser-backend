from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.core.exceptions import ValidationError
from app.models import Product, ProductListing, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_write_service import ProductWriteService


def test_product_write_service_handles_editor_fields_and_resets() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = Source(
            key=f"autosave-reset-{marker}.example",
            name=f"Autosave Reset {marker}",
            base_url=f"https://autosave-reset-{marker}.example",
            base_url_normalized=f"autosave-reset-{marker}.example",
        )
        db.add(source)
        db.flush()
        db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        ProductIngestService(db).apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": f"https://autosave-reset-{marker}.example/products/test-item",
                    "handle": "test-item",
                    "title": "Autosave Reset Product",
                    "description": "Source description",
                    "designer": "Autosave Designer",
                    "category": "Belts",
                    "tags": [],
                    "gender": "male",
                    "source_weight_grams": 410,
                    "orderability_status": "orderable",
                    "variants": [{"title": "UNI", "price": 100.0, "currency": "USD", "available": True}],
                    "images": [],
                }
            ],
        )
        db.flush()

        listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id), ProductListing.handle == "test-item").one()
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()

        ProductWriteService(db).update_product(
            product_id=int(product.id),
            payload={
                "title_override": "Display title",
                "brand_override_name": "Override Designer",
                "description_text": "Text override",
                "description_html": "<p>HTML override</p>",
                "description_visibility": False,
                "gender": "female",
                "manual_weight_grams": 777,
            },
        )
        db.flush()
        db.expire_all()
        product = db.query(Product).filter(Product.id == int(product.id)).one()

        assert product.gender == "female"
        assert product.gender_is_manual is True
        assert product.manual_weight_grams == 777
        assert product.presentation is not None
        assert product.presentation.title_override == "Display title"
        assert product.presentation.brand_override_name == "Override Designer"
        assert product.presentation.description_text == "Text override"
        assert product.presentation.description_html == "<p>HTML override</p>"
        assert product.presentation.description_visibility is False
        assert listing.source_designer_raw == "Autosave Designer"

        ProductWriteService(db).update_product(
            product_id=int(product.id),
            payload={"reset_to_default": ["title_override", "brand_override_name", "description_text", "description_html", "description_visibility", "gender", "manual_weight_grams"]},
        )
        db.flush()
        db.expire_all()
        product = db.query(Product).filter(Product.id == int(product.id)).one()
        db.refresh(listing)

        assert product.gender == "male"
        assert product.gender_is_manual is False
        assert product.manual_weight_grams is None
        assert product.presentation is not None
        assert product.presentation.title_override is None
        assert product.presentation.brand_override_name is None
        assert product.presentation.description_text is None
        assert product.presentation.description_html is None
        assert product.presentation.description_visibility is None
        assert listing.source_designer_raw == "Autosave Designer"
    finally:
        db.rollback()
        db.close()


def test_product_write_service_rejects_invalid_variant_compare_at_price() -> None:
    db = SessionLocal()
    try:
        try:
            ProductWriteService(db).create_manual_product(
                {
                    "title": "Manual invalid compare-at",
                    "gender": "unisex",
                    "availability_mode": "in_stock",
                    "visibility_status": "visible",
                    "orderability_status": "orderable",
                    "variants": [
                        {
                            "title": "Default",
                            "price": 1000,
                            "compare_at_price": 999,
                            "currency": "RUB",
                            "available": True,
                        }
                    ],
                    "manual_image_asset_ids": [],
                }
            )
            raise AssertionError("Expected ValidationError")
        except ValidationError as exc:
            assert "variant compare_at_price must be greater than price" in str(exc)
    finally:
        db.rollback()
        db.close()


def test_product_write_service_rejects_too_large_variant_compare_at_price() -> None:
    db = SessionLocal()
    try:
        try:
            ProductWriteService(db).create_manual_product(
                {
                    "title": "Manual invalid compare-at overflow",
                    "gender": "unisex",
                    "availability_mode": "in_stock",
                    "visibility_status": "visible",
                    "orderability_status": "orderable",
                    "variants": [
                        {
                            "title": "Default",
                            "price": 324,
                            "compare_at_price": 53425435342,
                            "currency": "RUB",
                            "available": True,
                        }
                    ],
                    "manual_image_asset_ids": [],
                }
            )
            raise AssertionError("Expected ValidationError")
        except ValidationError as exc:
            assert "Старая цена варианта слишком большая" in str(exc)
    finally:
        db.rollback()
        db.close()


def test_product_write_service_rejects_in_stock_for_sold_out_listing() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = Source(
            key=f"availability-lock-{marker}.example",
            name=f"Availability Lock {marker}",
            base_url=f"https://availability-lock-{marker}.example",
            base_url_normalized=f"availability-lock-{marker}.example",
        )
        db.add(source)
        db.flush()
        db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        ProductIngestService(db).apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": f"https://availability-lock-{marker}.example/products/test-item",
                    "handle": "test-item",
                    "title": "Availability Lock Product",
                    "description": "Source description",
                    "designer": "Availability Designer",
                    "category": "Belts",
                    "tags": [],
                    "gender": "male",
                    "source_weight_grams": 410,
                    "orderability_status": "sold_out",
                    "variants": [{"title": "UNI", "price": 100.0, "currency": "USD", "available": False}],
                    "images": [],
                }
            ],
        )
        db.flush()

        listing = db.query(ProductListing).filter(ProductListing.source_id == int(source.id), ProductListing.handle == "test-item").one()
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()

        try:
            ProductWriteService(db).update_product(
                product_id=int(product.id),
                payload={"availability_mode": "in_stock"},
            )
            raise AssertionError("Expected ValidationError")
        except ValidationError as exc:
            assert "Нельзя переключить, пока товар распродан" in str(exc)
    finally:
        db.rollback()
        db.close()
