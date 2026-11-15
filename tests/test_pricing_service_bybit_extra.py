from __future__ import annotations

from app.schemas.admin_settings import (
    PricingSettingsResponse,
    PricingSupplierRateResponse,
    PricingSupplierResponse,
)
from app.services.settings.pricing_service import PricingSettingsService


def _build_settings(*, usdt_to_rub_rate: float, usdt_extra_rub: float) -> PricingSettingsResponse:
    return PricingSettingsResponse(
        markup_multiplier=1.0,
        weight_tolerance=1.0,
        customs_threshold_eur=200.0,
        shipping_alt_threshold_eur=300.0,
        customs_duty_rate=0.15,
        eur_to_usd_rate=1.0,
        gbp_to_usd_rate=1.4,
        jpy_to_usd_rate=0.0065,
        eur_to_rub_rate=100.0,
        usd_to_rub_rate=100.0,
        usdt_to_rub_rate=usdt_to_rub_rate,
        usdt_extra_rub=usdt_extra_rub,
        final_rounding_mode="none",
        payment_fee_rate=0.0,
        customs_processing_rate=0.0,
        customs_fixed_rub=0.0,
        tax_rate=0.0,
        bybit_rate_status="skipped",
        bybit_rate_warning=None,
        bybit_bucket_step_usdt=0,
        bybit_bucket_max_usdt=0,
        bybit_bucket_rates=[],
        bybit_worker_auto_enabled=False,
        bybit_worker_interval_sec=0,
        bybit_last_updated_at=None,
        bybit_last_error=None,
        svc_rules=[],
        suppliers=[
            PricingSupplierResponse(
                id=1,
                key="main",
                name="Основной",
                provider_kind="main",
                parent_supplier_id=None,
                rate_currency="RUB",
                is_enabled=True,
                rates=[PricingSupplierRateResponse(min_kg=0.0, max_kg=None, rub=0.0)],
            )
        ],
        formula_latex="",
        formula_lines=[],
        formula_legend=[],
    )


def _compute(settings: PricingSettingsResponse):
    return PricingSettingsService.calculate_for_product(
        source_price=100.0,
        source_currency="USD",
        weight_grams=500.0,
        supplier_id=1,
        promo_factor=None,
        promo_only_no_discount=None,
        buyout_surcharge_value=None,
        buyout_surcharge_currency=None,
        variants=[],
        settings=settings,
    )


def test_bybit_extra_rub_changes_final_price() -> None:
    without_extra = _compute(_build_settings(usdt_to_rub_rate=100.0, usdt_extra_rub=0.0))
    with_extra = _compute(_build_settings(usdt_to_rub_rate=100.0, usdt_extra_rub=5.0))

    assert without_extra.manual_required is False
    assert with_extra.manual_required is False
    assert with_extra.final_price_rub is not None
    assert without_extra.final_price_rub is not None
    assert with_extra.final_price_rub > without_extra.final_price_rub


def test_price_uses_bbr_plus_bex_rate() -> None:
    result = _compute(_build_settings(usdt_to_rub_rate=100.0, usdt_extra_rub=5.0))

    assert result.components["usdt_extra_rub"] == 5.0
    assert result.components["bybit_bucket_rate_rub"] == 100.0
    assert result.components["effective_usdt_to_rub"] == 105.0
    assert result.components["source_price_rub"] == 100.0 * 105.0


def test_effective_eur_rate_includes_bybit_extra_rub() -> None:
    result = _compute(_build_settings(usdt_to_rub_rate=100.0, usdt_extra_rub=5.0))

    # E2U * BFX: EUR -> RUB conversion must apply the same markup as the USD path.
    assert result.components["eur_to_rub_effective"] == 1.0 * 105.0
