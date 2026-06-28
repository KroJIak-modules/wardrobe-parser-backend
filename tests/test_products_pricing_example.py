from __future__ import annotations

from uuid import uuid4

from app.core.database import SessionLocal
from app.models import Product, ProductListing, Source, SourceSetting, Supplier, SupplierShippingRate
from app.schemas.admin_settings import PricingSettingsResponse, PricingSupplierRateResponse, PricingSupplierResponse
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.product_query_service import ProductQueryService


def test_pricing_example_candidate_accepts_complete_derived_pricing_payload() -> None:
    product = {
        "source_price": 100.0,
        "final_price": 21500.0,
        "pricing_components": {
            "source_price_rub": 9500.0,
            "source_price_usd": 100.0,
            "source_price_eur": 88.5,
            "buyout_rub": 10000.0,
            "payment_fee_rub": 200.0,
            "customs_duty_rub": 0.0,
            "supplier_transport_rub": 3500.0,
            "subtotal_rub": 13700.0,
            "subtotal_after_markup_rub": 20550.0,
            "tax_rub": 1233.0,
            "usdt_extra_rub": 1.0,
            "bybit_bucket_rate_rub": 95.0,
            "derived_eur_to_usd_rate": 1.07,
            "derived_gbp_to_usd_rate": 1.27,
            "manual_required": False,
            "reason": None,
        },
    }

    assert ProductQueryService._is_pricing_example_candidate(product) is True


def test_pricing_example_candidate_rejects_incomplete_components() -> None:
    product = {
        "source_price": 100.0,
        "final_price": 21500.0,
        "pricing_components": {
            "source_price_rub": 9500.0,
            "source_price_usd": 100.0,
            "buyout_rub": 10000.0,
        },
    }

    assert ProductQueryService._is_pricing_example_candidate(product) is False


def test_build_sample_pricing_example_payload_uses_enabled_supplier_tariff() -> None:
    settings = PricingSettingsResponse(
        markup_multiplier=1.6,
        weight_tolerance=1.0,
        customs_threshold_eur=200.0,
        customs_duty_rate=0.15,
        eur_to_rub_rate=100.0,
        usd_to_rub_rate=90.0,
        usdt_to_rub_rate=92.0,
        usdt_extra_rub=1.0,
        final_rounding_mode="unit",
        payment_fee_rate=0.02,
        customs_processing_rate=0.08,
        customs_fixed_rub=540.0,
        tax_rate=0.06,
        bybit_rate_status="ok",
        bybit_rate_warning=None,
        bybit_bucket_step_usdt=0,
        bybit_bucket_max_usdt=0,
        bybit_bucket_rates=[],
        bybit_worker_auto_enabled=False,
        bybit_worker_interval_sec=0,
        bybit_last_updated_at=None,
        bybit_last_error=None,
        suppliers=[
            PricingSupplierResponse(
                id=7,
                key="italy-main",
                name="Italy Main",
                provider_kind="main",
                parent_supplier_id=None,
                rate_currency="RUB",
                is_enabled=True,
                rates=[PricingSupplierRateResponse(min_kg=0.0, max_kg=1.0, rub=2500.0)],
            )
        ],
        formula_latex="",
        formula_lines=[],
        formula_legend=[],
    )

    payload = ProductQueryService._build_sample_pricing_example_payload(settings)

    assert payload is not None
    assert payload["is_sample"] is True
    assert payload["product_id"] is None
    assert payload["price_summary"]["source_currency"] == "EUR"
    assert payload["price_summary"]["final_display_price"] is not None
    assert payload["components"]["supplier_transport_rub"] == 2500.0


def test_build_sample_pricing_example_payload_falls_back_to_zero_tariff_when_supplier_rates_missing() -> None:
    settings = PricingSettingsResponse(
        markup_multiplier=1.6,
        weight_tolerance=1.0,
        customs_threshold_eur=200.0,
        customs_duty_rate=0.15,
        eur_to_rub_rate=100.0,
        usd_to_rub_rate=90.0,
        usdt_to_rub_rate=92.0,
        usdt_extra_rub=1.0,
        final_rounding_mode="unit",
        payment_fee_rate=0.02,
        customs_processing_rate=0.08,
        customs_fixed_rub=540.0,
        tax_rate=0.06,
        bybit_rate_status="ok",
        bybit_rate_warning=None,
        bybit_bucket_step_usdt=0,
        bybit_bucket_max_usdt=0,
        bybit_bucket_rates=[],
        bybit_worker_auto_enabled=False,
        bybit_worker_interval_sec=0,
        bybit_last_updated_at=None,
        bybit_last_error=None,
        suppliers=[
            PricingSupplierResponse(
                id=7,
                key="italy-main",
                name="Italy Main",
                provider_kind="main",
                parent_supplier_id=None,
                rate_currency="RUB",
                is_enabled=True,
                rates=[],
            )
        ],
        formula_latex="",
        formula_lines=[],
        formula_legend=[],
    )

    payload = ProductQueryService._build_sample_pricing_example_payload(settings)

    assert payload is not None
    assert payload["is_sample"] is True
    assert "тариф еще не настроен" in payload["source_name"].lower()
    assert payload["components"]["supplier_transport_rub"] == 0.0


def test_pricing_example_uses_lightweight_lookup_without_legacy_full_product_load(monkeypatch) -> None:
    db = SessionLocal()
    marker = uuid4().hex[:10]
    try:
        source = Source(
            key=f"pricing-example-{marker}.example",
            name=f"Pricing Example {marker}",
            base_url=f"https://pricing-example-{marker}.example",
            base_url_normalized=f"pricing-example-{marker}.example",
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
                max_weight_kg=1.0,
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

        ProductIngestService(db).apply_batch(
            source_id=int(source.id),
            items=[
                {
                    "url": f"https://pricing-example-{marker}.example/products/archive-jacket",
                    "handle": f"archive-jacket-{marker}",
                    "title": "Archive Designer Jacket",
                    "description": "Structured archive jacket",
                    "designer": "Archive Designer",
                    "category": "Outerwear",
                    "tags": ["archive", "jacket"],
                    "gender": "unisex",
                    "source_weight_grams": 700,
                    "orderability_status": "orderable",
                    "variants": [{"title": "48", "price": 120.0, "currency": "USD", "available": True}],
                    "images": [f"https://cdn.example/{marker}.jpg"],
                }
            ],
        )
        db.flush()

        listing = (
            db.query(ProductListing)
            .filter(ProductListing.source_id == int(source.id), ProductListing.handle == f"archive-jacket-{marker}")
            .one()
        )
        product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()

        service = ProductQueryService(db)

        def _legacy_repo_call(*args, **kwargs):
            raise AssertionError("legacy list_products_by_ids path must not be used for pricing example")

        def _legacy_payload_call(*args, **kwargs):
            raise AssertionError("legacy build_admin_product_payload path must not be used for pricing example")

        monkeypatch.setattr(service.products, "list_products_by_ids", _legacy_repo_call)
        monkeypatch.setattr(service, "build_admin_product_payload", _legacy_payload_call)
        monkeypatch.setattr(service, "_is_pricing_example_candidate", lambda _payload: True)

        payload = service.get_pricing_example_payload(product_id=int(product.id))

        assert payload is not None
        assert payload["is_sample"] is False
        assert int(payload["product_id"]) == int(product.id)
        assert payload["title"] == "Jacket"
        assert payload["source_name"] == f"Pricing Example {marker}"
        assert payload["price_summary"]["source_currency"] == "USD"
        assert payload["price_summary"]["source_display_price"] == 120.0
        assert payload["price_summary"]["final_display_price"] is not None
        assert payload["image_url"] == f"https://cdn.example/{marker}.jpg"
        explicit_payload = service.get_pricing_example_payload(product_id=int(product.id))
        assert explicit_payload is not None
        assert int(explicit_payload["product_id"]) == int(product.id)
    finally:
        db.rollback()
        db.close()


def test_pricing_example_ignores_hidden_and_unavailable_products(monkeypatch) -> None:
    db = SessionLocal()
    marker = uuid4().hex[:10]
    try:
        source = Source(
            key=f"pricing-filter-{marker}.example",
            name=f"Pricing Filter {marker}",
            base_url=f"https://pricing-filter-{marker}.example",
            base_url_normalized=f"pricing-filter-{marker}.example",
        )
        db.add(source)
        db.flush()

        supplier = Supplier(
            key=f"supplier-filter-{marker}",
            name=f"Supplier Filter {marker}",
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
                max_weight_kg=1.0,
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

        service = ProductIngestService(db)

        def _create_product(handle: str, title: str) -> tuple[Product, ProductListing]:
            service.apply_batch(
                source_id=int(source.id),
                items=[
                    {
                        "url": f"https://pricing-filter-{marker}.example/products/{handle}",
                        "handle": handle,
                        "title": title,
                        "description": "Structured pricing example item",
                        "designer": "Archive Designer",
                        "category": "Outerwear",
                        "tags": ["archive", "jacket"],
                        "gender": "unisex",
                        "source_weight_grams": 700,
                        "orderability_status": "orderable",
                        "variants": [{"title": "48", "price": 120.0, "currency": "USD", "available": True}],
                        "images": [f"https://cdn.example/{handle}.jpg"],
                    }
                ],
            )
            db.flush()
            listing = (
                db.query(ProductListing)
                .filter(ProductListing.source_id == int(source.id), ProductListing.handle == handle)
                .one()
            )
            product = db.query(Product).filter(Product.primary_listing_id == int(listing.id)).one()
            return product, listing

        eligible_product, _eligible_listing = _create_product(f"eligible-{marker}", "Eligible Example Jacket")
        hidden_product, _hidden_listing = _create_product(f"hidden-{marker}", "Hidden Example Jacket")
        unavailable_product, unavailable_listing = _create_product(f"unavailable-{marker}", "Unavailable Example Jacket")
        _fallback_product, _fallback_listing = _create_product(f"fallback-{marker}", "Fallback Example Jacket")

        hidden_product.visibility_status = "hidden"
        unavailable_listing.orderability_status = "unavailable"
        unavailable_listing.status_reason = "source_removed"
        db.flush()

        service_query = ProductQueryService(db)
        monkeypatch.setattr(service_query, "_is_pricing_example_candidate", lambda _payload: True)
        payload = service_query.get_pricing_example_payload(product_id=int(eligible_product.id))

        assert payload is not None
        assert payload["is_sample"] is False
        assert int(payload["product_id"]) == int(eligible_product.id)
        fallback_payload = service_query.get_pricing_example_payload()
        hidden_payload = service_query.get_pricing_example_payload(product_id=int(hidden_product.id))
        unavailable_payload = service_query.get_pricing_example_payload(product_id=int(unavailable_product.id))
        assert fallback_payload is not None
        assert int(fallback_payload["product_id"]) not in {
            int(hidden_product.id),
            int(unavailable_product.id),
        }
        assert hidden_payload is None
        assert unavailable_payload is None
    finally:
        db.rollback()
        db.close()
