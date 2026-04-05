"""Periodic worker that refreshes Bybit FX snapshot into backend cache/storage."""

from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.settings.pricing_service import PricingSettingsService


logger = logging.getLogger("backend.bybit_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _run_refresh_once() -> None:
    db = SessionLocal()
    try:
        service = PricingSettingsService(db)
        response = service.get_settings(refresh_bybit=True)
        logger.info(
            "Bybit refresh done: status=%s buckets=%s warning=%s",
            response.bybit_rate_status,
            len(response.bybit_bucket_rates or []),
            response.bybit_rate_warning or "-",
        )
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Bybit refresh failed: %s", exc)
    finally:
        db.close()


def run_forever() -> None:
    interval_sec = max(30, int(settings.pricing_bybit_worker_interval_sec))
    logger.info("Bybit worker started. interval_sec=%s", interval_sec)
    while True:
        _run_refresh_once()
        time.sleep(interval_sec)


if __name__ == "__main__":
    run_forever()

