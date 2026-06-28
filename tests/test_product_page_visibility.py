from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService


def _create_source(db, marker: str) -> Source:
    source = Source(
        key=f"product-visibility-{marker}.example",
        name=f"Product Visibility {marker}",
        base_url=f"https://product-visibility-{marker}.example",
        base_url_normalized=f"product-visibility-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True, show_images=True, description_mode="text"))
    db.flush()
    return source


def _ingest_product(db, *, source_id: int, marker: str, handle: str, orderability_status: str, status_reason: str | None) -> Product:
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
                "variants": [{"title": "UNI", "price": 120.0, "currency": "USD", "available": orderability_status == "orderable"}],
                "images": [],
            }
        ],
    )
    db.flush()
    listing = db.query(ProductListing).filter(ProductListing.source_id == int(source_id), ProductListing.handle == handle).one()
    return db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()


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
