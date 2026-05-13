"""Backend sync orchestration over parser-service /sync/jobs contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock, Thread
from time import sleep
from typing import Any

import logging
import requests
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import ParserProduct, ParserSource, SyncAppliedBatch, SyncJobRuntime


router = APIRouter(tags=["jobs"])
LOGGER = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 2.0


class StartSyncRequest(BaseModel):
    triggered_by: str | None = "manual"
    sources: list[str] | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except Exception:
        return None


def _service_sync_base() -> str:
    return f"{settings.service_base_url.rstrip('/')}/api/v1/sync"


@dataclass
class AggregateJob:
    job_id: str
    service_job_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_sources: int = 0
    processed_sources: int = 0
    expected_db_upserts: int = 0
    db_upserts_done: int = 0
    failed_products: int = 0
    current_source_name: str | None = None
    current_source_index: int = 0
    current_stage: str | None = None
    can_cancel: bool = True
    products_success: int = 0
    products_error: int = 0
    event_cursor: int = 0
    applied_batch_ids: set[str] | None = None


class JobsState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: AggregateJob | None = None

    def get_latest(self) -> AggregateJob | None:
        with self._lock:
            return self._latest

    def set_latest(self, job: AggregateJob) -> None:
        with self._lock:
            self._latest = job


STATE = JobsState()


def _persist_runtime(job: AggregateJob, *, error_message: str | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(SyncJobRuntime).filter(SyncJobRuntime.aggregate_job_id == job.job_id).first()
        if row is None:
            row = SyncJobRuntime(
                aggregate_job_id=job.job_id,
                service_job_id=job.service_job_id,
                status=job.status,
                created_at=job.created_at,
            )
            db.add(row)
        row.service_job_id = job.service_job_id
        row.status = job.status
        row.created_at = job.created_at
        row.started_at = job.started_at
        row.completed_at = job.completed_at
        row.total_sources = int(job.total_sources or 0)
        row.processed_sources = int(job.processed_sources or 0)
        row.expected_db_upserts = int(job.expected_db_upserts or 0)
        row.db_upserts_done = int(job.db_upserts_done or 0)
        row.failed_products = int(job.failed_products or 0)
        row.current_source_name = job.current_source_name
        row.current_source_index = int(job.current_source_index or 0)
        row.current_stage = job.current_stage
        row.products_success = int(job.products_success or 0)
        row.products_error = int(job.products_error or 0)
        row.event_cursor = int(job.event_cursor or 0)
        row.can_cancel = bool(job.can_cancel)
        row.error_message = error_message
        db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to persist sync runtime state job_id=%s", job.job_id)
    finally:
        db.close()


def _load_latest_runtime() -> AggregateJob | None:
    db = SessionLocal()
    try:
        row = db.query(SyncJobRuntime).order_by(SyncJobRuntime.created_at.desc(), SyncJobRuntime.id.desc()).first()
        if row is None:
            return None
        return AggregateJob(
            job_id=row.aggregate_job_id,
            service_job_id=row.service_job_id,
            status=row.status,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            total_sources=row.total_sources or 0,
            processed_sources=row.processed_sources or 0,
            expected_db_upserts=row.expected_db_upserts or 0,
            db_upserts_done=row.db_upserts_done or 0,
            failed_products=row.failed_products or 0,
            current_source_name=row.current_source_name,
            current_source_index=row.current_source_index or 0,
            current_stage=row.current_stage,
            can_cancel=bool(row.can_cancel),
            products_success=row.products_success or 0,
            products_error=row.products_error or 0,
            event_cursor=row.event_cursor or 0,
            applied_batch_ids=set(),
        )
    finally:
        db.close()


def mark_interrupted_jobs_on_startup() -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(SyncJobRuntime)
            .filter(SyncJobRuntime.status.in_(["queued", "in_progress"]))
            .all()
        )
        now = _utcnow()
        for row in rows:
            row.status = "failed"
            row.can_cancel = False
            row.completed_at = now
            row.current_stage = "Прервано из-за перезапуска backend"
            row.error_message = "backend_restart_interrupted_job"
        db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to mark interrupted sync jobs on startup")
    finally:
        db.close()


def _map_latest_response(job: AggregateJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    total = max(1, job.total_sources)
    progress = (job.processed_sources / total) * 100.0
    products_progress = (job.db_upserts_done / max(1, job.expected_db_upserts)) * 100.0 if job.expected_db_upserts > 0 else progress
    return {
        "job_id": job.job_id,
        "status": job.status,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
        "next_scheduled_at": None,
        "total_products": None,
        "new_products": 0,
        "updated_products": 0,
        "new_images": 0,
        "total_sources": job.total_sources,
        "processed_sources": job.processed_sources,
        "progress_percent": round(progress, 2),
        "processed_products": job.db_upserts_done,
        "expected_products": job.expected_db_upserts,
        "failed_products": job.failed_products,
        "products_progress_percent": round(products_progress, 2),
        "current_source_name": job.current_source_name,
        "current_source_parser_type": "service_rework",
        "current_source_index": job.current_source_index,
        "current_stage": job.current_stage,
        "current_source_processed_products": job.products_success,
        "current_source_total_products": max(job.products_success + job.products_error, 0),
        "current_product_title": None,
        "site_products_total": 0,
        "can_cancel": job.can_cancel,
        "sync_period_minutes": 0,
    }


def _find_backend_source(db, source_key: str) -> ParserSource | None:
    norm = source_key.strip().lower()
    rows = db.query(ParserSource).filter(ParserSource.deleted_at.is_(None)).all()
    for row in rows:
        url = str(getattr(row, "url", "") or "").strip().lower()
        name = str(getattr(row, "name", "") or "").strip().lower()
        if norm and (norm in url or norm == name):
            return row
    return None


def _update_source_sync_telemetry(
    source_key: str,
    *,
    status: str | None = None,
    finished_at: datetime | None = None,
    duration_sec: int | None = None,
) -> None:
    db = SessionLocal()
    try:
        source = _find_backend_source(db, source_key)
        if source is None:
            return
        if finished_at is not None:
            source.last_sync_at = finished_at
        if duration_sec is not None:
            source.last_sync_duration_sec = max(0, int(duration_sec))
        if status is not None:
            source.last_sync_status = str(status).strip().lower()[:32] or None
        db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to update source sync telemetry source_key=%s", source_key)
    finally:
        db.close()


def _derive_status(variants: Any) -> str:
    if not isinstance(variants, list) or not variants:
        return "available"
    for variant in variants:
        if isinstance(variant, dict) and bool(variant.get("available")):
            return "available"
    return "out_of_stock"


def _upsert_products_from_items(source_key: str, items: list[dict[str, Any]]) -> tuple[int, int]:
    expected = len(items)
    if expected == 0:
        return (0, 0)
    db = SessionLocal()
    upserts = 0
    try:
        source = _find_backend_source(db, source_key)
        if source is None:
            LOGGER.warning("jobs_ingest: source not found for key=%s", source_key)
            return (expected, 0)
        for item in items:
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("external_id") or "").strip() or None
            canonical_url = str(item.get("canonical_url") or "").strip() or None
            source_product_url = str(item.get("source_product_url") or item.get("url") or "").strip()
            handle = str(item.get("handle") or "").strip()
            if not handle:
                if source_product_url:
                    handle = source_product_url.rsplit("/", 1)[-1][:200]
            if not handle:
                continue
            query = (
                db.query(ParserProduct)
                .filter(ParserProduct.deleted_at.is_(None))
                .filter(ParserProduct.source_id == int(source.id))
            )
            row = None
            if external_id:
                row = query.filter(ParserProduct.source_external_id == external_id).first()
            if row is None and canonical_url:
                row = query.filter(ParserProduct.canonical_url == canonical_url).first()
            if row is None:
                row = query.filter(ParserProduct.handle == handle).first()
            if row is None:
                row = ParserProduct(
                    source_id=int(source.id),
                    source_external_id=external_id,
                    canonical_url=canonical_url,
                    handle=handle,
                    title=str(item.get("title") or handle),
                    url=source_product_url,
                )
                db.add(row)
            else:
                if external_id:
                    row.source_external_id = external_id
                if canonical_url:
                    row.canonical_url = canonical_url
            incoming_title = str(item.get("title") or row.title or handle)
            incoming_description = item.get("description")
            row.vendor = str(item.get("vendor") or item.get("brand") or "").strip() or None
            row.product_type = str(item.get("product_type") or item.get("category") or "").strip() or None
            row.url = source_product_url or str(row.url or "").strip()
            row.price = _safe_float(item.get("price"))
            row.currency = str(item.get("currency") or row.currency or "USD").strip().upper()[:3] or "USD"
            images = item.get("images") if isinstance(item.get("images"), list) else []
            incoming_image_urls = [str(url).strip() for url in images if str(url).strip()]
            row.variants = item.get("variants") if isinstance(item.get("variants"), list) else []
            parsed_weight = _safe_float(item.get("weight_grams"))
            row.weight_grams = parsed_weight if parsed_weight is not None else row.weight_grams
            validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
            validation_errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
            current_status = str(getattr(row, "status", "") or "").strip().lower()
            title_sync_locked = bool(getattr(row, "title_sync_locked", False))
            description_sync_locked = bool(getattr(row, "description_sync_locked", False))
            images_sync_locked = bool(getattr(row, "images_sync_locked", False))

            if not title_sync_locked:
                row.title = incoming_title
            if not description_sync_locked:
                row.description = incoming_description
            if not images_sync_locked:
                row.image_urls = incoming_image_urls
                row.image_count = len(incoming_image_urls)

            # Manual moderation states must survive sync.
            if current_status in {"hidden", "unavailable"}:
                pass
            elif validation_errors:
                row.status = "unavailable"
            else:
                raw_availability = str(item.get("raw_availability") or "").strip().lower()
                if raw_availability in {"sold_out", "out_of_stock"}:
                    row.status = "out_of_stock"
                elif raw_availability in {"unavailable"}:
                    row.status = "unavailable"
                else:
                    row.status = _derive_status(row.variants)
            upserts += 1
        db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("jobs_ingest failed for source=%s expected=%s", source_key, expected)
        upserts = 0
    finally:
        db.close()
    return (expected, upserts)


def _is_batch_applied(service_job_id: str, batch_id: str) -> bool:
    db = SessionLocal()
    try:
        row = (
            db.query(SyncAppliedBatch.id)
            .filter(SyncAppliedBatch.service_job_id == service_job_id)
            .filter(SyncAppliedBatch.batch_id == batch_id)
            .first()
        )
        return row is not None
    finally:
        db.close()


def _mark_batch_applied(aggregate_job_id: str, service_job_id: str, batch_id: str, source_key: str | None) -> None:
    db = SessionLocal()
    try:
        exists = (
            db.query(SyncAppliedBatch.id)
            .filter(SyncAppliedBatch.service_job_id == service_job_id)
            .filter(SyncAppliedBatch.batch_id == batch_id)
            .first()
        )
        if exists:
            return
        db.add(
            SyncAppliedBatch(
                aggregate_job_id=aggregate_job_id,
                service_job_id=service_job_id,
                batch_id=batch_id,
                source_key=source_key,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _poll_and_apply(agg_job: AggregateJob) -> None:
    base = _service_sync_base()
    agg_job.started_at = _utcnow()
    agg_job.status = "in_progress"
    _persist_runtime(agg_job)

    terminal = {"completed", "failed", "cancelled"}
    if agg_job.applied_batch_ids is None:
        agg_job.applied_batch_ids = set()
    source_started_at: dict[str, datetime] = {}
    while True:
        latest = STATE.get_latest()
        if latest is None or latest.job_id != agg_job.job_id:
            return

        try:
            status_res = requests.get(f"{base}/jobs/{agg_job.service_job_id}", timeout=(5, 30))
            status_res.raise_for_status()
            payload = status_res.json() if isinstance(status_res.json(), dict) else {}
        except Exception:
            payload = {}

        agg_job.current_source_name = str(payload.get("current_source_name") or "").strip() or agg_job.current_source_name
        agg_job.current_source_index = int(payload.get("current_source_index") or agg_job.current_source_index or 0)
        agg_job.total_sources = int(payload.get("total_sources") or agg_job.total_sources or 0)
        agg_job.current_stage = str(payload.get("current_stage") or "").strip() or agg_job.current_stage
        agg_job.products_success = int(payload.get("products_success") or agg_job.products_success or 0)
        agg_job.products_error = int(payload.get("products_error") or agg_job.products_error or 0)
        agg_job.status = str(payload.get("status") or agg_job.status).strip().lower()
        _persist_runtime(agg_job)

        try:
            events_res = requests.get(
                f"{base}/jobs/{agg_job.service_job_id}/events",
                params={"cursor": agg_job.event_cursor, "limit": 250},
                timeout=(5, 30),
            )
            events_res.raise_for_status()
            events_payload = events_res.json() if isinstance(events_res.json(), dict) else {}
            events = events_payload.get("items") if isinstance(events_payload.get("items"), list) else []
            next_cursor = int(events_payload.get("next_cursor") or agg_job.event_cursor)
        except Exception:
            events = []
            next_cursor = agg_job.event_cursor

        for evt in events:
            if not isinstance(evt, dict):
                continue
            evt_type = str(evt.get("type") or "").strip()
            evt_payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
            if evt_type in {"source_progress", "source_started", "source_finished", "job_finished", "job_failed", "job_cancelled"}:
                strategy_value = str(evt_payload.get("strategy") or "").strip()
                stage_label = str(evt_payload.get("stage_label") or "").strip()
                if strategy_value:
                    agg_job.current_stage = f"{strategy_value}: {stage_label or evt_type}"
                elif stage_label:
                    agg_job.current_stage = stage_label
                else:
                    agg_job.current_stage = evt_type
                agg_job.current_source_name = str(evt_payload.get("source_key") or agg_job.current_source_name or "").strip() or agg_job.current_source_name
                agg_job.current_source_index = int(evt_payload.get("source_index") or agg_job.current_source_index or 0)
            if evt_type == "source_started":
                source_key = str(evt_payload.get("source_key") or "").strip()
                if source_key:
                    source_started_at[source_key] = _utcnow()
            if evt_type == "source_finished":
                source_key = str(evt_payload.get("source_key") or "").strip()
                source_status = str(evt_payload.get("status") or "").strip().lower() or "completed"
                finished_at = _utcnow()
                started_at = source_started_at.get(source_key)
                duration_sec = None
                if started_at is not None:
                    duration_sec = int(max(0.0, (finished_at - started_at).total_seconds()))
                if source_key:
                    _update_source_sync_telemetry(
                        source_key,
                        status=source_status,
                        finished_at=finished_at,
                        duration_sec=duration_sec,
                    )
            if str(evt.get("type") or "") != "product_batch":
                continue
            event_payload = evt_payload
            batch_id = str(event_payload.get("batch_id") or "").strip()
            if batch_id and batch_id in agg_job.applied_batch_ids:
                continue
            if batch_id and _is_batch_applied(agg_job.service_job_id, batch_id):
                agg_job.applied_batch_ids.add(batch_id)
                continue
            source_key = str(event_payload.get("source_key") or "").strip()
            items = event_payload.get("items") if isinstance(event_payload.get("items"), list) else []
            cast_items = [item for item in items if isinstance(item, dict)]
            expected, applied = _upsert_products_from_items(source_key=source_key, items=cast_items)
            if source_key:
                finished_at = _utcnow()
                duration_sec = None
                started_at = source_started_at.get(source_key)
                if started_at is not None:
                    duration_sec = int(max(0.0, (finished_at - started_at).total_seconds()))
                _update_source_sync_telemetry(
                    source_key,
                    status="completed",
                    finished_at=finished_at,
                    duration_sec=duration_sec,
                )
            agg_job.expected_db_upserts += expected
            agg_job.db_upserts_done += applied
            agg_job.failed_products += max(0, expected - applied)
            if batch_id:
                try:
                    _mark_batch_applied(
                        aggregate_job_id=agg_job.job_id,
                        service_job_id=agg_job.service_job_id,
                        batch_id=batch_id,
                        source_key=source_key or None,
                    )
                    agg_job.applied_batch_ids.add(batch_id)
                except Exception:
                    LOGGER.exception("failed to mark batch as applied service_job_id=%s batch_id=%s", agg_job.service_job_id, batch_id)

        agg_job.event_cursor = max(agg_job.event_cursor, next_cursor)
        agg_job.processed_sources = min(agg_job.total_sources, max(agg_job.processed_sources, agg_job.current_source_index))
        _persist_runtime(agg_job)

        if agg_job.status in terminal:
            try:
                _backfill_source_sync_telemetry_from_events(agg_job.service_job_id)
            except Exception:
                LOGGER.exception("failed to backfill source sync telemetry service_job_id=%s", agg_job.service_job_id)
            agg_job.completed_at = _utcnow()
            agg_job.can_cancel = False
            if not agg_job.current_stage:
                agg_job.current_stage = agg_job.status
            _persist_runtime(agg_job)
            return

        sleep(POLL_INTERVAL_SEC)


def _parse_event_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        txt = str(raw).strip()
        if not txt:
            return None
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"
        return datetime.fromisoformat(txt)
    except Exception:
        return None


def _backfill_source_sync_telemetry_from_events(service_job_id: str) -> None:
    base = _service_sync_base()
    cursor = 0
    started: dict[str, datetime] = {}
    while True:
        res = requests.get(
            f"{base}/jobs/{service_job_id}/events",
            params={"cursor": cursor, "limit": 250},
            timeout=(5, 30),
        )
        res.raise_for_status()
        payload = res.json() if isinstance(res.json(), dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        next_cursor = int(payload.get("next_cursor") or cursor)
        for evt in items:
            if not isinstance(evt, dict):
                continue
            evt_type = str(evt.get("type") or "").strip()
            evt_payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}
            source_key = str(evt_payload.get("source_key") or "").strip()
            if not source_key:
                continue
            ts = _parse_event_ts(evt.get("ts")) or _utcnow()
            if evt_type == "source_started":
                started[source_key] = ts
            elif evt_type == "source_finished":
                status = str(evt_payload.get("status") or "").strip().lower() or "completed"
                duration_sec = None
                if source_key in started:
                    duration_sec = int(max(0.0, (ts - started[source_key]).total_seconds()))
                _update_source_sync_telemetry(
                    source_key,
                    status=status,
                    finished_at=ts,
                    duration_sec=duration_sec,
                )
        if next_cursor <= cursor:
            break
        cursor = next_cursor


@router.post("/jobs")
def run_sync_all_enabled_sources(payload: StartSyncRequest | None = None) -> dict[str, Any]:
    latest = STATE.get_latest()
    if latest and latest.status in {"pending", "queued", "in_progress"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sync already in progress")

    base = _service_sync_base()
    request_payload: dict[str, Any] = {
        "triggered_by": str((payload.triggered_by if payload else None) or "manual"),
        "dry_run": False,
    }
    source_keys = [str(s).strip() for s in ((payload.sources if payload else None) or []) if str(s).strip()]
    if source_keys:
        request_payload["sources"] = source_keys
    try:
        response = requests.post(f"{base}/jobs", json=request_payload, timeout=(10, 30))
        response.raise_for_status()
        body = response.json() if isinstance(response.json(), dict) else {}
    except requests.HTTPError as exc:
        detail = f"failed to start sync job: {exc.response.status_code if exc.response is not None else 'http_error'}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="failed to start sync job") from exc

    service_job_id = str(body.get("job_id") or "").strip()
    if not service_job_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="empty service job id")

    created_at = _utcnow()
    agg = AggregateJob(
        job_id=f"agg-{int(created_at.timestamp())}",
        service_job_id=service_job_id,
        status="queued",
        created_at=created_at,
    )
    STATE.set_latest(agg)
    _persist_runtime(agg)
    Thread(target=_poll_and_apply, args=(agg,), daemon=True).start()
    return {"ok": True, "job_id": agg.job_id}


@router.get("/jobs/latest")
def jobs_latest() -> dict[str, Any] | None:
    latest = STATE.get_latest()
    if latest is None:
        latest = _load_latest_runtime()
    return _map_latest_response(latest)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    latest = STATE.get_latest()
    if latest is None or latest.job_id != job_id:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    if latest.status not in {"queued", "in_progress"}:
        return {"ok": False, "message": "already finished"}

    base = _service_sync_base()
    try:
        requests.post(f"{base}/jobs/{latest.service_job_id}/cancel", timeout=(5, 15)).raise_for_status()
    except Exception:
        pass

    latest.status = "cancelled"
    latest.completed_at = _utcnow()
    latest.can_cancel = False
    latest.current_stage = "cancelled"
    _persist_runtime(latest)
    return {"ok": True, "job_id": latest.job_id, "status": latest.status}
