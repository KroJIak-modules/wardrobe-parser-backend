from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting, Supplier, SupplierShippingRate
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService


def _create_priced_source(db, marker: str) -> Source:
    source = Source(
        key=f"price-contract-{marker}.example",
        name=f"Price Contract {marker}",
        base_url=f"https://price-contract-{marker}.example",
        base_url_normalized=f"price-contract-{marker}.example",
    )
    db.add(source)
    db.flush()

    supplier = Supplier(
        key=f"supplier-{marker}",
        name=f"Supplier {marker}",
        provider_kind="main",
        rate_currency="RUB",
        is_enabled=True,
    )
    db.add(supplier)
    db.flush()

    db.add(
        SupplierShippingRate(
            supplier_id=int(supplier.id),
            min_weight_kg=0.0,
            max_weight_kg=2.0,
            price_rub=2500.0,
        )
    )
    db.add(
        SourceSetting(
            source_id=int(source.id),
            supplier_id=int(supplier.id),
            is_enabled=True,
            is_sync_enabled=True,
            show_images=True,
            description_mode="text",
        )
    )
    db.flush()
    return source


def _ingest_multi_variant_product(db, *, source_id: int, marker: str, handle: str) -> Product:
    ProductIngestService(db).apply_batch(
        source_id=int(source_id),
        items=[
            {
                "url": f"https://price-contract-{marker}.example/products/{handle}",
                "handle": handle,
                "title": "Archive Designer Multi Coat",
                "description": "Structured coat",
                "designer": "Archive Designer",
                "category": "Outerwear",
                "tags": ["coat", "archive"],
                "gender": "unisex",
                "source_weight_grams": 900,
                "orderability_status": "orderable",
                "variants": [
                    {"title": "46", "price": 100.0, "currency": "USD", "available": True},
                    {"title": "48", "price": 140.0, "currency": "USD", "available": True},
                ],
                "images": [f"https://cdn.example/{marker}.jpg"],
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


def test_admin_product_payload_uses_price_summary_and_variant_final_prices() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = _create_priced_source(db, marker)
        product = _ingest_multi_variant_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="archive-multi-coat",
        )

        payload = ProductQueryService(db).get_product_payload(int(product.id), audience="admin")

        assert payload is not None
        assert "price" not in payload
        assert "currency" not in payload
        assert "source_price" not in payload
        assert "source_currency" not in payload
        assert "final_price" not in payload
        assert "final_currency" not in payload
        assert payload["price_summary"]["source_display_price"] == 100.0
        assert payload["price_summary"]["source_currency"] == "USD"
        assert payload["price_summary"]["source_has_range"] is True
        assert payload["price_summary"]["final_display_price"] is not None
        assert payload["price_summary"]["final_has_range"] is True
        assert len(payload["variants"]) == 2
        assert all("final_price" in variant for variant in payload["variants"])
        assert all(variant["final_price"] is not None for variant in payload["variants"])
    finally:
        db.rollback()
        db.close()


def test_admin_products_table_item_uses_price_summary_without_legacy_price_fields() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = _create_priced_source(db, marker)
        product = _ingest_multi_variant_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="archive-table-coat",
        )

        items = ProductQueryService(db).list_admin_table_products(limit=20, offset=0)["items"]
        row = next(item for item in items if int(item["id"]) == int(product.id))

        assert "price" not in row
        assert "currency" not in row
        assert "source_price" not in row
        assert "source_currency" not in row
        assert "final_price" not in row
        assert "final_currency" not in row
        assert row["price_summary"]["source_display_price"] == 100.0
        assert row["price_summary"]["source_has_range"] is True
        assert row["price_summary"]["final_display_price"] is not None
        assert row["price_summary"]["final_has_range"] is True
    finally:
        db.rollback()
        db.close()


def test_dedup_candidate_payload_uses_price_summary_without_scalar_price_fields() -> None:
    db = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        source = _create_priced_source(db, marker)
        product = _ingest_multi_variant_product(
            db,
            source_id=int(source.id),
            marker=marker,
            handle="archive-dedup-coat",
        )

        payload = ProductQueryService(db).build_dedup_candidate_payload(product)

        assert "price" not in payload
        assert "currency" not in payload
        assert "source_price" not in payload
        assert "final_price" not in payload
        assert payload["price_summary"]["source_display_price"] == 100.0
        assert payload["price_summary"]["source_has_range"] is True
    finally:
        db.rollback()
        db.close()


def test_price_summary_uses_representative_available_variant_components() -> None:
    variants = [
        {
            "id": 11,
            "listing_id": 101,
            "source_ref_id": "unavailable-cheap",
            "available": False,
            "price": 70.0,
            "currency": "USD",
            "final_price": 6500.0,
            "final_currency": "RUB",
            "pricing_manual_required": False,
            "pricing_reason": None,
            "pricing_components": {"marker": "unavailable-cheap"},
        },
        {
            "id": 12,
            "listing_id": 101,
            "source_ref_id": "available-high",
            "available": True,
            "price": 120.0,
            "currency": "USD",
            "final_price": 11800.0,
            "final_currency": "RUB",
            "pricing_manual_required": False,
            "pricing_reason": None,
            "pricing_components": {"marker": "available-high"},
        },
        {
            "id": 13,
            "listing_id": 102,
            "source_ref_id": "available-low",
            "available": True,
            "price": 95.0,
            "currency": "USD",
            "final_price": 9100.0,
            "final_currency": "RUB",
            "pricing_manual_required": False,
            "pricing_reason": None,
            "pricing_components": {"marker": "available-low"},
        },
    ]

    summary = ProductQueryService._build_price_summary(variants)
    components = ProductQueryService._representative_pricing_components(variants, summary)

    assert summary is not None
    assert summary["representative_variant_id"] == 13
    assert summary["representative_listing_id"] == 102
    assert summary["representative_source_ref_id"] == "available-low"
    assert summary["source_display_price"] == 95.0
    assert summary["final_display_price"] == 9100.0
    assert summary["source_has_range"] is True
    assert summary["final_has_range"] is True
    assert components == {"marker": "available-low"}


def test_price_summary_falls_back_to_any_priced_variant_when_no_available_prices_exist() -> None:
    variants = [
        {
            "id": 21,
            "listing_id": 201,
            "source_ref_id": "priced-high",
            "available": False,
            "price": 140.0,
            "currency": "USD",
            "final_price": 12800.0,
            "final_currency": "RUB",
            "pricing_manual_required": True,
            "pricing_reason": "missing_weight",
            "pricing_components": {"marker": "priced-high"},
        },
        {
            "id": 22,
            "listing_id": 202,
            "source_ref_id": "priced-low",
            "available": False,
            "price": 90.0,
            "currency": "USD",
            "final_price": 8700.0,
            "final_currency": "RUB",
            "pricing_manual_required": True,
            "pricing_reason": "missing_weight",
            "pricing_components": {"marker": "priced-low"},
        },
    ]

    summary = ProductQueryService._build_price_summary(variants)
    components = ProductQueryService._representative_pricing_components(variants, summary)

    assert summary is not None
    assert summary["representative_variant_id"] == 22
    assert summary["source_display_price"] == 90.0
    assert summary["final_display_price"] == 8700.0
    assert summary["pricing_manual_required"] is True
    assert summary["pricing_reason"] == "missing_weight"
    assert components == {"marker": "priced-low"}


def test_price_summary_ignores_blocked_variant_differences_when_available_prices_match() -> None:
    variants = [
        {
            "id": 31,
            "listing_id": 301,
            "source_ref_id": "available-a",
            "available": True,
            "price": 95.0,
            "currency": "USD",
            "final_price": 9100.0,
            "final_currency": "RUB",
        },
        {
            "id": 32,
            "listing_id": 302,
            "source_ref_id": "available-b",
            "available": True,
            "price": 95.0,
            "currency": "USD",
            "final_price": 9100.0,
            "final_currency": "RUB",
        },
        {
            "id": 33,
            "listing_id": 303,
            "source_ref_id": "blocked-different",
            "available": False,
            "price": 140.0,
            "currency": "USD",
            "final_price": 12800.0,
            "final_currency": "RUB",
        },
    ]

    summary = ProductQueryService._build_price_summary(variants)

    assert summary is not None
    assert summary["source_display_price"] == 95.0
    assert summary["final_display_price"] == 9100.0
    assert summary["source_has_range"] is False
    assert summary["final_has_range"] is False


def test_price_summary_shows_range_for_blocked_only_variants_with_different_prices() -> None:
    variants = [
        {
            "id": 41,
            "listing_id": 401,
            "source_ref_id": "blocked-low",
            "available": False,
            "price": 90.0,
            "currency": "USD",
            "final_price": 8700.0,
            "final_currency": "RUB",
        },
        {
            "id": 42,
            "listing_id": 402,
            "source_ref_id": "blocked-high",
            "available": False,
            "price": 140.0,
            "currency": "USD",
            "final_price": 12800.0,
            "final_currency": "RUB",
        },
    ]

    summary = ProductQueryService._build_price_summary(variants)

    assert summary is not None
    assert summary["source_display_price"] == 90.0
    assert summary["final_display_price"] == 8700.0
    assert summary["source_has_range"] is True
    assert summary["final_has_range"] is True
