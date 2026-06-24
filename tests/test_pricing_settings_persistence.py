from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import SessionLocal
from app.models import PricingSetting
from app.repositories.catalog_settings import CatalogPricingSettingsRepository


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
        entity, _ = CatalogPricingSettingsRepository(initial_db).get_or_create_default()
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
