from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService
from app.services.catalog.site_catalog_sort_price_service import SiteCatalogSortPriceService
from app.services.settings.pricing_service import PricingSettingsService


def _default_supplier_id(db) -> int:
    settings = PricingSettingsService(db).get_settings()
    supplier = next(iter(settings.suppliers or []), None)
    if supplier is None:
        raise AssertionError("Seed supplier is required for tests")
    return int(supplier.id)


def _create_source(db, marker: str) -> Source:
    source = Source(
        key=f"product-visibility-{marker}.example",
        name=f"Product Visibility {marker}",
        base_url=f"https://product-visibility-{marker}.example",
        base_url_normalized=f"product-visibility-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(
        SourceSetting(
            source_id=int(source.id),
            is_enabled=True,
            is_sync_enabled=True,
            show_images=True,
            description_mode="text",
            supplier_id=_default_supplier_id(db),
        )
    )
    db.flush()
    return source


def _ingest_product(
    db,
    *,
    source_id: int,
    marker: str,
    handle: str,
    orderability_status: str,
    status_reason: str | None,
    images: list[str] | None = None,
    variants: list[dict] | None = None,
) -> Product:
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://product-visibility-{marker}.example/products/{handle}",
                "handle": handle,
                "title": f"Visibility Test {handle}",
                "description": "",
                "designer": "Visibility Test Designer",
                "category": "Outerwear",
                "tags": ["visibility"],
                "gender": "unisex",
                "source_weight_grams": 500,
                "orderability_status": orderability_status,
                "status_reason": status_reason,
                "variants": variants if variants is not None else [{"title": "UNI", "price": 120.0, "currency": "USD", "available": orderability_status == "orderable"}],
                "images": images if images is not None else [f"https://product-visibility-{marker}.example/images/{handle}.jpg"],
            }
        ],
    )
    db.flush()
    listing = db.query(ProductListing).filter(ProductListing.source_id == int(source_id), ProductListing.handle == handle).one()
    product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()
    SiteCatalogSortPriceService(db).refresh_product_ids([int(product.id)], commit=False)
    db.flush()
    return product


def test_unavailable_product_is_not_openable_but_dedup_card_still_knows_its_status() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = _create_source(db, marker)
        available_product = _ingest_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="available-item",
            orderability_status="orderable",
            status_reason=None,
        )
        unavailable_product = _ingest_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="unavailable-item",
            orderability_status="unavailable",
            status_reason="source_removed",
        )

        service = ProductQueryService(db)

        available_payload = service.get_product_payload(int(available_product.id), audience="admin")
        unavailable_admin_payload = service.get_product_payload(int(unavailable_product.id), audience="admin")
        unavailable_public_payload = service.get_product_payload(int(unavailable_product.id), audience="public")
        unavailable_dedup_payload = service.build_dedup_candidate_payload(unavailable_product)

        assert available_payload is not None
        assert unavailable_admin_payload is None
        assert unavailable_public_payload is None
        assert unavailable_dedup_payload["orderability_status"] == "unavailable"
        assert unavailable_dedup_payload["status_reason"] == "source_removed"
    finally:
        db.rollback()
        db.close()


def test_product_without_final_price_becomes_unavailable_everywhere() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = _create_source(db, marker)
        product = _ingest_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="missing-price-item",
            orderability_status="orderable",
            status_reason=None,
            images=[f"https://product-visibility-{marker}.example/images/missing-price.jpg"],
            variants=[{"title": "UNI", "price": None, "currency": "USD", "available": True}],
        )

        service = ProductQueryService(db)
        admin_payload = service.build_admin_table_product_payload(product)
        unavailable_items = service.list_products(
            limit=20,
            offset=0,
            query="missing-price-item",
            orderability_status="unavailable",
            audience="admin",
        )["items"]
        orderable_items = service.list_products(
            limit=20,
            offset=0,
            query="missing-price-item",
            orderability_status="orderable",
            audience="admin",
        )["items"]

        assert admin_payload["orderability_status"] == "unavailable"
        assert admin_payload["status_reason"] == "missing_source_price"
        assert [item["id"] for item in unavailable_items] == [int(product.id)]
        assert orderable_items == []
        assert service.get_product_payload(int(product.id), audience="public") is None
    finally:
        db.rollback()
        db.close()


def test_product_without_images_becomes_unavailable_everywhere() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = _create_source(db, marker)
        product = _ingest_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="missing-images-item",
            orderability_status="orderable",
            status_reason=None,
            images=[],
            variants=[{"title": "UNI", "price": 120.0, "currency": "USD", "available": True}],
        )

        service = ProductQueryService(db)
        admin_payload = service.build_admin_table_product_payload(product)
        unavailable_items = service.list_products(
            limit=20,
            offset=0,
            query="missing-images-item",
            orderability_status="unavailable",
            audience="admin",
        )["items"]
        sold_out_items = service.list_products(
            limit=20,
            offset=0,
            query="missing-images-item",
            orderability_status="sold_out",
            audience="admin",
        )["items"]

        assert admin_payload["orderability_status"] == "unavailable"
        assert admin_payload["status_reason"] == "missing_images"
        assert [item["id"] for item in unavailable_items] == [int(product.id)]
        assert sold_out_items == []
        assert service.get_product_payload(int(product.id), audience="public") is None
    finally:
        db.rollback()
        db.close()
