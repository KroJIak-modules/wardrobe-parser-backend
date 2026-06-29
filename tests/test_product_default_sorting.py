from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService


def _create_source(db, *, key: str, sort_priority: int) -> Source:
    source = Source(
        key=key,
        name=key.replace(".example", "").replace("-", " ").title(),
        base_url=f"https://{key}/",
        base_url_normalized=f"https://{key}/",
        adapter_key=f"adapter-{key}",
        parser_config={"mode": "auto"},
    )
    db.add(source)
    db.flush()
    db.add(
        SourceSetting(
            source_id=int(source.id),
            sort_priority=int(sort_priority),
            is_enabled=True,
            is_sync_enabled=True,
            show_images=True,
            description_mode="text",
        )
    )
    db.flush()
    return source


def _ingest_product(
    db,
    *,
    source_id: int,
    host_key: str,
    handle: str,
    designer: str,
    orderability_status: str,
) -> Product:
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://{host_key}/products/{handle}",
                "handle": handle,
                "title": handle.replace("-", " ").title(),
                "description": "",
                "designer": designer,
                "category": "Outerwear",
                "tags": ["sorting"],
                "gender": "unisex",
                "source_weight_grams": 500,
                "orderability_status": orderability_status,
                "status_reason": None if orderability_status != "unavailable" else "sorting_test_unavailable",
                "variants": [
                    {
                        "title": "UNI",
                        "price": 120.0,
                        "currency": "USD",
                        "available": orderability_status == "orderable",
                    }
                ],
                "images": [],
            }
        ],
    )
    db.flush()
    listing = (
        db.query(ProductListing)
        .filter(ProductListing.source_id == int(source_id), ProductListing.handle == handle)
        .one()
    )
    return db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()


def test_default_product_sorting_uses_source_orderability_novelty_and_visibility() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:10]
    source_a_key = f"sorting-a-{marker}.example"
    source_b_key = f"sorting-b-{marker}.example"
    try:
        source_a = _create_source(db, key=source_a_key, sort_priority=1)
        source_b = _create_source(db, key=source_b_key, sort_priority=2)

        alpha_orderable_older_visible = _ingest_product(
            db,
            source_id=int(source_a.id),
            host_key=source_a_key,
            handle="alpha-orderable-older-visible",
            designer="Alpha Designer",
            orderability_status="orderable",
        )
        alpha_orderable_newer_hidden = _ingest_product(
            db,
            source_id=int(source_a.id),
            host_key=source_a_key,
            handle="alpha-orderable-newer-hidden",
            designer="Alpha Designer",
            orderability_status="orderable",
        )
        alpha_unavailable = _ingest_product(
            db,
            source_id=int(source_a.id),
            host_key=source_a_key,
            handle="alpha-unavailable",
            designer="Alpha Designer",
            orderability_status="unavailable",
        )
        alpha_sold_out = _ingest_product(
            db,
            source_id=int(source_a.id),
            host_key=source_a_key,
            handle="alpha-sold-out",
            designer="Alpha Designer",
            orderability_status="sold_out",
        )
        beta_orderable_newest = _ingest_product(
            db,
            source_id=int(source_a.id),
            host_key=source_a_key,
            handle="beta-orderable-newest",
            designer="Beta Designer",
            orderability_status="orderable",
        )
        source_b_alpha = _ingest_product(
            db,
            source_id=int(source_b.id),
            host_key=source_b_key,
            handle="source-b-alpha",
            designer="Alpha Designer",
            orderability_status="orderable",
        )

        alpha_orderable_older_visible.availability_mode = "in_stock"
        alpha_orderable_newer_hidden.visibility_status = "hidden"
        alpha_orderable_newer_hidden.availability_mode = "in_stock"
        alpha_unavailable.availability_mode = "by_order"
        alpha_sold_out.availability_mode = "in_stock"
        beta_orderable_newest.availability_mode = "in_stock"
        source_b_alpha.availability_mode = "in_stock"
        db.flush()

        service = ProductQueryService(db)
        admin_table_ids = [int(item["id"]) for item in service.list_admin_table_products(limit=50, offset=0)["items"]]
        admin_list_ids = [int(item["id"]) for item in service.list_products(limit=50, offset=0, audience="admin")["items"]]

        expected_ids = [
            int(beta_orderable_newest.id),
            int(alpha_orderable_newer_hidden.id),
            int(alpha_orderable_older_visible.id),
            int(alpha_unavailable.id),
            int(alpha_sold_out.id),
            int(source_b_alpha.id),
        ]

        assert admin_table_ids[:6] == expected_ids
        assert admin_list_ids[:6] == expected_ids
    finally:
        db.rollback()
        db.close()
