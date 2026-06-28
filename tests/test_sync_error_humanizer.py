from __future__ import annotations

from uuid import uuid4

import requests

from app.api.v1.sources import _source_payload
from app.core.database import SessionLocal
from app.core.exceptions import ValidationError
from app.models import Source, SourceSetting, SourceSyncState, SyncJob
from app.services.catalog.sync_error_humanizer import humanize_sync_error
from app.services.catalog.sync_job_service import SyncJobService


def test_humanize_sync_error_translates_service_connection_failure() -> None:
    raw = (
        "HTTPConnectionPool(host='service', port=8000): Max retries exceeded with url: /api/v1/sync/jobs/123 "
        "(Caused by NewConnectionError(\"HTTPConnection(host='service', port=8000): "
        "Failed to establish a new connection: [Errno 111] Connection refused\"))"
    )
    assert humanize_sync_error(raw) == "Сервис синхронизации временно недоступен. Попробуй повторить запуск позже."


def test_sync_job_start_returns_human_message_when_service_unavailable(monkeypatch) -> None:
    db = SessionLocal()
    source_key = f"start-sync-{uuid4().hex[:10]}.example"
    try:
        source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            adapter_key="demo__v1",
            parser_config={"mode": "auto"},
        )
        db.add(source)
        db.flush()
        db.add(SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True))
        db.commit()

        def _raise_unavailable(*args, **kwargs):
            raise requests.ConnectionError(
                "HTTPConnectionPool(host='service', port=8000): Max retries exceeded with url: /api/v1/sync/jobs"
            )

        monkeypatch.setattr(requests, "post", _raise_unavailable)

        try:
            SyncJobService(db).start_job(triggered_by_admin_user_id=None, source_keys=[source_key], trigger_kind="manual")
        except ValidationError as exc:
            assert str(exc) == "Сервис синхронизации временно недоступен. Попробуй повторить запуск позже."
        else:
            raise AssertionError("ValidationError not raised")
    finally:
        db.query(SourceSetting).filter(SourceSetting.source_id == int(source.id)).delete(synchronize_session=False)
        db.query(SourceSyncState).filter(SourceSyncState.source_id == int(source.id)).delete(synchronize_session=False)
        db.query(Source).filter(Source.key == source_key).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_source_payload_hides_technical_last_error_message() -> None:
    db = SessionLocal()
    source_key = f"source-error-{uuid4().hex[:10]}.example"
    try:
        source = Source(
            key=source_key,
            name=source_key,
            base_url=f"https://{source_key}",
            base_url_normalized=source_key,
            adapter_key="demo__v1",
            parser_config={"mode": "auto"},
        )
        db.add(source)
        db.flush()
        setting = SourceSetting(source_id=int(source.id), is_enabled=True, is_sync_enabled=True)
        sync_state = SourceSyncState(
            source_id=int(source.id),
            last_sync_status="failed",
            last_error_code="connectionerror",
            last_error_message=(
                "HTTPConnectionPool(host='service', port=8000): Max retries exceeded with url: /api/v1/sync/jobs/123 "
                "(Caused by NewConnectionError(\"HTTPConnection(host='service', port=8000): "
                "Failed to establish a new connection: [Errno 111] Connection refused\"))"
            ),
        )
        db.add(setting)
        db.add(sync_state)
        db.flush()
        source.setting = setting
        source.sync_state = sync_state

        payload = _source_payload(source, {})

        assert payload["last_error_message"] == "Сервис синхронизации временно недоступен. Попробуй повторить запуск позже."
    finally:
        db.rollback()
        db.close()


def test_sync_job_serialize_hides_technical_error_message() -> None:
    db = SessionLocal()
    job_id: int | None = None
    try:
        job = SyncJob(
            trigger_kind="manual",
            status="failed",
            total_sources=1,
            processed_sources=1,
            products_seen=0,
            products_applied=0,
            error_message=(
                "HTTPConnectionPool(host='service', port=8000): Max retries exceeded with url: /api/v1/sync/jobs/123 "
                "(Caused by NewConnectionError(\"HTTPConnection(host='service', port=8000): "
                "Failed to establish a new connection: [Errno 111] Connection refused\"))"
            ),
        )
        db.add(job)
        db.commit()
        job_id = int(job.id)

        payload = SyncJobService(db).serialize_job(int(job.id))

        assert payload["error"] == "Сервис синхронизации временно недоступен. Попробуй повторить запуск позже."
    finally:
        if job_id is not None:
            db.query(SyncJob).filter(SyncJob.id == job_id).delete(synchronize_session=False)
            db.commit()
        db.close()
