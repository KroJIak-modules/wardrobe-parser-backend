from __future__ import annotations

from uuid import uuid4

import requests

from app.api.v1.sources import _source_payload
from app.core.database import SessionLocal
from app.core.exceptions import ValidationError
from app.models import Source, SourceSetting, SourceSyncState, SyncJob, SyncJobSourceRun
from app.services.catalog.sync_error_humanizer import humanize_sync_error
from app.services.catalog.sync_job_service import SyncJobService


def test_humanize_sync_error_translates_service_connection_failure() -> None:
    raw = (
        "HTTPConnectionPool(host='service', port=8000): Max retries exceeded with url: /api/v1/sync/jobs/123 "
        "(Caused by NewConnectionError(\"HTTPConnection(host='service', port=8000): "
        "Failed to establish a new connection: [Errno 111] Connection refused\"))"
    )
    assert humanize_sync_error(raw) == "Сервис синхронизации временно недоступен. Попробуй повторить запуск позже."


def test_humanize_sync_error_translates_source_access_block() -> None:
    assert humanize_sync_error("storefront_blocked: Cloudflare captcha") == (
        "Сайт источника временно ограничил автоматический доступ. Попробуй повторить синхронизацию позже."
    )


def test_humanize_sync_error_translates_source_configuration_failure() -> None:
    assert humanize_sync_error("Missing source.config.strategy_sequence") == (
        "Настройки источника требуют проверки. Синхронизация этого источника не выполнена."
    )


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


def test_completed_job_exposes_source_product_issues_as_warnings() -> None:
    db = SessionLocal()
    source_key = f"sync-warning-{uuid4().hex[:10]}.example"
    job_id: int | None = None
    source_id: int | None = None
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
        source_id = int(source.id)
        job = SyncJob(
            trigger_kind="manual",
            status="completed",
            total_sources=1,
            processed_sources=1,
            products_seen=5,
            products_applied=5,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)
        db.add(
            SyncJobSourceRun(
                sync_job_id=job_id,
                source_id=source_id,
                status="completed",
                products_received=5,
                products_applied=5,
                failed_products=2,
                issue_counts={"missing_price": 2},
            )
        )
        db.commit()

        payload = SyncJobService(db).serialize_job(job_id)

        assert payload["status"] == "completed"
        assert payload["failed_products"] == 0
        assert payload["warning_products"] == 2
        assert payload["current_source_name"] is None
        assert payload["source_issues"] == [
            {
                "source_id": str(source_id),
                "source_name": source_key,
                "kind": "product_validation",
                "title": "Часть товаров сохранена как недоступная: в данных не хватает обязательной информации.",
                "affected_products": 2,
                "reasons": [{"code": "missing_price", "label": "не указана цена", "count": 2}],
            }
        ]
    finally:
        if job_id is not None:
            db.query(SyncJobSourceRun).filter(SyncJobSourceRun.sync_job_id == job_id).delete(synchronize_session=False)
            db.query(SyncJob).filter(SyncJob.id == job_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(SourceSyncState).filter(SourceSyncState.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_running_job_exposes_current_source_name() -> None:
    db = SessionLocal()
    source_key = f"sync-running-{uuid4().hex[:10]}.example"
    job_id: int | None = None
    source_id: int | None = None
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
        source_id = int(source.id)
        job = SyncJob(
            trigger_kind="manual",
            status="running",
            total_sources=1,
            processed_sources=0,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)
        db.add(SyncJobSourceRun(sync_job_id=job_id, source_id=source_id, status="running"))
        db.commit()

        payload = SyncJobService(db).serialize_job(job_id)

        assert payload["current_source_name"] == source_key
    finally:
        if job_id is not None:
            db.query(SyncJobSourceRun).filter(SyncJobSourceRun.sync_job_id == job_id).delete(synchronize_session=False)
            db.query(SyncJob).filter(SyncJob.id == job_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(SourceSyncState).filter(SourceSyncState.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_job_serialization_exposes_persisted_activity() -> None:
    db = SessionLocal()
    job_id: int | None = None
    try:
        job = SyncJob(
            trigger_kind="manual",
            status="queued",
            total_sources=1,
            current_stage_code="queued",
            current_stage_label="Запуск принят",
            current_stage_detail="Подготавливаем очередь источников",
        )
        db.add(job)
        db.commit()
        job_id = int(job.id)

        payload = SyncJobService(db).serialize_job(job_id)

        assert payload["current_stage"] == {
            "code": "queued",
            "label": "Запуск принят",
            "detail": "Подготавливаем очередь источников",
            "updated_at": None,
        }
    finally:
        if job_id is not None:
            db.query(SyncJob).filter(SyncJob.id == job_id).delete(synchronize_session=False)
            db.commit()
        db.close()


def test_running_source_overrides_stale_completion_activity() -> None:
    db = SessionLocal()
    source_key = f"sync-active-{uuid4().hex[:10]}.example"
    job_id: int | None = None
    source_id: int | None = None
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
        source_id = int(source.id)
        job = SyncJob(
            trigger_kind="manual",
            status="running",
            total_sources=2,
            processed_sources=1,
            current_stage_code="source_done",
            current_stage_label="Источник обработан",
            current_stage_detail="Сохраняем результаты",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)
        db.add(SyncJobSourceRun(sync_job_id=job_id, source_id=source_id, status="running"))
        db.commit()

        payload = SyncJobService(db).serialize_job(job_id)

        assert payload["current_source_name"] == source_key
        assert payload["current_stage"]["code"] == "source_active"
        assert payload["current_stage"]["label"] == "Обрабатываем товары текущего источника"
    finally:
        if job_id is not None:
            db.query(SyncJobSourceRun).filter(SyncJobSourceRun.sync_job_id == job_id).delete(synchronize_session=False)
            db.query(SyncJob).filter(SyncJob.id == job_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(SourceSyncState).filter(SourceSyncState.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_failed_job_does_not_expose_stale_running_source() -> None:
    db = SessionLocal()
    source_key = f"sync-failed-{uuid4().hex[:10]}.example"
    job_id: int | None = None
    source_id: int | None = None
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
        source_id = int(source.id)
        job = SyncJob(trigger_kind="manual", status="failed", total_sources=1)
        db.add(job)
        db.flush()
        job_id = int(job.id)
        db.add(SyncJobSourceRun(sync_job_id=job_id, source_id=source_id, status="running"))
        db.commit()

        payload = SyncJobService(db).serialize_job(job_id)

        assert payload["current_source_name"] is None
        assert payload["current_stage"]["code"] == "failed"
    finally:
        if job_id is not None:
            db.query(SyncJobSourceRun).filter(SyncJobSourceRun.sync_job_id == job_id).delete(synchronize_session=False)
            db.query(SyncJob).filter(SyncJob.id == job_id).delete(synchronize_session=False)
        if source_id is not None:
            db.query(SourceSetting).filter(SourceSetting.source_id == source_id).delete(synchronize_session=False)
            db.query(SourceSyncState).filter(SourceSyncState.source_id == source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == source_id).delete(synchronize_session=False)
        db.commit()
        db.close()
