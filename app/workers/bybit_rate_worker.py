"""Periodic worker that refreshes Bybit FX snapshot into backend cache/storage."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AdminUiSettings
from app.services.catalog.filter_assignment_queue import ProductFilterAssignmentQueue
from app.services.catalog.filter_assignment_service import ProductFilterAssignmentService
from app.services.catalog.site_catalog_sort_price_queue import SiteCatalogSortPriceQueue
from app.services.catalog.site_catalog_sort_price_service import SiteCatalogSortPriceService
from app.services.catalog.sync_job_service import SyncJobService
from app.services.settings.pricing_service import PricingSettingsService
from app.services.settings.weight_recalc_queue import WeightRuleRecalcQueue
from app.services.settings.weight_recalc_runtime_service import WeightRecalcRuntimeService
from app.services.settings.weight_rule_service import WeightRuleService


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
            SyncJobService(db).start_job(
                triggered_by_admin_user_id=None,
                source_keys=None,
                trigger_kind="scheduled",
            )
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


def _run_weight_recalc_once(batch_size: int) -> int:
    queue = WeightRuleRecalcQueue()
    product_ids = queue.pop_ready_batch(
        limit=batch_size,
        debounce_sec=int(settings.weight_recalc_worker_debounce_sec),
    )
    if not product_ids:
        return 0

    db = SessionLocal()
    try:
        WeightRecalcRuntimeService(db).mark_running()
        processed = WeightRuleService(db).recalculate_product_ids(product_ids)
        has_more_work = queue.size() > 0
        WeightRecalcRuntimeService(db).advance_after_batch(processed_count=processed, has_more_work=has_more_work)
        logger.info("Weight recalculation done for %s products", processed)
        return processed
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Weight recalculation failed: %s", exc)
        try:
            queue.enqueue_product_ids(product_ids)
            WeightRecalcRuntimeService(db).mark_retryable_error(message=f"{exc.__class__.__name__}: {exc}")
        except Exception as requeue_exc:  # pragma: no cover - worker runtime guard
            logger.exception("Weight recalculation requeue failed: %s", requeue_exc)
            WeightRecalcRuntimeService(db).mark_failed(message=f"{exc.__class__.__name__}: {exc}")
        return 0
    finally:
        db.close()


def _run_filter_assignment_rebuild_once(batch_size: int) -> int:
    db = SessionLocal()
    try:
        revision = ProductFilterAssignmentService(db).rebuild_pending_revision(batch_size=batch_size)
        if revision > 0:
            logger.info("Filter assignment rebuild completed: revision=%s", revision)
        return int(revision)
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Filter assignment rebuild failed: %s", exc)
        return 0
    finally:
        db.close()


def _run_filter_assignment_refresh_once(batch_size: int) -> int:
    queue = ProductFilterAssignmentQueue()
    product_ids = queue.pop_ready_batch(
        limit=batch_size,
        debounce_sec=int(settings.filter_assignment_worker_debounce_sec),
    )
    if not product_ids:
        return 0

    db = SessionLocal()
    try:
        processed = ProductFilterAssignmentService(db).refresh_current_revision_product_ids(product_ids)
        logger.info("Filter assignment refresh done for %s products", processed)
        return processed
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Filter assignment refresh failed: %s", exc)
        try:
            queue.enqueue_product_ids(product_ids)
        except Exception as requeue_exc:  # pragma: no cover - worker runtime guard
            logger.exception("Filter assignment refresh requeue failed: %s", requeue_exc)
        return 0
    finally:
        db.close()


def _run_site_sort_price_rebuild_once(batch_size: int) -> int:
    db = SessionLocal()
    try:
        processed = SiteCatalogSortPriceService(db).refresh_missing_batch(batch_size=batch_size)
        if processed > 0:
            logger.info("Site sort price rebuild done for %s products", processed)
        return processed
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Site sort price rebuild failed: %s", exc)
        return 0
    finally:
        db.close()


def _run_site_sort_price_refresh_once(batch_size: int) -> int:
    queue = SiteCatalogSortPriceQueue()
    product_ids = queue.pop_ready_batch(
        limit=batch_size,
        debounce_sec=int(settings.site_sort_price_worker_debounce_sec),
    )
    if not product_ids:
        return 0

    db = SessionLocal()
    try:
        processed = SiteCatalogSortPriceService(db).refresh_product_ids(product_ids)
        logger.info("Site sort price refresh done for %s products", processed)
        return processed
    except Exception as exc:  # pragma: no cover - worker runtime guard
        logger.exception("Site sort price refresh failed: %s", exc)
        try:
            queue.enqueue_product_ids(product_ids)
        except Exception as requeue_exc:  # pragma: no cover - worker runtime guard
            logger.exception("Site sort price requeue failed: %s", requeue_exc)
        return 0
    finally:
        db.close()


def run_forever() -> None:
    bybit_interval_sec = max(30, int(settings.pricing_bybit_worker_interval_sec))
    bybit_retry_sec = max(10, min(30, bybit_interval_sec // 2))
    weight_recalc_idle_sec = max(1, int(settings.weight_recalc_worker_idle_sec))
    weight_recalc_batch_size = max(1, int(settings.weight_recalc_worker_batch_size))
    weight_recalc_debounce_sec = max(0, int(settings.weight_recalc_worker_debounce_sec))
    filter_assignment_idle_sec = max(1, int(settings.filter_assignment_worker_idle_sec))
    filter_assignment_batch_size = max(1, int(settings.filter_assignment_worker_batch_size))
    filter_assignment_debounce_sec = max(0, int(settings.filter_assignment_worker_debounce_sec))
    site_sort_price_idle_sec = max(1, int(settings.site_sort_price_worker_idle_sec))
    site_sort_price_batch_size = max(1, int(settings.site_sort_price_worker_batch_size))
    site_sort_price_debounce_sec = max(0, int(settings.site_sort_price_worker_debounce_sec))
    logger.info(
        "Bybit+AutoSync worker started. bybit_interval_sec=%s weight_recalc_idle_sec=%s weight_recalc_batch_size=%s weight_recalc_debounce_sec=%s filter_assignment_idle_sec=%s filter_assignment_batch_size=%s filter_assignment_debounce_sec=%s site_sort_price_idle_sec=%s site_sort_price_batch_size=%s site_sort_price_debounce_sec=%s",
        bybit_interval_sec,
        weight_recalc_idle_sec,
        weight_recalc_batch_size,
        weight_recalc_debounce_sec,
        filter_assignment_idle_sec,
        filter_assignment_batch_size,
        filter_assignment_debounce_sec,
        site_sort_price_idle_sec,
        site_sort_price_batch_size,
        site_sort_price_debounce_sec,
    )
    next_bybit_at = time.time()
    next_auto_sync_at = time.time()
    next_weight_recalc_at = time.time()
    next_filter_assignment_at = time.time()
    next_site_sort_price_at = time.time()
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
        if now >= next_weight_recalc_at:
            processed = _run_weight_recalc_once(weight_recalc_batch_size)
            next_weight_recalc_at = now + (1 if processed > 0 else weight_recalc_idle_sec)
        if now >= next_filter_assignment_at:
            rebuilt_revision = _run_filter_assignment_rebuild_once(filter_assignment_batch_size)
            if rebuilt_revision > 0:
                next_filter_assignment_at = now + 1
            else:
                processed = _run_filter_assignment_refresh_once(filter_assignment_batch_size)
                next_filter_assignment_at = now + (1 if processed > 0 else filter_assignment_idle_sec)
        if now >= next_site_sort_price_at:
            rebuilt = _run_site_sort_price_rebuild_once(site_sort_price_batch_size)
            if rebuilt > 0:
                next_site_sort_price_at = now + 1
            else:
                processed = _run_site_sort_price_refresh_once(site_sort_price_batch_size)
                next_site_sort_price_at = now + (1 if processed > 0 else site_sort_price_idle_sec)
        time.sleep(1)


if __name__ == "__main__":
    run_forever()
