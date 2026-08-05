from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Product,
    ProductListing,
    ProductListingMember,
    ProductListingVariant,
    Source,
    SourceSetting,
    Supplier,
    SupplierShippingRate,
)
from app.schemas.admin_site_content import AdminSiteAccessSettingsUpdateRequest
from app.schemas.site import SiteCartQuoteItemRequest
from app.services.catalog.cart_pricing_service import CartPricingService
from app.services.catalog.site_access_service import SiteAccessService
from app.services.settings.pricing_service import PricingSettingsService


def _create_quote_variants(db) -> tuple[int, int, int, int, int]:
    marker = uuid4().hex[:8]
    supplier = Supplier(
        key=f"cart-supplier-{marker}",
        name=f"Cart supplier {marker}",
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
            max_weight_kg=None,
            price_rub=0.0,
        )
    )

    source = Source(
        key=f"cart-source-{marker}",
        name=f"Cart source {marker}",
        base_url=f"https://cart-source-{marker}.example",
        base_url_normalized=f"cart-source-{marker}.example",
    )
    db.add(source)
    db.flush()
    db.add(
        SourceSetting(
            source_id=int(source.id),
            supplier_id=int(supplier.id),
            is_enabled=True,
            is_sync_enabled=True,
            buyout_surcharge_value=1000.0,
            buyout_surcharge_currency="RUB",
        )
    )

    preorder_product = Product(
        availability_mode="by_order",
        visibility_status="visible",
        lifecycle_status="active",
        dedup_status="independent",
        manual_weight_grams=500,
    )
    db.add(preorder_product)
    db.flush()
    preorder_listing = ProductListing(
        source_id=int(source.id),
        external_id=f"preorder-{marker}",
        url=f"https://cart-source-{marker}.example/preorder",
        url_normalized=f"https://cart-source-{marker}.example/preorder",
        host_normalized=f"cart-source-{marker}.example",
        source_title="Cart preorder",
        orderability_status="orderable",
        ingest_mode="sync",
    )
    db.add(preorder_listing)
    db.flush()
    db.add(
        ProductListingMember(
            product_id=int(preorder_product.id),
            listing_id=int(preorder_listing.id),
            membership_kind="owner",
        )
    )
    db.flush()
    preorder_product.primary_listing_id = int(preorder_listing.id)
    first = ProductListingVariant(
        listing_id=int(preorder_listing.id),
        position=1,
        title="Preorder one",
        price_amount=100.0,
        compare_at_price_amount=200.0,
        currency_code="USD",
        pricing_mode="source",
        is_orderable=True,
    )
    second = ProductListingVariant(
        listing_id=int(preorder_listing.id),
        position=2,
        title="Preorder two",
        price_amount=100.0,
        compare_at_price_amount=200.0,
        currency_code="USD",
        pricing_mode="source",
        is_orderable=True,
    )
    db.add_all([first, second])

    in_stock_product = Product(
        availability_mode="in_stock",
        visibility_status="visible",
        lifecycle_status="active",
        dedup_status="independent",
    )
    db.add(in_stock_product)
    db.flush()
    in_stock_listing = ProductListing(
        source_id=int(source.id),
        external_id=f"in-stock-{marker}",
        url=f"https://cart-source-{marker}.example/in-stock",
        url_normalized=f"https://cart-source-{marker}.example/in-stock",
        host_normalized=f"cart-source-{marker}.example",
        source_title="Cart in stock",
        orderability_status="orderable",
        ingest_mode="manual",
    )
    db.add(in_stock_listing)
    db.flush()
    db.add(
        ProductListingMember(
            product_id=int(in_stock_product.id),
            listing_id=int(in_stock_listing.id),
            membership_kind="owner",
        )
    )
    db.flush()
    in_stock_product.primary_listing_id = int(in_stock_listing.id)
    fixed = ProductListingVariant(
        listing_id=int(in_stock_listing.id),
        position=1,
        title="In stock",
        price_amount=5000.0,
        currency_code="RUB",
        pricing_mode="fixed_final_rub",
        is_orderable=True,
    )
    db.add(fixed)
    db.flush()
    return (
        int(preorder_product.id),
        int(in_stock_product.id),
        int(first.id),
        int(second.id),
        int(fixed.id),
    )


def test_cart_quote_applies_preorder_source_surcharge_and_svc_once() -> None:
    db = SessionLocal()
    try:
        pricing, _ = PricingSettingsService(db)._get_or_create_pricing_entity()
        pricing.markup_multiplier = 1.0
        pricing.weight_tolerance = 1.0
        pricing.customs_threshold_eur = 1_000_000.0
        pricing.customs_duty_rate = 0.0
        pricing.payment_fee_rate = 0.0
        pricing.customs_processing_rate = 0.0
        pricing.customs_fixed_rub = 0.0
        pricing.tax_rate = 0.0
        pricing.usdt_to_rub_rate = 100.0
        pricing.usd_to_rub_rate = 100.0
        pricing.eur_to_usd_rate = 1.0
        pricing.eur_to_rub_rate = 100.0
        pricing.usdt_extra_rub = 0.0
        pricing.final_rounding_mode = "none"
        pricing.svc_rules = [
            {"min_rub": 0.0, "max_rub": None, "mode": "fixed_rub", "value": 500.0}
        ]
        preorder_product_id, in_stock_product_id, first_id, second_id, fixed_id = _create_quote_variants(db)
        db.flush()

        quote = CartPricingService(db).quote(
            [
                SiteCartQuoteItemRequest(product_id=preorder_product_id, variant_id=first_id, quantity=1),
                SiteCartQuoteItemRequest(product_id=preorder_product_id, variant_id=second_id, quantity=1),
                SiteCartQuoteItemRequest(product_id=in_stock_product_id, variant_id=fixed_id, quantity=1),
            ]
        )

        assert [item.final_line_total_rub for item in quote.items] == [
            11500.0,
            10000.0,
            5000.0,
        ]
        assert [item.original_line_total_rub for item in quote.items] == [
            11500.0,
            11500.0,
            5000.0,
        ]
        assert quote.items[0].old_line_total_rub is not None
        assert quote.items[0].old_line_total_rub > quote.items[0].original_line_total_rub
        assert quote.items[1].old_line_total_rub is not None
        assert quote.items[1].old_line_total_rub > quote.items[1].original_line_total_rub
        assert quote.items[2].old_line_total_rub is None
        # This baseline represents only cart-level SVC and “Выкуп +” consolidation,
        # not product compare-at prices.
        assert quote.svc_progress.preorder_subtotal_rub == 21000.0
        assert quote.svc_progress.applied_amount_rub == 500.0
        assert quote.final_total_rub == quote.total_rub == 26500.0
        assert quote.original_total_rub == 28000.0
        assert quote.original_total_rub > quote.final_total_rub
        assert quote.svc_tiers[0].is_applied is True
        assert quote.svc_tiers[0].amount_rub == 500.0
    finally:
        db.rollback()
        db.close()


def test_cart_quote_total_baseline_ignores_product_compare_at_without_cart_benefit() -> None:
    db = SessionLocal()
    try:
        pricing, _ = PricingSettingsService(db)._get_or_create_pricing_entity()
        pricing.markup_multiplier = 1.0
        pricing.weight_tolerance = 1.0
        pricing.customs_threshold_eur = 1_000_000.0
        pricing.customs_duty_rate = 0.0
        pricing.payment_fee_rate = 0.0
        pricing.customs_processing_rate = 0.0
        pricing.customs_fixed_rub = 0.0
        pricing.tax_rate = 0.0
        pricing.usdt_to_rub_rate = 100.0
        pricing.usd_to_rub_rate = 100.0
        pricing.eur_to_usd_rate = 1.0
        pricing.eur_to_rub_rate = 100.0
        pricing.usdt_extra_rub = 0.0
        pricing.final_rounding_mode = "none"
        pricing.svc_rules = []
        preorder_product_id, _, first_id, _, _ = _create_quote_variants(db)
        db.flush()

        quote = CartPricingService(db).quote([
            SiteCartQuoteItemRequest(product_id=preorder_product_id, variant_id=first_id, quantity=1),
        ])

        assert quote.items[0].final_line_total_rub == quote.final_total_rub
        assert quote.items[0].old_line_total_rub is not None
        assert quote.items[0].old_line_total_rub > quote.final_total_rub
        # The product is discounted, but that must not create a total discount.
        assert quote.original_total_rub == quote.final_total_rub
    finally:
        db.rollback()
        db.close()


def test_empty_cart_quote_exposes_configured_svc_tiers() -> None:
    db = SessionLocal()
    try:
        pricing, _ = PricingSettingsService(db)._get_or_create_pricing_entity()
        pricing.svc_rules = [
            {"min_rub": 0.0, "max_rub": 10_000.0, "mode": "fixed_rub", "value": 900.0},
            {"min_rub": 10_000.0, "max_rub": None, "mode": "fixed_rub", "value": 500.0},
        ]
        db.flush()

        quote = CartPricingService(db).quote([])

        assert quote.items == []
        assert quote.original_total_rub == quote.final_total_rub == quote.total_rub == 0.0
        assert [(tier.min_rub, tier.max_rub) for tier in quote.svc_tiers] == [
            (0.0, 10_000.0),
            (10_000.0, None),
        ]
        assert not any(tier.is_applied for tier in quote.svc_tiers)
        assert quote.svc_progress.preorder_subtotal_rub == 0.0
        assert quote.svc_progress.next_threshold_rub == 10_000.0
    finally:
        db.rollback()
        db.close()


def test_cart_quote_requires_site_access() -> None:
    db = SessionLocal()
    try:
        SiteAccessService(db).update_admin_settings(
            AdminSiteAccessSettingsUpdateRequest(
                enabled=True, title="", description="", password="cart-secret"
            )
        )
        db.commit()
        response = TestClient(app).post(
            "/api/v1/site/cart/quote",
            json={"items": [{"variant_id": 1, "quantity": 1}]},
        )
        assert response.status_code == 401
    finally:
        SiteAccessService(db).update_admin_settings(
            AdminSiteAccessSettingsUpdateRequest(
                enabled=False, title="", description="", password=""
            )
        )
        db.commit()
        db.close()
