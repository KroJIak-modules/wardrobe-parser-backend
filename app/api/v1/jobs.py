"""Backend sync orchestrator over parser-service /sync API.

Runs one service job per source with sync_enabled=true and exposes legacy
/admin-friendly /jobs endpoints consumed by frontend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, status
import logging

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import ParserProduct, ParserSource


router = APIRouter(tags=["jobs"])
LOGGER = logging.getLogger(__name__)

SOURCE_JOB_TIMEOUT_SEC = 20 * 60
POLL_INTERVAL_SEC = 2.0


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


def _load_sync_enabled_sources() -> list[dict[str, Any]]:
    base = _service_sync_base()
    res = requests.get(f"{base}/sources", timeout=(5, 30))
    res.raise_for_status()
    items = res.json()
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        if not bool(item.get("sync_enabled", True)):
            continue
        out.append(item)
    return out


@dataclass
class AggregateJob:
    job_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_sources: int = 0
    processed_sources: int = 0
    expected_products: int = 0
    processed_products: int = 0
    expected_db_upserts: int = 0
    db_upserts_done: int = 0
    failed_products: int = 0
    current_source_name: str | None = None
    current_source_index: int = 0
    current_stage: str | None = None
    can_cancel: bool = True
    source_jobs: dict[str, str] = field(default_factory=dict)


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
        "expected_products": job.expected_db_upserts if job.expected_db_upserts > 0 else job.expected_products,
        "failed_products": job.failed_products,
        "products_progress_percent": round(products_progress, 2),
        "current_source_name": job.current_source_name,
        "current_source_parser_type": "service_rework",
        "current_source_index": job.current_source_index,
        "current_stage": job.current_stage,
        "current_source_processed_products": 0,
        "current_source_total_products": 0,
        "current_product_title": None,
        "site_products_total": 0,
        "can_cancel": job.can_cancel,
        "sync_period_minutes": 0,
    }


def _poll_and_finalize(agg_job: AggregateJob) -> None:
    base = _service_sync_base()
    agg_job.started_at = _utcnow()
    agg_job.status = "in_progress"

    for idx, (source_key, service_job_id) in enumerate(list(agg_job.source_jobs.items()), start=1):
        agg_job.current_source_index = idx
        agg_job.current_source_name = source_key
        agg_job.current_stage = "service_job_pending"
        started_monotonic = monotonic()
        while True:
            try:
                res = requests.get(f"{base}/jobs/{service_job_id}", timeout=(5, 30))
                res.raise_for_status()
                payload = res.json()
                st = str(payload.get("status") or "").lower()
            except Exception:
                st = "failed"
                payload = {}

            if st == "pending":
                agg_job.current_stage = "service_job_pending"
            elif st == "in_progress":
                agg_job.current_stage = "service_job_running"

            if st in {"pending", "in_progress"} and (monotonic() - started_monotonic) > SOURCE_JOB_TIMEOUT_SEC:
                st = "failed"
                payload = {
                    "report": {
                        "total_found_products": 0,
                        "parsed_visible_products": 0,
                    },
                }
                agg_job.current_stage = "service_job_timeout"

            if st in {"success", "partial", "failed", "cancelled"}:
                report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
                agg_job.expected_products += int(report.get("total_found_products") or 0)
                agg_job.processed_products += int(report.get("parsed_visible_products") or 0)
                up_expected, up_done = _upsert_products_from_service_report(source_key, report)
                agg_job.expected_db_upserts += up_expected
                agg_job.db_upserts_done += up_done
                if st == "failed":
                    agg_job.failed_products += 1
                break
            sleep(POLL_INTERVAL_SEC)
        agg_job.processed_sources += 1

    agg_job.completed_at = _utcnow()
    agg_job.can_cancel = False
    agg_job.current_stage = "completed"
    agg_job.status = "completed" if agg_job.failed_products == 0 else "failed"


@router.post("/jobs")
def run_sync_all_enabled_sources(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    latest = STATE.get_latest()
    if latest and latest.status in {"pending", "in_progress"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sync already in progress")

    sources = _load_sync_enabled_sources()
    if not sources:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="no sync-enabled sources")

    base = _service_sync_base()
    created_at = _utcnow()
    aggregate_id = f"agg-{int(created_at.timestamp())}"
    agg = AggregateJob(
        job_id=aggregate_id,
        status="pending",
        created_at=created_at,
        total_sources=len(sources),
    )

    for source in sources:
        source_key = str(source.get("key") or "").strip()
        if not source_key:
            continue
        res = requests.post(f"{base}/sources/{source_key}/run-async", params={"dry_run": "false"}, timeout=(10, 30))
        if not res.ok:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"failed to start source {source_key}: {res.status_code}")
        body = res.json()
        sid = str(body.get("job_id") or "").strip()
        if not sid:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"empty service job id for {source_key}")
        agg.source_jobs[source_key] = sid

    STATE.set_latest(agg)
    Thread(target=_poll_and_finalize, args=(agg,), daemon=True).start()
    return {"ok": True, "job_id": agg.job_id}


@router.get("/jobs/latest")
def jobs_latest() -> dict[str, Any] | None:
    return _map_latest_response(STATE.get_latest())


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    latest = STATE.get_latest()
    if latest is None or latest.job_id != job_id:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    if latest.status not in {"pending", "in_progress"}:
        return {"ok": False, "message": "already finished"}

    base = _service_sync_base()
    for service_job_id in latest.source_jobs.values():
        try:
            requests.post(f"{base}/jobs/{service_job_id}/cancel", timeout=(5, 15))
        except Exception:
            pass
    latest.status = "cancelled"
    latest.completed_at = _utcnow()
    latest.can_cancel = False
    latest.current_stage = "cancelled"
    return {"ok": True, "job_id": latest.job_id, "status": latest.status}


def _find_backend_source(db, source_key: str) -> ParserSource | None:
    norm = source_key.strip().lower()
    rows = db.query(ParserSource).filter(ParserSource.deleted_at.is_(None)).all()
    for row in rows:
        url = str(getattr(row, "url", "") or "").strip().lower()
        name = str(getattr(row, "name", "") or "").strip().lower()
        if norm and (norm in url or norm == name):
            return row
    return None


def _derive_status(variants: Any) -> str:
    if not isinstance(variants, list) or not variants:
        return "available"
    for v in variants:
        if isinstance(v, dict) and bool(v.get("available")):
            return "available"
    return "out_of_stock"


def _upsert_products_from_service_report(source_key: str, report: dict[str, Any]) -> tuple[int, int]:
    valid_products = report.get("valid_products") if isinstance(report.get("valid_products"), list) else []
    unavailable_products = report.get("unavailable_products") if isinstance(report.get("unavailable_products"), list) else []
    products = [*valid_products, *unavailable_products]
    expected = len(products)
    if expected == 0:
        return (0, 0)
    db = SessionLocal()
    upserts = 0
    try:
        source = _find_backend_source(db, source_key)
        if source is None:
            LOGGER.warning("jobs_ingest: source not found for key=%s", source_key)
            return (expected, 0)
        for item in products:
            if not isinstance(item, dict):
                continue
            handle = str(item.get("handle") or "").strip()
            if not handle:
                continue
            row = (
                db.query(ParserProduct)
                .filter(ParserProduct.deleted_at.is_(None))
                .filter(ParserProduct.source_id == int(source.id))
                .filter(ParserProduct.handle == handle)
                .first()
            )
            if row is None:
                row = ParserProduct(source_id=int(source.id), handle=handle, title=str(item.get("title") or handle), url=str(item.get("url") or ""))
                db.add(row)
            row.title = str(item.get("title") or row.title or handle)
            row.description = item.get("description")
            row.vendor = str(item.get("vendor") or "").strip() or None
            row.product_type = str(item.get("product_type") or "").strip() or None
            row.url = str(item.get("url") or row.url or "").strip()
            row.price = _safe_float(item.get("price"))
            row.currency = str(item.get("currency") or row.currency or "USD").strip().upper()[:3] or "USD"
            images = item.get("images") if isinstance(item.get("images"), list) else []
            row.image_urls = [str(x).strip() for x in images if str(x).strip()]
            row.image_count = len(row.image_urls)
            row.variants = item.get("variants") if isinstance(item.get("variants"), list) else []
            parsed_weight = _safe_float(item.get("weight_grams"))
            row.weight_grams = parsed_weight if parsed_weight is not None else row.weight_grams
            reasons = item.get("unavailable_reasons") if isinstance(item.get("unavailable_reasons"), list) else []
            if reasons:
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
