"""Periodic worker that refreshes Bybit FX snapshot into backend cache/storage."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AdminUiSettings
from app.api.v1.jobs import StartSyncRequest, run_sync_all_enabled_sources
from app.services.settings.pricing_service import PricingSettingsService


logger = logging.getLogger("backend.bybit_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _run_refresh_once() -> bool:
    db = SessionLocal()
    try:
        service = PricingSettingsService(db)
        response = service.get_settings(refresh_bybit=True)
        ok = str(response.bybit_rate_status or "").lower() not in {"fallback_stored", "unknown"}
        logger.info(
            "Bybit refresh done: status=%s buckets=%s warning=%s",
            response.bybit_rate_status,
            len(response.bybit_bucket_rates or []),
            response.bybit_rate_warning or "-",
        )
        return ok
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Bybit refresh failed: %s", exc)
        return False
    finally:
        db.close()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _run_auto_sync_once() -> tuple[bool, int]:
    db = SessionLocal()
    try:
        now_utc = _utcnow()
        entity = db.query(AdminUiSettings).filter(AdminUiSettings.id == 1).one_or_none()
        if entity is None:
            entity = AdminUiSettings(id=1, auto_sync_period_minutes=60)
            db.add(entity)
            db.flush()

        period_minutes = max(60, int(getattr(entity, "auto_sync_period_minutes", 60) or 60))
        next_run_at = _to_utc(getattr(entity, "auto_sync_next_run_at", None))
        if next_run_at is None:
            next_run_at = now_utc + timedelta(minutes=period_minutes)
            entity.auto_sync_next_run_at = next_run_at
            entity.auto_sync_last_status = "scheduled"
            entity.auto_sync_last_error = None
            db.commit()
            return True, max(1, int((next_run_at - now_utc).total_seconds()))

        wait_sec = int((next_run_at - now_utc).total_seconds())
        if wait_sec > 0:
            return True, max(1, wait_sec)

        try:
            run_sync_all_enabled_sources(StartSyncRequest(triggered_by="auto"))
            entity.auto_sync_last_started_at = now_utc
            entity.auto_sync_last_status = "started"
            entity.auto_sync_last_error = None
            entity.auto_sync_next_run_at = now_utc + timedelta(minutes=period_minutes)
            db.commit()
            logger.info("Auto-sync started, next run at %s", entity.auto_sync_next_run_at.isoformat())
            return True, max(1, period_minutes * 60)
        except HTTPException as exc:
            detail = str(getattr(exc, "detail", "") or "").strip() or f"http_{exc.status_code}"
            if int(exc.status_code) == 409:
                entity.auto_sync_last_status = "busy"
                entity.auto_sync_last_error = None
                entity.auto_sync_next_run_at = now_utc + timedelta(seconds=30)
                db.commit()
                logger.info("Auto-sync skipped: sync already running. retry in 30s")
                return True, 30
            entity.auto_sync_last_status = "error"
            entity.auto_sync_last_error = detail[:1024]
            entity.auto_sync_last_finished_at = now_utc
            entity.auto_sync_next_run_at = now_utc + timedelta(seconds=60)
            db.commit()
            logger.warning("Auto-sync failed: %s. retry in 60s", detail)
            return False, 60
        except Exception as exc:  # pragma: no cover - runtime guard
            detail = str(exc) or "auto sync failed"
            entity.auto_sync_last_status = "error"
            entity.auto_sync_last_error = detail[:1024]
            entity.auto_sync_last_finished_at = now_utc
            entity.auto_sync_next_run_at = now_utc + timedelta(seconds=60)
            db.commit()
            logger.exception("Auto-sync crashed: %s", detail)
            return False, 60
    finally:
        db.close()


def run_forever() -> None:
    bybit_interval_sec = max(30, int(settings.pricing_bybit_worker_interval_sec))
    bybit_retry_sec = max(10, min(30, bybit_interval_sec // 2))
    logger.info("Bybit+AutoSync worker started. bybit_interval_sec=%s", bybit_interval_sec)
    next_bybit_at = time.time()
    next_auto_sync_at = time.time()
    while True:
        now = time.time()
        if now >= next_bybit_at:
            ok = _run_refresh_once()
            next_bybit_at = now + (bybit_interval_sec if ok else bybit_retry_sec)
            if not ok:
                logger.warning("Bybit refresh not successful. retry in %ss", bybit_retry_sec)
        if now >= next_auto_sync_at:
            _, delay_sec = _run_auto_sync_once()
            next_auto_sync_at = now + max(1, int(delay_sec))
        time.sleep(1)


if __name__ == "__main__":
    run_forever()
