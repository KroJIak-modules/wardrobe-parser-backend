from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock, Thread
import logging
import time
import requests

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import ValidationError
from app.models import SyncJob, SyncJobSourceRun
from app.repositories.catalog_products import CatalogProductRepository
from app.repositories.catalog_sources import CatalogSourceRepository
from app.repositories.catalog_sync import CatalogSyncRepository
from app.services.catalog.designer_catalog_sync_service import DesignerCatalogSyncService
from app.services.catalog.sync_error_humanizer import humanize_sync_error, normalize_sync_error_code
from app.services.catalog.product_ingest_service import ProductIngestService
from app.services.catalog.source_registry_service import SourceRegistryService


LOGGER = logging.getLogger(__name__)


class SyncJobService:
    _lock = Lock()
    _service_job_ids: dict[int, str] = {}
    _poll_threads: dict[int, Thread] = {}

    def __init__(self, db: Session) -> None:
        self.db = db
        self.products = CatalogProductRepository(db)
        self.sync_repo = CatalogSyncRepository(db)
        self.source_repo = CatalogSourceRepository(db)

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _service_url(path: str) -> str:
        return f"{settings.service_base_url.rstrip('/')}/api/v1/sync{path}"

    @staticmethod
    def _normalize_job_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"queued", "completed", "failed"}:
            return value
        if value == "running":
            return "running"
        if value == "canceled":
            return "canceled"
        return "queued"

    @staticmethod
    def _normalize_source_run_status(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value in {"queued", "completed", "failed", "skipped"}:
            return value
        if value == "success":
            return "completed"
        if value == "partial":
            return "completed"
        if value == "running":
            return "running"
        if value == "canceled":
            return "failed"
        return "queued"

    @staticmethod
    def _normalize_source_state_status(raw: str | None) -> str | None:
        value = str(raw or "").strip().lower()
        if value in {"success", "partial", "failed"}:
            return value
        if value == "completed":
            return "success"
        return None

    @staticmethod
    def _derive_error_code(error_message: str | None) -> str | None:
        return normalize_sync_error_code(error_message)

    @staticmethod
    def _stage_detail(*, stage_code: str | None, fields: object, source_name: str | None) -> tuple[str, str]:
        code = str(stage_code or "").strip().lower()
        values = fields if isinstance(fields, dict) else {}
        source = str(source_name or "").strip()
        labels = {
            "source_prepare": ("Подготавливаем источник", "Проверяем настройки и выбираем способ загрузки"),
            "discover_products": ("Ищем товары на витрине", "Собираем актуальный список карточек товаров"),
            "discover_done": ("Список товаров собран", "Переходим к загрузке и проверке карточек"),
            "fetch_start": ("Начинаем загрузку карточек", "Получаем данные товаров из источника"),
            "fetch_progress": ("Загружаем и проверяем товары", "Получаем цены, варианты и доступность"),
            "fetch_skip": ("Пропускаем проблемную карточку", "Остальные товары продолжают обрабатываться"),
            "export_products": ("Выгружаем товары", "Получаем карточки через экспорт источника"),
            "strategy_run": ("Проверяем сайт в браузере", "Выполняем сценарий, который нужен этому источнику"),
            "source_done": ("Источник обработан", "Сохраняем результаты и переходим к следующему источнику"),
            "source_failed": ("Источник завершился с проблемой", "Фиксируем результат и продолжаем с остальными источниками"),
        }
        title, detail = labels.get(code, ("Синхронизация выполняется", "Получаем и проверяем данные источника"))
        processed = str(values.get("processed") or "").strip()
        percentage = str(values.get("pct") or values.get("percent") or "").strip()
        progress = processed or (f"Готово {percentage}%" if percentage else "")
        if progress:
            detail = f"{detail}. {progress}"
        return title, detail

    @classmethod
    def _set_current_stage(
        cls,
        db: Session,
        *,
        backend_job_id: int,
        stage_code: str | None,
        fields: object = None,
        source_key: str | None = None,
    ) -> None:
        job = CatalogSyncRepository(db).get_job(backend_job_id)
        if job is None:
            return
        source = CatalogSourceRepository(db).get_by_key(source_key) if source_key else None
        source_name = str(getattr(source, "name", None) or source_key or "").strip() or None
        if source_name:
            job.last_source_name = source_name
        title, detail = cls._stage_detail(stage_code=stage_code, fields=fields, source_name=source_name)
        job.current_stage_code = str(stage_code or "syncing").strip().lower() or "syncing"
        job.current_stage_label = title
        job.current_stage_detail = detail
        job.current_stage_updated_at = cls._utcnow()

    @staticmethod
    def _normalize_issue_counts(raw: object) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        counts: dict[str, int] = {}
        for raw_code, raw_count in raw.items():
            code = str(raw_code or "").strip().lower()
            try:
                count = max(0, int(raw_count))
            except (TypeError, ValueError):
                continue
            if code and count:
                counts[code] = count
        return counts

    @staticmethod
    def _issue_label(code: str) -> str:
        labels = {
            "missing_variants": "нет доступного варианта товара",
            "missing_currency": "не указана валюта цены",
            "missing_price": "не указана цена",
            "missing_images": "нет изображения товара",
            "missing_title": "нет названия товара",
            "missing_url": "нет ссылки на товар",
            "invalid_price": "некорректная цена",
            "unavailable": "товар недоступен",
            "unknown": "не хватает обязательных данных",
        }
        return labels.get(code, "данные товара не прошли проверку")

    def _serialize_source_issues(self, job_id: int) -> tuple[int, list[dict]]:
        warning_products = 0
        issues: list[dict] = []
        for source_run in self.sync_repo.list_source_runs(sync_job_id=job_id):
            issue_counts = self._normalize_issue_counts(getattr(source_run, "issue_counts", {}))
            failed_products = max(0, int(getattr(source_run, "failed_products", 0) or 0))
            accounted_products = sum(issue_counts.values())
            unclassified_products = max(0, failed_products - accounted_products)
            if unclassified_products:
                issue_counts["unknown"] = issue_counts.get("unknown", 0) + unclassified_products

            source_status = self._normalize_source_run_status(getattr(source_run, "status", None))
            error_code = normalize_sync_error_code(
                getattr(source_run, "error_message", None),
                getattr(source_run, "error_code", None),
            )
            error_message = humanize_sync_error(
                getattr(source_run, "error_message", None),
                getattr(source_run, "error_code", None),
            )
            is_source_failure = source_status == "failed"
            if not issue_counts and not is_source_failure:
                continue

            warning_products += sum(issue_counts.values())
            reasons = [
                {"code": code, "label": self._issue_label(code), "count": count}
                for code, count in sorted(issue_counts.items(), key=lambda item: (-item[1], item[0]))
            ]
            source = getattr(source_run, "source", None)
            issues.append(
                {
                    "source_id": str(getattr(source_run, "source_id", "") or ""),
                    "source_name": str(getattr(source, "name", None) or "Источник"),
                    "kind": error_code or ("source_failed" if is_source_failure else "product_validation"),
                    "title": (
                        error_message
                        if is_source_failure
                        else "Часть товаров сохранена как недоступная: в данных не хватает обязательной информации."
                    ),
                    "affected_products": sum(issue_counts.values()),
                    "reasons": reasons,
                }
            )
        return warning_products, issues

    def _current_source_name(self, job_id: int) -> str | None:
        for source_run in self.sync_repo.list_source_runs(sync_job_id=job_id):
            if self._normalize_source_run_status(getattr(source_run, "status", None)) != "running":
                continue
            source = getattr(source_run, "source", None)
            return str(getattr(source, "name", None) or "").strip() or None
        return None

    def _current_source_run(self, job_id: int):
        for source_run in self.sync_repo.list_source_runs(sync_job_id=job_id):
            if self._normalize_source_run_status(getattr(source_run, "status", None)) == "running":
                return source_run
        return None

    @staticmethod
    def _fallback_stage_payload(job: SyncJob) -> dict:
        status = str(getattr(job, "status", "") or "").strip().lower()
        if status == "queued":
            return {
                "code": "queued",
                "label": "Запуск ожидает начала",
                "detail": "Подготавливаем очередь источников и подключение к сервису синхронизации",
                "updated_at": None,
            }
        if status == "running":
            return {
                "code": "syncing",
                "label": "Синхронизация выполняется",
                "detail": "Получаем и проверяем данные выбранных источников",
                "updated_at": None,
            }
        if status == "failed":
            return {
                "code": "failed",
                "label": "Синхронизация завершилась с ошибкой",
                "detail": humanize_sync_error(getattr(job, "error_message", None)) or "Проверь причины ошибки в карточке запуска",
                "updated_at": None,
            }
        return {"code": None, "label": None, "detail": None, "updated_at": None}

    @classmethod
    def mark_interrupted_jobs_on_startup(cls) -> None:
        db = SessionLocal()
        try:
            for job in db.query(SyncJob).filter(SyncJob.status.in_(["queued", "running"])).all():
                active_run = (
                    db.query(SyncJobSourceRun)
                    .filter(SyncJobSourceRun.sync_job_id == int(job.id), SyncJobSourceRun.status == "running")
                    .order_by(SyncJobSourceRun.id.desc())
                    .first()
                )
                if active_run is not None:
                    source = CatalogSourceRepository(db).get_by_id(int(active_run.source_id))
                    job.last_source_name = str(getattr(source, "name", None) or "").strip() or job.last_source_name
                job.status = "failed"
                job.finished_at = cls._utcnow()
                job.error_message = humanize_sync_error("backend_restarted", "backend_restarted")
                job.current_stage_code = "backend_restarted"
                job.current_stage_label = "Синхронизация прервана перезапуском сервера"
                job.current_stage_detail = job.error_message
                job.current_stage_updated_at = cls._utcnow()
                (
                    db.query(SyncJobSourceRun)
                    .filter(SyncJobSourceRun.sync_job_id == int(job.id), SyncJobSourceRun.status == "running")
                    .update(
                        {
                            "status": "failed",
                            "finished_at": job.finished_at,
                            "error_code": "backend_restarted",
                            "error_message": job.error_message,
                        },
                        synchronize_session=False,
                    )
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def start_job(
        self,
        *,
        triggered_by_admin_user_id: int | None,
        source_keys: list[str] | None = None,
        trigger_kind: str = "manual",
    ) -> dict:
        registry = SourceRegistryService(self.db)
        sources = registry.list_all()
        normalized_requested = [str(key or "").strip().lower() for key in (source_keys or []) if str(key or "").strip()]
        selected_sources = []
        for source in sources:
            source_mode = SourceRegistryService.derive_source_mode(source)
            if source_mode == "personal":
                continue
            if not bool(getattr(getattr(source, "setting", None), "is_enabled", True)):
                continue
            if not bool(getattr(getattr(source, "setting", None), "is_sync_enabled", True)):
                continue
            if normalized_requested:
                if source.key in normalized_requested:
                    selected_sources.append(source)
                continue
            selected_sources.append(source)
        if not selected_sources:
            raise ValueError("Нет доступных источников для синхронизации")

        candidate_urls_by_source = {
            source.key: self.products.list_source_listing_urls(int(source.id))
            for source in selected_sources
            if SourceRegistryService.derive_source_mode(source) == "manual"
        }
        try:
            response = requests.post(
                self._service_url("/jobs"),
                json={
                    "triggered_by": "backend",
                    "dry_run": False,
                    "sources": [source.key for source in selected_sources],
                    "candidate_urls_by_source": candidate_urls_by_source,
                },
                timeout=(5, 30),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ValidationError(humanize_sync_error(str(exc), exc.__class__.__name__) or "Сервис синхронизации временно недоступен.") from exc
        payload = response.json()
        service_job_id = str(payload.get("job_id") or "").strip()
        if not service_job_id:
            raise RuntimeError("service did not return job_id")

        job = self.sync_repo.create_job(
            trigger_kind=str(trigger_kind or "manual").strip().lower() or "manual",
            status="queued",
            triggered_by_admin_user_id=(int(triggered_by_admin_user_id) if triggered_by_admin_user_id is not None else None),
            total_sources=len(selected_sources),
            processed_sources=0,
            products_seen=0,
            products_applied=0,
            current_stage_code="queued",
            current_stage_label="Запуск принят",
            current_stage_detail="Подготавливаем очередь источников и подключение к сервису синхронизации",
            current_stage_updated_at=self._utcnow(),
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
                status="running",
                started_at=cls._utcnow(),
            )
        else:
            source_run.status = "running"
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
        reconcile_missing: bool,
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
                status="running",
                started_at=cls._utcnow(),
            )
        if sync_repo.has_applied_batch(source_run_id=int(source_run.id), batch_key=batch_key):
            return

        applied = ProductIngestService(db).apply_batch(
            source_id=int(source.id),
            items=items,
            reconcile_missing=bool(reconcile_missing),
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
    def _process_source_finished(
        cls,
        db: Session,
        *,
        backend_job_id: int,
        source_key: str,
        status_value: str,
        error_code: str | None = None,
        error_message: str | None = None,
        unavailable_products: int = 0,
        issue_counts: dict | None = None,
    ) -> None:
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
            source_run.status = cls._normalize_source_run_status(status_value)
            source_run.finished_at = now
        failed_products = max(
            int(source_run.failed_products or 0),
            max(0, int(unavailable_products or 0)),
            max(0, int(source_run.products_received or 0) - int(source_run.products_applied or 0)),
        )
        source_run.failed_products = failed_products
        source_run.issue_counts = cls._normalize_issue_counts(issue_counts)
        normalized_source_state_status = cls._normalize_source_state_status(status_value)
        if normalized_source_state_status == "failed":
            source_run.error_code = normalize_sync_error_code(error_message, error_code)
            source_run.error_message = humanize_sync_error(error_message, source_run.error_code)
        elif normalized_source_state_status is not None:
            source_run.error_message = None
            source_run.error_code = None

        sync_state = source_repo.ensure_sync_state(source)
        sync_state.last_sync_at = now
        if normalized_source_state_status is not None:
            sync_state.last_sync_status = normalized_source_state_status
        if source_run.started_at is not None:
            sync_state.last_sync_duration_sec = max(0, int((now - source_run.started_at).total_seconds()))
        if normalized_source_state_status == "failed":
            sync_state.last_error_message = source_run.error_message
            sync_state.last_error_code = source_run.error_code
        elif normalized_source_state_status is not None:
            sync_state.last_error_message = None
            sync_state.last_error_code = None
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
            registry.list_all()
            sync_repo = CatalogSyncRepository(db)
            job = sync_repo.get_job(backend_job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = cls._utcnow()
            job.current_stage_code = "connecting"
            job.current_stage_label = "Подключаемся к сервису синхронизации"
            job.current_stage_detail = "Запускаем обработку выбранных источников"
            job.current_stage_updated_at = cls._utcnow()
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
                        cls._set_current_stage(
                            db,
                            backend_job_id=backend_job_id,
                            source_key=source_key,
                            stage_code=str(payload.get("stage_code") or "source_prepare"),
                            fields=payload.get("fields"),
                        )
                    elif event_type == "source_progress" and source_key:
                        cls._set_current_stage(
                            db,
                            backend_job_id=backend_job_id,
                            source_key=source_key,
                            stage_code=str(payload.get("stage_code") or "syncing"),
                            fields=payload.get("fields"),
                        )
                    elif event_type == "product_batch" and source_key:
                        batch_key = str(payload.get("batch_id") or "").strip() or f"source:{source_key}:{next_cursor}"
                        stage = str(payload.get("stage") or "").strip().lower()
                        reconcile_missing = bool(payload.get("reconcile_missing", False))
                        batch_items = payload.get("items") if isinstance(payload.get("items"), list) else []
                        cls._process_product_batch(
                            db,
                            backend_job_id=backend_job_id,
                            batch_key=batch_key,
                            source_key=source_key,
                            stage=stage,
                            reconcile_missing=reconcile_missing,
                            items=[item for item in batch_items if isinstance(item, dict)],
                        )
                    elif event_type == "source_finished" and source_key:
                        cls._process_source_finished(
                            db,
                            backend_job_id=backend_job_id,
                            source_key=source_key,
                            status_value=str(payload.get("status") or "completed").strip().lower(),
                            error_code=(str(payload.get("error_code") or "").strip() or None),
                            error_message=(str(payload.get("error") or "").strip() or None),
                            unavailable_products=int(payload.get("unavailable_products") or 0),
                            issue_counts=payload.get("issue_counts") if isinstance(payload.get("issue_counts"), dict) else None,
                        )
                    db.commit()

                cursor = next_cursor
                service_status = str(status_payload.get("status") or "").strip().lower()
                if service_status in {"completed", "failed", "canceled"}:
                    final_status = cls._normalize_job_status(service_status)
                    error_message = humanize_sync_error(str(status_payload.get("error") or "").strip() or None)
                    break
                time.sleep(2.0)

            job = sync_repo.get_job(backend_job_id)
            if job is not None:
                job.status = cls._normalize_job_status(final_status)
                job.finished_at = cls._utcnow()
                job.error_message = error_message
                job.current_stage_code = str(final_status or "completed")
                if final_status == "failed":
                    job.current_stage_label = "Синхронизация завершена с ошибками"
                    job.current_stage_detail = error_message or "Проверь проблемы по источникам ниже"
                elif final_status == "canceled":
                    job.current_stage_label = "Синхронизация отменена"
                    job.current_stage_detail = "Обработка источников остановлена"
                else:
                    job.current_stage_label = "Синхронизация завершена"
                    job.current_stage_detail = "Все выбранные источники обработаны"
                job.current_stage_updated_at = cls._utcnow()
            DesignerCatalogSyncService(db).reconcile(sync_product_links=True)
            db.commit()
        except Exception as exc:
            db.rollback()
            LOGGER.exception("Backend sync job %s failed", backend_job_id)
            job = CatalogSyncRepository(db).get_job(backend_job_id)
            if job is not None:
                job.status = "failed"
                job.finished_at = cls._utcnow()
                job.error_message = humanize_sync_error(str(exc), exc.__class__.__name__)
                job.current_stage_code = "failed"
                job.current_stage_label = "Синхронизация остановлена из-за ошибки"
                job.current_stage_detail = job.error_message
                job.current_stage_updated_at = cls._utcnow()
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
        warning_products, source_issues = self._serialize_source_issues(int(job.id))
        job_status = self._normalize_job_status(getattr(job, "status", "queued"))
        current_source_run = self._current_source_run(int(job.id)) if job_status in {"queued", "running"} else None
        current_source = getattr(current_source_run, "source", None)
        current_source_name = (
            str(getattr(current_source, "name", None) or "").strip()
            or str(getattr(job, "last_source_name", None) or "").strip()
            or None
        )
        stage_payload = {
            "code": str(getattr(job, "current_stage_code", None) or "").strip() or None,
            "label": str(getattr(job, "current_stage_label", None) or "").strip() or None,
            "detail": str(getattr(job, "current_stage_detail", None) or "").strip() or None,
            "updated_at": job.current_stage_updated_at.isoformat() if getattr(job, "current_stage_updated_at", None) else None,
        }
        if not stage_payload["label"]:
            stage_payload = self._fallback_stage_payload(job)
        if current_source_name and stage_payload["code"] in {"source_done", "source_failed", "completed", "failed"}:
            stage_payload = {
                "code": "source_active",
                "label": "Обрабатываем товары текущего источника",
                "detail": "Получаем карточки, проверяем цены, варианты и доступность",
                "updated_at": stage_payload["updated_at"],
            }
        return {
            "job_id": str(job.id),
            "status": job_status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "total_sources": total_sources,
            "processed_sources": processed_sources,
            "progress_percent": round(source_progress if self._normalize_job_status(getattr(job, "status", "queued")) not in {"completed", "failed", "canceled"} else 100.0, 2),
            "products_seen": expected_products,
            "products_applied": processed_products,
            "failed_products": max(0, expected_products - processed_products),
            "warning_products": warning_products,
            "source_issues": source_issues,
            "current_source_name": current_source_name,
            "current_stage": stage_payload,
            "error": humanize_sync_error(job.error_message),
            "can_cancel": self._normalize_job_status(getattr(job, "status", "")) in {"queued", "running"},
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
        backend_job.status = "canceled"
        backend_job.finished_at = self._utcnow()
        backend_job.current_stage_code = "canceled"
        backend_job.current_stage_label = "Синхронизация отменена"
        backend_job.current_stage_detail = "Обработка источников остановлена администратором"
        backend_job.current_stage_updated_at = self._utcnow()
        self.db.commit()
        return self.serialize_job(int(job_id))
