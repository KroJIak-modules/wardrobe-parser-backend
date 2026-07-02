from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import SessionLocal
from app.models import PricingSetting
from app.services.settings.pricing_service import PricingSettingsService


def test_pricing_settings_persists_bybit_runtime_fields() -> None:
    marker = uuid4().hex[:12]
    expected_bucket_rates = [
        {
            "bucket_usdt": 50.0,
            "rate_rub_per_usdt": 101.25,
            "pay_rub": 5062.5,
            "source": f"test-{marker}",
        }
    ]
    expected_updated_at = datetime(2026, 6, 23, 3, 45, tzinfo=timezone.utc)
    expected_error = f"test-error-{marker}"

    initial_db = SessionLocal()
    original_bucket_rates: list[dict] | None = None
    original_updated_at: datetime | None = None
    original_error: str | None = None
    entity_id = 1
    try:
        entity, _ = PricingSettingsService(initial_db)._get_or_create_pricing_entity()
        entity_id = int(entity.id)
        original_bucket_rates = list(getattr(entity, "bybit_bucket_rates", None) or [])
        original_updated_at = getattr(entity, "bybit_last_updated_at", None)
        original_error = getattr(entity, "bybit_last_error", None)

        entity.bybit_bucket_rates = expected_bucket_rates
        entity.bybit_last_updated_at = expected_updated_at
        entity.bybit_last_error = expected_error
        initial_db.commit()
    finally:
        initial_db.close()

    verify_db = SessionLocal()
    try:
        stored = verify_db.query(PricingSetting).filter(PricingSetting.id == entity_id).one()
        assert stored.bybit_bucket_rates == expected_bucket_rates
        assert stored.bybit_last_updated_at == expected_updated_at
        assert stored.bybit_last_error == expected_error
    finally:
        verify_db.close()

    restore_db = SessionLocal()
    try:
        restore = restore_db.query(PricingSetting).filter(PricingSetting.id == entity_id).one()
        restore.bybit_bucket_rates = original_bucket_rates or []
        restore.bybit_last_updated_at = original_updated_at
        restore.bybit_last_error = original_error
        restore_db.commit()
    finally:
        restore_db.close()


def test_pricing_settings_persists_svc_rules() -> None:
    marker = uuid4().hex[:12]
    expected_rules = [
        {
            "min_rub": 10000.0,
            "max_rub": 20000.0,
            "mode": "fixed_rub",
            "value": 2500.0,
        },
        {
            "min_rub": 20000.0,
            "max_rub": None,
            "mode": "percent",
            "value": 0.15,
        },
    ]

    initial_db = SessionLocal()
    original_rules: list[dict] | None = None
    entity_id = 1
    try:
        entity, _ = PricingSettingsService(initial_db)._get_or_create_pricing_entity()
        entity_id = int(entity.id)
        original_rules = list(getattr(entity, "svc_rules", None) or [])
        entity.svc_rules = expected_rules
        initial_db.commit()
    finally:
        initial_db.close()

    verify_db = SessionLocal()
    try:
        stored = verify_db.query(PricingSetting).filter(PricingSetting.id == entity_id).one()
        assert stored.svc_rules == expected_rules, marker
    finally:
        verify_db.close()

    restore_db = SessionLocal()
    try:
        restore = restore_db.query(PricingSetting).filter(PricingSetting.id == entity_id).one()
        restore.svc_rules = original_rules or []
        restore_db.commit()
    finally:
        restore_db.close()


def test_pricing_settings_persists_conversion_coefficients() -> None:
    initial_db = SessionLocal()
    original_values: tuple[float, float, float] | None = None
    entity_id = 1
    try:
        entity, _ = PricingSettingsService(initial_db)._get_or_create_pricing_entity()
        entity_id = int(entity.id)
        original_values = (
            float(getattr(entity, "eur_to_usd_rate", 0.0) or 0.0),
            float(getattr(entity, "gbp_to_usd_rate", 0.0) or 0.0),
            float(getattr(entity, "jpy_to_usd_rate", 0.0) or 0.0),
        )
        entity.eur_to_usd_rate = 1.23
        entity.gbp_to_usd_rate = 1.41
        entity.jpy_to_usd_rate = 0.0067
        initial_db.commit()
    finally:
        initial_db.close()

    verify_db = SessionLocal()
    try:
        stored = verify_db.query(PricingSetting).filter(PricingSetting.id == entity_id).one()
        assert float(stored.eur_to_usd_rate) == 1.23
        assert float(stored.gbp_to_usd_rate) == 1.41
        assert float(stored.jpy_to_usd_rate) == 0.0067
    finally:
        verify_db.close()

    restore_db = SessionLocal()
    try:
        restore = restore_db.query(PricingSetting).filter(PricingSetting.id == entity_id).one()
        restore.eur_to_usd_rate = original_values[0] if original_values is not None else 1.18
        restore.gbp_to_usd_rate = original_values[1] if original_values is not None else 1.4
        restore.jpy_to_usd_rate = original_values[2] if original_values is not None else 0.0065
        restore_db.commit()
    finally:
        restore_db.close()
