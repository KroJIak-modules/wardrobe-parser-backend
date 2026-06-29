from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.core.exceptions import ValidationError
from app.models import Designer, DesignerSourceName, ImageAsset, Product, ProductListingGalleryImage, ProductListingVariant, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.product_write_service import ProductWriteService
from app.services.settings.weight_rule_service import WeightRuleService


def _create_manual_product(db) -> int:
    return ProductWriteService(db).create_manual_product(
        {
            "title": "Personal product",
            "description_text": "Description",
            "designer_name": "Designer",
            "source_category_name": "Category",
            "gender": "unisex",
            "availability_mode": "in_stock",
            "visibility_status": "visible",
            "orderability_status": "orderable",
            "variants": [{"title": "Default", "price": 1000, "currency": "RUB", "available": True}],
            "manual_image_asset_ids": [],
            "manual_weight_grams": 500,
            "filter_slugs": [],
            "custom_catalog_slugs": [],
        }
    )


def test_product_payload_marks_personal_manual_product_without_sync_binding() -> None:
    db = SessionLocal()
    try:
        product_id = _create_manual_product(db)
        payload = ProductQueryService(db).get_product_payload(product_id, audience="admin")

        assert payload is not None
        assert payload["source_mode"] == "personal"
        assert payload["has_sync_listing"] is False
    finally:
        db.rollback()
        db.close()


def test_manual_product_creation_creates_source_brand_mapping_and_catalog_designer() -> None:
    db = SessionLocal()
    brand = f"ZZ TEST MANUAL BRAND {uuid4().hex[:12]}"
    try:
        product_id = ProductWriteService(db).create_manual_product(
            {
                "title": "Manual designer creation",
                "description_text": "Description",
                "designer_name": brand,
                "source_category_name": "Shorts",
                "gender": "unisex",
                "availability_mode": "in_stock",
                "visibility_status": "visible",
                "orderability_status": "orderable",
                "variants": [{"title": "Default", "price": 1000, "currency": "RUB", "available": True}],
                "manual_image_asset_ids": [],
                "filter_slugs": [],
                "custom_catalog_slugs": [],
            }
        )
        db.flush()

        product = db.query(Product).filter(Product.id == product_id).one()
        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).one()
        designer = db.query(Designer).filter(Designer.id == int(mapping.designer_id or 0)).one()

        assert int(product.designer_id or 0) == int(designer.id)
        assert designer.name == brand
        assert designer.origin_kind == "auto"
        assert mapping.designer_name == brand
        assert int(mapping.designer_id or 0) == int(designer.id)

        ProductWriteService(db).delete_manual_product(product_id=product_id)
        db.flush()

        assert db.query(DesignerSourceName).filter(DesignerSourceName.source_name == brand).count() == 0
        assert db.query(Designer).filter(Designer.name == brand).count() == 0
    finally:
        db.rollback()
        db.close()


def test_manual_product_creation_keeps_only_uploaded_gallery_rows() -> None:
    db = SessionLocal()
    marker = uuid4().hex
    try:
        asset = ImageAsset(
            storage_key=f"products/tests/{marker}.jpg",
            mime_type="image/jpeg",
            byte_size=128,
            width_px=40,
            height_px=40,
            checksum_sha256=f"{marker:0<64}"[:64],
        )
        db.add(asset)
        db.flush()

        product_id = ProductWriteService(db).create_manual_product(
            {
                "title": "Manual gallery product",
                "description_text": "Description",
                "designer_name": f"ZZ TEST MANUAL GALLERY {marker[:8]}",
                "source_category_name": "Shorts",
                "gender": "unisex",
                "availability_mode": "in_stock",
                "visibility_status": "visible",
                "orderability_status": "orderable",
                "variants": [{"title": "Default", "price": 1000, "currency": "RUB", "available": True}],
                "manual_image_asset_ids": [int(asset.id)],
                "filter_slugs": [],
                "custom_catalog_slugs": [],
            }
        )
        db.flush()

        rows = (
            db.query(ProductListingGalleryImage)
            .filter(ProductListingGalleryImage.product_id == int(product_id))
            .order_by(ProductListingGalleryImage.position.asc(), ProductListingGalleryImage.id.asc())
            .all()
        )

        assert len(rows) == 1
        assert rows[0].origin_kind == "uploaded_asset"
        assert int(rows[0].image_asset_id or 0) == int(asset.id)
        assert rows[0].listing_image_id is None
    finally:
        db.rollback()
        db.close()


def test_product_write_service_allows_variant_editing_for_personal_manual_product_without_sync_binding() -> None:
    db = SessionLocal()
    try:
        product_id = _create_manual_product(db)

        ProductWriteService(db).update_manual_variants(
            product_id=product_id,
            variants=[
                {"title": "S", "price": 1200, "currency": "RUB", "available": True},
                {"title": "M", "price": 1300, "currency": "RUB", "available": False},
            ],
        )
        db.flush()

        product = db.query(Product).filter(Product.id == product_id).one()
        rows = (
            db.query(ProductListingVariant)
            .filter(ProductListingVariant.listing_id == int(product.primary_listing_id or 0))
            .order_by(ProductListingVariant.position.asc())
            .all()
        )
        assert len(rows) == 2
        assert [row.title for row in rows] == ["S", "M"]
        assert [int(row.price_amount) for row in rows] == [1200, 1300]
        assert [bool(row.is_orderable) for row in rows] == [True, False]
    finally:
        db.rollback()
        db.close()


def test_manual_rub_only_variants_make_weight_optional_and_remove_missing_weight_from_settings() -> None:
    db = SessionLocal()
    try:
        product_id = ProductWriteService(db).create_manual_product(
            {
                "title": "Manual no weight RUB product",
                "description_text": "Description",
                "designer_name": "Designer",
                "source_category_name": "Category",
                "gender": "unisex",
                "availability_mode": "by_order",
                "visibility_status": "visible",
                "orderability_status": "orderable",
                "variants": [{"title": "Default", "price": 100, "currency": "USD", "available": True}],
                "manual_image_asset_ids": [],
            }
        )
        db.flush()

        product = db.query(Product).filter(Product.id == product_id).one()
        primary_listing = product.primary_listing
        assert primary_listing is not None
        assert primary_listing.orderability_status == "unavailable"
        assert primary_listing.status_reason == "missing_weight"

        ProductWriteService(db).update_manual_variants(
            product_id=product_id,
            variants=[
                {"title": "S", "price": 1200, "currency": "RUB", "available": True},
                {"title": "M", "price": 1300, "currency": "RUB", "available": False},
            ],
        )
        db.flush()
        db.refresh(product)
        db.refresh(primary_listing)

        assert primary_listing.orderability_status == "orderable"
        assert primary_listing.status_reason is None
        assert product.weight_rule_id is None

        missing_ids = {
            int(item.id)
            for item in WeightRuleService(db).list_missing_weight_products(limit=500, offset=0)
        }
        assert product_id not in missing_ids
    finally:
        db.rollback()
        db.close()


def test_product_write_service_rejects_variant_editing_when_sync_bound() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        product_id = _create_manual_product(db)
        sync_key = f"sync-variant-{marker}.example"
        sync_source = Source(
            key=sync_key,
            name=sync_key,
            base_url=f"https://{sync_key}",
            base_url_normalized=sync_key,
        )
        db.add(sync_source)
        db.flush()
        db.add(SourceSetting(source_id=int(sync_source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        ProductIngestService(db).apply_batch(
            source_id=int(sync_source.id),
            target_product_id=product_id,
            items=[
                {
                    "url": f"https://{sync_key}/products/sync-item",
                    "handle": "sync-item",
                    "title": "Sync item",
                    "description": "Sync description",
                    "designer": "Designer",
                    "category": "Category",
                    "tags": [],
                    "gender": "unisex",
                    "source_weight_grams": 500,
                    "orderability_status": "orderable",
                    "variants": [{"title": "Default", "price": 1500, "currency": "RUB", "available": True}],
                    "images": [],
                }
            ],
        )
        db.flush()

        try:
            ProductWriteService(db).update_manual_variants(
                product_id=product_id,
                variants=[{"title": "New", "price": 1600, "currency": "RUB", "available": True}],
            )
            raise AssertionError("Expected ValidationError")
        except ValidationError as exc:
            assert "personal source without sync binding" in str(exc)
    finally:
        db.rollback()
        db.close()


def test_sync_bound_created_product_belongs_to_real_source_instead_of_personal() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    override_brand = f"ZZ TEST OVERRIDE BRAND {marker}"
    source_brand = f"ZZ TEST SOURCE BRAND {marker}"
    try:
        source_key = f"sync-bound-{marker}.example"
        sync_source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            parser_config={"mode": "manual"},
        )
        db.add(sync_source)
        db.flush()
        db.add(SourceSetting(source_id=int(sync_source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        product_id = ProductWriteService(db).create_sync_bound_product(
            payload={
                "title": "Overridden Sync Title",
                "description_text": "Overridden Sync Description",
                "designer_name": override_brand,
                "gender": "female",
                "availability_mode": "by_order",
                "visibility_status": "visible",
                "manual_weight_grams": 440,
                "filter_slugs": [],
                "custom_catalog_slugs": [],
            },
            source_id=int(sync_source.id),
            service_item={
                "url": f"https://{source_key}/products/sync-item",
                "handle": "sync-item",
                "title": "Source Sync Title",
                "description": "Source Sync Description",
                "designer": source_brand,
                "category": "Outerwear",
                "tags": ["archive"],
                "gender": "male",
                "source_weight_grams": 400,
                "orderability_status": "orderable",
                "variants": [{"title": "Default", "price": 1500, "currency": "USD", "available": True}],
                "images": [f"https://{source_key}/image.jpg"],
            },
        )
        db.flush()

        payload = ProductQueryService(db).get_product_payload(product_id, audience="admin")

        assert payload is not None
        assert payload["source_id"] == int(sync_source.id)
        assert payload["source_name"] == source_key
        assert payload["source_mode"] == "manual"
        assert payload["has_sync_listing"] is True
        assert payload["title"] == "Overridden Sync Title"
        assert payload["brand_name"] == override_brand
        assert payload["description"] == "Overridden Sync Description"
        assert all(str(listing["ingest_mode"]) == "sync" for listing in payload["listings"])
        assert all(listing["source_name"] == source_key for listing in payload["listings"])
        assert len(payload["variants"]) == 1
        assert payload["variants"][0]["source_name"] == source_key

        product = db.query(Product).filter(Product.id == product_id).one()
        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == override_brand).one()
        designer = db.query(Designer).filter(Designer.id == int(mapping.designer_id or 0)).one()
        assert int(product.designer_id or 0) == int(designer.id)
        assert designer.name == override_brand
    finally:
        db.rollback()
        db.close()


def test_sync_bound_creation_does_not_duplicate_source_images_with_uploaded_assets() -> None:
    db = SessionLocal()
    marker = uuid4().hex
    try:
        asset = ImageAsset(
            storage_key=f"products/tests/{marker}.jpg",
            mime_type="image/jpeg",
            byte_size=128,
            width_px=40,
            height_px=40,
            checksum_sha256=f"{marker:0<64}"[:64],
        )
        db.add(asset)
        db.flush()

        source_key = f"sync-gallery-{marker[:12]}.example"
        sync_source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            parser_config={"mode": "manual"},
        )
        db.add(sync_source)
        db.flush()
        db.add(SourceSetting(source_id=int(sync_source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        product_id = ProductWriteService(db).create_sync_bound_product(
            payload={
                "title": "Sync gallery product",
                "description_text": "Sync description",
                "designer_name": f"ZZ TEST SYNC GALLERY {marker[:8]}",
                "gender": "female",
                "availability_mode": "by_order",
                "visibility_status": "visible",
                "manual_image_asset_ids": [int(asset.id)],
                "filter_slugs": [],
                "custom_catalog_slugs": [],
            },
            source_id=int(sync_source.id),
            service_item={
                "url": f"https://{source_key}/products/gallery-item",
                "handle": "gallery-item",
                "title": "Gallery item",
                "description": "Sync description",
                "designer": f"ZZ TEST SYNC RAW {marker[:8]}",
                "category": "Outerwear",
                "tags": ["archive"],
                "gender": "male",
                "source_weight_grams": 400,
                "orderability_status": "orderable",
                "variants": [{"title": "Default", "price": 1500, "currency": "USD", "available": True}],
                "images": [
                    f"https://{source_key}/image-1.jpg",
                    f"https://{source_key}/image-2.jpg",
                ],
            },
        )
        db.flush()

        rows = (
            db.query(ProductListingGalleryImage)
            .filter(ProductListingGalleryImage.product_id == int(product_id))
            .order_by(ProductListingGalleryImage.position.asc(), ProductListingGalleryImage.id.asc())
            .all()
        )

        assert len(rows) == 2
        assert all(row.origin_kind == "source_image" for row in rows)
        assert all(row.image_asset_id is None for row in rows)
        assert all(row.listing_image_id is not None for row in rows)
    finally:
        db.rollback()
        db.close()


def test_sync_bound_created_product_creates_source_brand_mapping_when_no_override() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source_key = f"sync-source-brand-{marker}.example"
        source_brand = "Aéropostale"
        sync_source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            parser_config={"mode": "manual"},
        )
        db.add(sync_source)
        db.flush()
        db.add(SourceSetting(source_id=int(sync_source.id), is_enabled=True, is_sync_enabled=True))
        db.flush()

        product_id = ProductWriteService(db).create_sync_bound_product(
            payload={
                "title": "Source Brand Product",
                "description_text": "Sync description",
                "designer_name": source_brand,
                "gender": "female",
                "availability_mode": "by_order",
                "visibility_status": "visible",
                "manual_weight_grams": 440,
                "filter_slugs": [],
                "custom_catalog_slugs": [],
            },
            source_id=int(sync_source.id),
            service_item={
                "url": f"https://{source_key}/products/source-brand",
                "handle": "source-brand",
                "title": "Source Brand Product",
                "description": "Source Sync Description",
                "designer": source_brand,
                "category": "Outerwear",
                "tags": ["archive"],
                "gender": "female",
                "source_weight_grams": 400,
                "orderability_status": "orderable",
                "variants": [{"title": "Default", "price": 1500, "currency": "USD", "available": True}],
                "images": [f"https://{source_key}/image.jpg"],
            },
        )
        db.flush()

        product = db.query(Product).filter(Product.id == product_id).one()
        mapping = db.query(DesignerSourceName).filter(DesignerSourceName.source_name == source_brand).one()
        designer = db.query(Designer).filter(Designer.id == int(mapping.designer_id or 0)).one()

        assert int(product.designer_id or 0) == int(designer.id)
        assert designer.name == source_brand
        assert mapping.designer_name == source_brand
    finally:
        db.rollback()
        db.close()


def test_manual_rub_variant_keeps_final_price_and_compare_at_without_formula() -> None:
    db = SessionLocal()
    try:
        product_id = ProductWriteService(db).create_manual_product(
            {
                "title": "Manual RUB product",
                "description_text": "Description",
                "designer_name": "Designer",
                "source_category_name": "Category",
                "gender": "unisex",
                "availability_mode": "in_stock",
                "visibility_status": "visible",
                "orderability_status": "orderable",
                "variants": [
                    {
                        "title": "Default",
                        "price": 17990,
                        "compare_at_price": 21990,
                        "currency": "RUB",
                        "available": True,
                    }
                ],
                "manual_image_asset_ids": [],
            }
        )
        db.flush()

        product = db.query(Product).filter(Product.id == product_id).one()
        row = (
            db.query(ProductListingVariant)
            .filter(ProductListingVariant.listing_id == int(product.primary_listing_id or 0))
            .one()
        )
        service = ProductQueryService(db)
        variant_payload = service._variant_payload(product, row.listing, row)

        assert str(row.pricing_mode) == "fixed_final_rub"
        assert variant_payload["price"] == 17990.0
        assert variant_payload["final_price"] == 17990.0
        assert variant_payload["compare_at_price"] == 21990.0
        assert variant_payload["final_compare_at_price"] == 21990.0
    finally:
        db.rollback()
        db.close()


def test_manual_compare_at_price_is_allowed_for_non_rub_variant() -> None:
    db = SessionLocal()
    try:
        product_id = ProductWriteService(db).create_manual_product(
            {
                "title": "Manual USD compare-at",
                "gender": "unisex",
                "availability_mode": "in_stock",
                "visibility_status": "visible",
                "orderability_status": "orderable",
                "variants": [
                    {
                        "title": "Default",
                        "price": 120,
                        "compare_at_price": 150,
                        "currency": "USD",
                        "available": True,
                    }
                ],
                "manual_image_asset_ids": [],
            }
        )
        db.flush()

        product = db.query(Product).filter(Product.id == product_id).one()
        row = (
            db.query(ProductListingVariant)
            .filter(ProductListingVariant.listing_id == int(product.primary_listing_id or 0))
            .one()
        )
        service = ProductQueryService(db)
        variant_payload = service._variant_payload(product, row.listing, row)

        assert str(row.pricing_mode) == "source"
        assert variant_payload["price"] == 120.0
        assert variant_payload["compare_at_price"] == 150.0
    finally:
        db.rollback()
        db.close()
