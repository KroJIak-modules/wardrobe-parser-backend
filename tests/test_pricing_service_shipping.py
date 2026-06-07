from uuid import uuid4

import app.api.v1.auth as auth_module
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.parser import PricingSettingsResponse, PricingSupplierRateResponse, PricingSupplierResponse
from app.services.settings.pricing_service import PricingSettingsService


class DummyLimiter:
    def __init__(self) -> None:
        self.failed: dict[str, int] = {}

    def is_limited(self, client_key: str) -> bool:
        return self.failed.get(client_key, 0) >= 2

    def register_failed_attempt(self, client_key: str) -> None:
        self.failed[client_key] = self.failed.get(client_key, 0) + 1


def _build_settings(*, suppliers: list[PricingSupplierResponse]) -> PricingSettingsResponse:
    return PricingSettingsResponse(
        markup_multiplier=1.0,
        weight_tolerance=1.0,
        promo_factor=1.0,
        customs_threshold_eur=200.0,
        customs_threshold_currency="EUR",
        customs_duty_rate=0.15,
        bybit_usdt_to_rub=100.0,
        bybit_extra_rub=0.0,
        eur_to_usd_rate=1.0,
        gbp_to_usd_rate=1.0,
        jpy_to_usd_rate=0.01,
        final_rounding_mode="none",
        payment_fee_rate=0.0,
        customs_processing_rate=0.0,
        customs_fixed_rub=0.0,
        shipping_alt_threshold_eur=300.0,
        tax_rate=0.0,
        dedup_only_available_products=False,
        show_product_description=True,
        svc_rules=[],
        insurance_rules=[],
        service_fee_rules=[],
        bybit_rate_status="skipped",
        bybit_rate_warning=None,
        bybit_bucket_step_usdt=0,
        bybit_bucket_max_usdt=0,
        bybit_bucket_rates=[],
        bybit_worker_auto_enabled=False,
        bybit_worker_interval_sec=0,
        bybit_last_updated_at=None,
        bybit_last_error=None,
        suppliers=suppliers,
        formula_latex="",
        formula_lines=[],
        formula_legend=[],
    )


def test_normalize_shipping_rows_accepts_pydantic_rate_models() -> None:
    rows = [
        PricingSupplierRateResponse(min_kg=0.0, max_kg=0.5, rub=3400.0),
        PricingSupplierRateResponse(min_kg=0.5, max_kg=1.0, rub=3900.0),
    ]

    normalized = PricingSettingsService._normalize_shipping_rows(rows)

    assert normalized == [
        {"min_kg": 0.0, "max_kg": 0.5, "rub": 3400.0},
        {"min_kg": 0.5, "max_kg": 1.0, "rub": 3900.0},
    ]


def test_resolve_supplier_rate_prefers_supplier_ranges_over_region_fallback() -> None:
    settings = _build_settings(
        suppliers=[
            PricingSupplierResponse(
                id=1,
                key="uk",
                name="Великобритания",
                category="main",
                parent_supplier_id=None,
                alt_position=0,
                rate_currency="RUB",
                rates=[PricingSupplierRateResponse(min_kg=0.0, max_kg=0.5, rub=3400.0)],
            )
        ]
    )

    value, meta = PricingSettingsService._resolve_supplier_rate(
        supplier_id=1,
        billable_kg=0.4,
        use_alt_rate=False,
        settings=settings,
    )

    assert value == 3400.0
    assert meta["shipping_rate_mode"] == "range_match"
    assert meta["shipping_tariff_label"] == "0-0.5 кг"


def test_resolve_supplier_rate_reports_missing_tariff_without_fallback() -> None:
    settings = _build_settings(
        suppliers=[
            PricingSupplierResponse(
                id=1,
                key="uk",
                name="Великобритания",
                category="main",
                parent_supplier_id=None,
                alt_position=0,
                rate_currency="RUB",
                rates=[],
            )
        ]
    )

    value, meta = PricingSettingsService._resolve_supplier_rate(
        supplier_id=1,
        billable_kg=0.4,
        use_alt_rate=False,
        settings=settings,
    )

    assert value == 0.0
    assert meta["shipping_rate_mode"] == "missing_tariff"
    assert meta["shipping_tariff_label"] is None


def test_calculate_for_product_uses_supplier_tariff_ranges_for_uk_supplier() -> None:
    settings = _build_settings(
        suppliers=[
            PricingSupplierResponse(
                id=1,
                key="uk",
                name="Великобритания",
                category="main",
                parent_supplier_id=None,
                alt_position=0,
                rate_currency="RUB",
                rates=[PricingSupplierRateResponse(min_kg=0.0, max_kg=0.5, rub=3400.0)],
            )
        ]
    )

    result = PricingSettingsService.calculate_for_product(
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

    assert result.manual_required is False
    assert result.reason is None
    assert result.components["supplier_transport_rub"] == 3400.0
    assert result.components["shipping_rule_label"] == "0-0.5 кг"


def test_calculate_for_product_requires_manual_when_supplier_has_no_tariff() -> None:
    settings = _build_settings(
        suppliers=[
            PricingSupplierResponse(
                id=1,
                key="uk",
                name="Великобритания",
                category="main",
                parent_supplier_id=None,
                alt_position=0,
                rate_currency="RUB",
                rates=[],
            )
        ]
    )

    result = PricingSettingsService.calculate_for_product(
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

    assert result.final_price_rub is None
    assert result.manual_required is True
    assert result.reason == "missing_tariff"
    assert result.components["shipping_rate_mode"] == "missing_tariff"


def test_create_supplier_generates_internal_key_from_id(monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "_login_rate_limiter", DummyLimiter())
    client = TestClient(app)

    login = client.post("/api/v1/auth/login", json={"login": "superadmin", "password": "Q7m2Lx9pRt"})
    assert login.status_code == 200

    created = client.post(
        "/api/v1/settings/pricing/suppliers",
        json={
            "name": f"Тестовый тариф {uuid4().hex[:8]}",
            "category": "main",
            "rate_currency": "RUB",
        },
    )
    assert created.status_code == 200
    payload = created.json()
    supplier_id = int(payload["id"])
    assert payload["key"] == f"supplier-{supplier_id}"

    deleted = client.delete(f"/api/v1/settings/pricing/suppliers/{supplier_id}")
    assert deleted.status_code == 200
