from app.schemas.admin_settings import PricingSettingsResponse, PricingSupplierRateResponse, PricingSupplierResponse
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


def test_pricing_example_candidate_rejects_manual_override_payload() -> None:
    product = {
        "source_price": 100.0,
        "final_price": 21500.0,
        "pricing_components": {
            "manual_override": True,
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
        },
    }

    assert ProductQueryService._is_pricing_example_candidate(product) is False


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
    assert payload["source_currency"] == "EUR"
    assert payload["final_price"] is not None
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
