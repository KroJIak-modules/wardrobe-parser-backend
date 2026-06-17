from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock, Thread
import logging
import time
import requests

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import SyncJob
from app.repositories.catalog_sources import CatalogSourceRepository
from app.repositories.catalog_sync import CatalogSyncRepository
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.source_registry_service import SourceRegistryService


LOGGER = logging.getLogger(__name__)


class SyncJobService:
    _lock = Lock()
    _service_job_ids: dict[int, str] = {}
    _poll_threads: dict[int, Thread] = {}

    def __init__(self, db: Session) -> None:
        self.db = db
        self.sync_repo = CatalogSyncRepository(db)
        self.source_repo = CatalogSourceRepository(db)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _service_url(path: str) -> str:
        return f"{settings.service_base_url.rstrip('/')}/api/v1/sync{path}"

    @classmethod
    def mark_interrupted_jobs_on_startup(cls) -> None:
        db = SessionLocal()
        try:
            for job in db.query(SyncJob).filter(SyncJob.status.in_(["queued", "in_progress"])).all():
                job.status = "failed"
                job.finished_at = cls._utcnow()
                job.error_message = "backend_restarted"
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    def start_job(self, *, triggered_by_admin_user_id: int | None, source_keys: list[str] | None = None) -> dict:
        registry = SourceRegistryService(self.db)
        sources = registry.refresh_from_service()
        normalized_requested = [str(key or "").strip().lower() for key in (source_keys or []) if str(key or "").strip()]
        selected_sources = [
            source
            for source in sources
            if source.key != SourceRegistryService.MANUAL_SOURCE_KEY
            and bool(getattr(getattr(source, "setting", None), "is_enabled", True))
            and bool(getattr(getattr(source, "setting", None), "is_sync_enabled", True))
            and (not normalized_requested or source.key in normalized_requested)
        ]
        if not selected_sources:
            raise ValueError("Нет доступных источников для синхронизации")

        response = requests.post(
            self._service_url("/jobs"),
            json={
                "triggered_by": "backend",
                "dry_run": False,
                "sources": [source.key for source in selected_sources],
            },
            timeout=(5, 30),
        )
        response.raise_for_status()
        payload = response.json()
        service_job_id = str(payload.get("job_id") or "").strip()
        if not service_job_id:
            raise RuntimeError("service did not return job_id")

        job = self.sync_repo.create_job(
            trigger_kind="manual",
            status="queued",
            triggered_by_admin_user_id=(int(triggered_by_admin_user_id) if triggered_by_admin_user_id is not None else None),
            total_sources=len(selected_sources),
            processed_sources=0,
            products_seen=0,
            products_applied=0,
        )
        self.db.commit()

        thread = Thread(
            target=self._poll_service_job,
            args=(int(job.id), service_job_id),
            name=f"backend-sync-{job.id}",
            daemon=True,
        )
        with self._lock:
            self._service_job_ids[int(job.id)] = service_job_id
            self._poll_threads[int(job.id)] = thread
        thread.start()
        return self.serialize_job(int(job.id))

    @classmethod
    def _process_source_started(cls, db: Session, *, backend_job_id: int, source_key: str) -> None:
        source = CatalogSourceRepository(db).get_by_key(source_key)
        if source is None:
            return
        sync_repo = CatalogSyncRepository(db)
        source_run = sync_repo.get_source_run(sync_job_id=backend_job_id, source_id=int(source.id))
        if source_run is None:
            source_run = sync_repo.create_source_run(
                sync_job_id=backend_job_id,
                source_id=int(source.id),
                status="in_progress",
                started_at=cls._utcnow(),
            )
        else:
            source_run.status = "in_progress"
            source_run.started_at = source_run.started_at or cls._utcnow()
        db.flush()

    @classmethod
    def _process_product_batch(
        cls,
        db: Session,
        *,
        backend_job_id: int,
        batch_key: str,
        source_key: str,
        stage: str,
        items: list[dict],
    ) -> None:
        source = CatalogSourceRepository(db).get_by_key(source_key)
        if source is None:
            return
        sync_repo = CatalogSyncRepository(db)
        source_run = sync_repo.get_source_run(sync_job_id=backend_job_id, source_id=int(source.id))
        if source_run is None:
            source_run = sync_repo.create_source_run(
                sync_job_id=backend_job_id,
                source_id=int(source.id),
                status="in_progress",
                started_at=cls._utcnow(),
            )
        if sync_repo.has_applied_batch(source_run_id=int(source_run.id), batch_key=batch_key):
            return

        applied = ProductIngestService(db).apply_batch(
            source_id=int(source.id),
            items=items,
            reconcile_missing=(str(stage or "").strip().lower() != "failed"),
        )
        source_run.products_received = int(source_run.products_received or 0) + int(applied.listings_seen)
        source_run.products_applied = int(source_run.products_applied or 0) + int(applied.listings_applied)
        sync_repo.mark_applied_batch(source_run_id=int(source_run.id), batch_key=batch_key)

        job = sync_repo.get_job(backend_job_id)
        if job is not None:
            job.products_seen = int(job.products_seen or 0) + int(applied.listings_seen)
            job.products_applied = int(job.products_applied or 0) + int(applied.listings_applied)
        db.flush()

    @classmethod
    def _process_source_finished(cls, db: Session, *, backend_job_id: int, source_key: str, status_value: str) -> None:
        source_repo = CatalogSourceRepository(db)
        source = source_repo.get_by_key(source_key)
        if source is None:
            return
        sync_repo = CatalogSyncRepository(db)
        source_run = sync_repo.get_source_run(sync_job_id=backend_job_id, source_id=int(source.id))
        now = cls._utcnow()
        if source_run is None:
            source_run = sync_repo.create_source_run(
                sync_job_id=backend_job_id,
                source_id=int(source.id),
                status=status_value,
                started_at=now,
                finished_at=now,
            )
        else:
            source_run.status = status_value
            source_run.finished_at = now

        sync_state = source_repo.ensure_sync_state(source)
        sync_state.last_sync_at = now
        sync_state.last_sync_status = status_value
        if source_run.started_at is not None:
            sync_state.last_sync_duration_sec = max(0, int((now - source_run.started_at).total_seconds()))
        job = sync_repo.get_job(backend_job_id)
        if job is not None:
            job.processed_sources = int(job.processed_sources or 0) + 1
        db.flush()

    @classmethod
    def _poll_service_job(cls, backend_job_id: int, service_job_id: str) -> None:
        db = SessionLocal()
        cursor = 0
        try:
            registry = SourceRegistryService(db)
            registry.refresh_from_service()
            sync_repo = CatalogSyncRepository(db)
            job = sync_repo.get_job(backend_job_id)
            if job is None:
                return
            job.status = "in_progress"
            job.started_at = cls._utcnow()
            db.commit()

            final_status = "completed"
            error_message: str | None = None
            while True:
                status_res = requests.get(cls._service_url(f"/jobs/{service_job_id}"), timeout=(5, 30))
                status_res.raise_for_status()
                raw_status_payload = status_res.json()
                status_payload = raw_status_payload if isinstance(raw_status_payload, dict) else {}

                events_res = requests.get(
                    cls._service_url(f"/jobs/{service_job_id}/events"),
                    params={"cursor": cursor, "limit": 500},
                    timeout=(5, 30),
                )
                events_res.raise_for_status()
                raw_events_payload = events_res.json()
                events_payload = raw_events_payload if isinstance(raw_events_payload, dict) else {}
                items = events_payload.get("items") if isinstance(events_payload.get("items"), list) else []
                next_cursor = int(events_payload.get("next_cursor") or cursor)

                for event in items:
                    if not isinstance(event, dict):
                        continue
                    event_type = str(event.get("type") or "").strip()
                    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                    source_key = str(payload.get("source_key") or "").strip().lower()
                    if event_type == "source_started" and source_key:
                        cls._process_source_started(db, backend_job_id=backend_job_id, source_key=source_key)
                    elif event_type == "product_batch" and source_key:
                        batch_key = str(payload.get("batch_id") or "").strip() or f"source:{source_key}:{next_cursor}"
                        stage = str(payload.get("stage") or "").strip().lower()
                        batch_items = payload.get("items") if isinstance(payload.get("items"), list) else []
                        cls._process_product_batch(
                            db,
                            backend_job_id=backend_job_id,
                            batch_key=batch_key,
                            source_key=source_key,
                            stage=stage,
                            items=[item for item in batch_items if isinstance(item, dict)],
                        )
                    elif event_type == "source_finished" and source_key:
                        cls._process_source_finished(
                            db,
                            backend_job_id=backend_job_id,
                            source_key=source_key,
                            status_value=str(payload.get("status") or "completed").strip().lower(),
                        )
                    db.commit()

                cursor = next_cursor
                service_status = str(status_payload.get("status") or "").strip().lower()
                if service_status in {"completed", "failed", "cancelled"}:
                    final_status = service_status
                    error_message = str(status_payload.get("error") or "").strip() or None
                    break
                time.sleep(2.0)

            job = sync_repo.get_job(backend_job_id)
            if job is not None:
                job.status = final_status
                job.finished_at = cls._utcnow()
                job.error_message = error_message
            db.commit()
        except Exception as exc:
            db.rollback()
            LOGGER.exception("Backend sync job %s failed", backend_job_id)
            job = CatalogSyncRepository(db).get_job(backend_job_id)
            if job is not None:
                job.status = "failed"
                job.finished_at = cls._utcnow()
                job.error_message = str(exc)
                db.commit()
        finally:
            with cls._lock:
                cls._poll_threads.pop(int(backend_job_id), None)
            db.close()

    def serialize_job(self, job_id: int) -> dict:
        job = self.sync_repo.get_job(job_id)
        if job is None:
            raise ValueError("job not found")
        processed_products = int(job.products_applied or 0)
        expected_products = int(job.products_seen or 0)
        total_sources = max(0, int(job.total_sources or 0))
        processed_sources = max(0, int(job.processed_sources or 0))
        source_progress = (processed_sources / total_sources * 100.0) if total_sources > 0 else 0.0
        product_progress = (processed_products / expected_products * 100.0) if expected_products > 0 else 0.0
        return {
            "job_id": str(job.id),
            "status": str(job.status or "queued"),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.finished_at.isoformat() if job.finished_at else None,
            "next_scheduled_at": None,
            "total_products": expected_products or None,
            "new_products": processed_products,
            "updated_products": 0,
            "new_images": 0,
            "total_sources": total_sources,
            "processed_sources": processed_sources,
            "progress_percent": round(source_progress if str(job.status) not in {"completed", "failed", "cancelled"} else 100.0, 2),
            "processed_products": processed_products,
            "expected_products": expected_products,
            "failed_products": max(0, expected_products - processed_products),
            "products_progress_percent": round(product_progress if expected_products > 0 else 0.0, 2),
            "error": job.error_message,
            "can_cancel": str(job.status or "") in {"queued", "in_progress"},
        }

    def latest(self) -> dict | None:
        job = self.sync_repo.get_latest_job()
        if job is None:
            return None
        return self.serialize_job(int(job.id))

    def cancel(self, job_id: int) -> dict:
        backend_job = self.sync_repo.get_job(job_id)
        if backend_job is None:
            raise ValueError("job not found")
        service_job_id = self._service_job_ids.get(int(job_id))
        if service_job_id:
            response = requests.post(self._service_url(f"/jobs/{service_job_id}/cancel"), timeout=(5, 20))
            response.raise_for_status()
        backend_job.status = "cancelled"
        backend_job.finished_at = self._utcnow()
        self.db.commit()
        return self.serialize_job(int(job_id))
