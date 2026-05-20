"""Backend sync orchestration over parser-service /sync/jobs contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock, Thread
from time import sleep
from typing import Any
from urllib.parse import urlparse

import hashlib
import logging
import requests
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import ParserProduct, ParserProductOriginVariant, ParserSource, SyncAppliedBatch, SyncJobRuntime
from app.services.catalog.category_index_service import CategoryIndexService


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


def _service_sources_list() -> list[dict[str, Any]]:
    try:
        res = requests.get(f"{_service_sync_base()}/sources", timeout=(3, 10))
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
    except Exception:
        return []


def _load_source_strategy_sequence(source_key: str | None) -> list[str]:
    key = str(source_key or "").strip().lower()
    if not key:
        return []
    try:
        for item in _service_sources_list():
            cur_key = str(item.get("key") or "").strip().lower()
            if cur_key != key:
                continue
            cfg = item.get("config") if isinstance(item.get("config"), dict) else {}
            seq = cfg.get("strategy_sequence") if isinstance(cfg.get("strategy_sequence"), list) else []
            return [str(x).strip() for x in seq if str(x).strip()]
    except Exception:
        return []
    return []


def _normalized_host(raw: str) -> str:
    try:
        host = str(urlparse(str(raw or "").strip()).netloc or "").strip().lower()
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _build_manual_candidate_urls_by_source(*, source_keys: list[str]) -> dict[str, list[str]]:
    normalized_selected = {str(key or "").strip().lower() for key in source_keys if str(key or "").strip()}
    if not normalized_selected:
        return {}
    service_sources = _service_sources_list()
    manual_keys: set[str] = set()
    for item in service_sources:
        key = str(item.get("key") or "").strip().lower()
        if not key or key not in normalized_selected:
            continue
        cfg = item.get("config") if isinstance(item.get("config"), dict) else {}
        mode = str(cfg.get("mode") or "auto").strip().lower()
        if mode == "manual":
            manual_keys.add(key)
    if not manual_keys:
        return {}

    db = SessionLocal()
    try:
        sources = (
            db.query(ParserSource)
            .filter(ParserSource.deleted_at.is_(None))
            .all()
        )
        source_id_to_key: dict[int, str] = {}
        for source in sources:
            host = _normalized_host(getattr(source, "url", "") or "")
            name = str(getattr(source, "name", "") or "").strip().lower()
            for candidate in (host, name):
                if candidate and candidate in manual_keys:
                    source_id_to_key[int(source.id)] = candidate
                    break
        if not source_id_to_key:
            return {}

        rows = (
            db.query(
                ParserProductOriginVariant.source_id,
                ParserProductOriginVariant.source_product_url,
            )
            .join(ParserProduct, ParserProduct.id == ParserProductOriginVariant.product_id)
            .filter(ParserProduct.deleted_at.is_(None))
            .filter(ParserProductOriginVariant.source_id.in_(list(source_id_to_key.keys())))
            .all()
        )
        urls_by_key: dict[str, list[str]] = {key: [] for key in manual_keys}
        seen_by_key: dict[str, set[str]] = {key: set() for key in manual_keys}
        for row in rows:
            source_id = int(row.source_id) if row.source_id is not None else None
            if source_id is None:
                continue
            source_key = source_id_to_key.get(source_id)
            if not source_key:
                continue
            product_url = str(row.source_product_url or "").strip()
            if not product_url:
                continue
            if product_url in seen_by_key[source_key]:
                continue
            seen_by_key[source_key].add(product_url)
            urls_by_key[source_key].append(product_url)
        return {
            key: values
            for key, values in urls_by_key.items()
            if values
        }
    finally:
        db.close()


_JADED_STRATEGY_RU: dict[str, str] = {
    "shopify_json": "Shopify JSON",
    "shopify_js": "Shopify JS",
    "shopify_browser_extension": "Браузерный сценарий",
}

_STRATEGY_RU: dict[str, str] = {
    "shopify_json": "Shopify JSON",
    "shopify_js": "Shopify JS",
    "shopify_browser_extension": "Браузерный сценарий",
    "store_backlash_colorme": "ColorMe сценарий",
    "vinted_jsonld": "Vinted JSON-LD",
    "grailed_algolia_jsonld": "Grailed Algolia",
    "goat_browser_extension": "GOAT браузерный сценарий",
    "intl_protocol_index_cafe24": "Cafe24 сценарий",
}

_JADED_STAGE_RU: dict[str, str] = {
    "discover_start": "Поиск товаров",
    "discover_done": "Ссылки найдены",
    "fetch_start": "Загрузка карточек",
    "fetch_progress": "Обработка карточек",
    "fetch_skip": "Пропуск карточки",
    "run_done": "Этап завершен",
    "start": "Старт стратегии",
    "progress": "Выполнение стратегии",
    "done": "Стратегия завершена",
    "second_pass_done": "Повторная попытка завершена",
    "pages_fetched": "Загрузка страниц каталога",
    "page_loop_detected": "Обнаружено зацикливание страниц",
}


def _parse_processed_percent(raw_processed: Any) -> int | None:
    text = str(raw_processed or "").strip()
    if "/" not in text:
        return None
    try:
        left, right = text.split("/", 1)
        done = float(left.strip())
        total = float(right.strip())
        if total <= 0:
            return None
        return int(max(0, min(100, round((done / total) * 100))))
    except Exception:
        return None


def _extract_percent_from_stage_text(stage_text: str | None) -> float | None:
    text = str(stage_text or "").strip()
    if not text:
        return None
    try:
        tail = text.rsplit("|", 1)[1].strip()
        if tail.endswith("%"):
            val = float(tail[:-1].strip())
            if 0.0 <= val <= 100.0:
                return val
    except Exception:
        return None
    return None


def _stage_is_generic(raw: str | None) -> bool:
    value = str(raw or "").strip().lower()
    return value in {"", "progress", "in_progress", "source_started"}


def _format_stage_for_source(
    *,
    source_key: str | None,
    strategy_value: str,
    evt_type: str,
    evt_payload: dict[str, Any],
) -> str:
    source_norm = str(source_key or "").strip().lower()
    stage_raw = str(evt_payload.get("stage") or "").strip()
    stage_label = str(evt_payload.get("stage_label") or "").strip()
    percent_value = _safe_float(evt_payload.get("percent"))
    if percent_value is None:
        percent_value = _safe_float(evt_payload.get("pct"))
    if percent_value is None:
        percent_value = _safe_float(evt_payload.get("progress_percent"))
    if percent_value is None:
        parsed_pct = _parse_processed_percent(evt_payload.get("processed"))
        percent_value = float(parsed_pct) if parsed_pct is not None else None
    percent_tail = f" | {int(round(percent_value))}%" if percent_value is not None else ""
    fields = evt_payload.get("fields") if isinstance(evt_payload.get("fields"), dict) else {}
    if percent_value is None and fields:
        percent_value = _safe_float(fields.get("percent"))
        if percent_value is None:
            percent_value = _safe_float(fields.get("pct"))
        if percent_value is None:
            parsed_pct = _parse_processed_percent(fields.get("processed"))
            percent_value = float(parsed_pct) if parsed_pct is not None else None
        percent_tail = f" | {int(round(percent_value))}%" if percent_value is not None else ""

    if evt_type == "source_started":
        return "Источник: подготовка к запуску"
    if evt_type == "source_finished":
        return "Источник: обработка завершена"
    if evt_type == "job_finished":
        return "Синхронизация завершена"
    if evt_type == "job_failed":
        return "Синхронизация завершилась с ошибкой"
    if evt_type == "job_cancelled":
        return "Синхронизация отменена"

    if source_norm == "jadedldn.com":
        stage_ru = _JADED_STAGE_RU.get(stage_raw, stage_label or evt_type or "Выполнение")
        if strategy_value == "shopify_json":
            pages_fetched = fields.get("pages_fetched")
            dedup_items = fields.get("dedup_items")
            if pages_fetched is not None and dedup_items is not None:
                stage_ru = f"Сбор товаров: страниц {pages_fetched}, товаров {dedup_items}"
            # Shopify JSON often has unknown final denominator at this point.
            # Keep progress visibly moving instead of a flat 0%.
            if percent_value is not None and percent_value < 1.0:
                try:
                    pf = int(fields.get("pages_fetched") or 0)
                except Exception:
                    pf = 0
                if pf > 0:
                    percent_value = min(25.0, max(2.0, float(pf * 5)))
                    percent_tail = f" | {int(round(percent_value))}%"
            elif fields.get("max_products") is not None and stage_raw == "start":
                stage_ru = f"Старт обхода sitemap (лимит {fields.get('max_products')})"
        if strategy_value == "shopify_js":
            processed = str(fields.get("processed") or "").strip()
            if processed:
                stage_ru = f"Обработка карточек {processed}"
        return f"{stage_ru}{percent_tail}".strip()

    # Default plain-language stage for non-jaded sources.
    if strategy_value == "shopify_browser_extension":
        stage_norm = stage_raw.lower()
        if stage_norm.startswith("start "):
            return f"Подготовка браузерного сценария{percent_tail}".strip()
        if "navigated to homepage" in stage_norm:
            return f"Открытие сайта{percent_tail}".strip()
        if "fetching sitemap.xml" in stage_norm:
            return f"Запрос sitemap.xml{percent_tail}".strip()
        if "sitemap.xml ok" in stage_norm:
            return f"sitemap.xml получен{percent_tail}".strip()
        if "product sitemaps discovered=" in stage_norm:
            return f"Найдены карты товаров{percent_tail}".strip()
        if stage_norm.startswith("sitemap ") and "->" in stage_norm:
            # Example: sitemap 4/24 -> ...
            part = stage_raw.split("->", 1)[0].strip()
            return f"Чтение карты товаров: {part}{percent_tail}".strip()
        if stage_norm.startswith("sitemap ok "):
            return f"Ссылки товаров добавлены{percent_tail}".strip()
        if stage_norm.startswith("sitemap failed "):
            return f"Ошибка чтения карты товаров{percent_tail}".strip()
        if "scenario_start" in str(fields.get("phase") or "").lower():
            return f"Запуск сценария{percent_tail}".strip()
        if "browser_started" in str(fields.get("phase") or "").lower():
            return f"Браузер запущен{percent_tail}".strip()
        if "extension_connected" in str(fields.get("phase") or "").lower():
            return f"Расширение подключено{percent_tail}".strip()
        if "waiting_extension" in str(fields.get("phase") or "").lower():
            return f"Ожидание расширения{percent_tail}".strip()
        if "extension_ping_ok" in str(fields.get("phase") or "").lower():
            return f"Проверка расширения{percent_tail}".strip()

    if strategy_value == "shopify_js" and stage_raw == "progress":
        processed = str(fields.get("processed") or "").strip()
        if processed:
            return f"Обработка карточек {processed}{percent_tail}".strip()
        return f"Обработка карточек{percent_tail}".strip()
    if stage_raw == "progress":
        return f"Выполнение{percent_tail}".strip()
    if stage_raw == "discover_start":
        return f"Поиск ссылок на товары{percent_tail}".strip()
    if stage_raw == "discover_done":
        return f"Поиск ссылок завершен{percent_tail}".strip()
    if stage_raw == "fetch_start":
        return f"Начата загрузка карточек{percent_tail}".strip()
    if stage_raw == "fetch_progress":
        return f"Обработка карточек{percent_tail}".strip()
    if stage_raw == "fetch_skip":
        return f"Пропуск проблемной карточки{percent_tail}".strip()
    if stage_raw in {"run_done", "done", "source_done"}:
        return f"Этап обработки завершен{percent_tail}".strip()
    if stage_label:
        return f"{stage_label}{percent_tail}".strip()
    return f"{evt_type}{percent_tail}".strip()


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
    current_source_progress_percent: float | None = None


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
    if job.status in {"in_progress", "queued"}:
        in_source_index = max(0, int(job.current_source_index or 0) - 1)
        progress = (in_source_index / total) * 100.0
    stage_text = str(job.current_stage or "").strip()
    if job.status in {"in_progress", "queued"} and stage_text:
        # Use detailed stage percent when available.
        stage_pct = _extract_percent_from_stage_text(stage_text)
        if stage_pct is None:
            stage_pct = _safe_float(getattr(job, "current_source_progress_percent", None))
        if stage_pct is not None:
            in_source_index = max(0, int(job.current_source_index or 0) - 1)
            per_source_weight = 100.0 / total
            progress = (in_source_index * per_source_weight) + ((stage_pct / 100.0) * per_source_weight)
        if progress >= 99.99:
            # Fallback: parse detailed stage text like "Карточки 100/200"
            # to avoid fake 100% while source is still running.
            try:
                import re
                m = re.search(r"(\d+)\s*/\s*(\d+)", stage_text)
                if m:
                    done = float(m.group(1))
                    total_items = float(m.group(2))
                    if total_items > 0:
                        progress = max(0.0, min(100.0, (done / total_items) * 100.0))
            except Exception:
                pass
        # In-progress job must never report 100%, otherwise UI looks frozen.
        # Keep headroom until terminal status is received.
        if progress >= 100.0:
            progress = 99.0
    products_progress = (job.db_upserts_done / max(1, job.expected_db_upserts)) * 100.0 if job.expected_db_upserts > 0 else progress
    if job.status in {"in_progress", "queued"} and products_progress >= 100.0:
        products_progress = 99.0
    strategy_sequence = _load_source_strategy_sequence(job.current_source_name)
    current_strategy = str(getattr(job, "current_strategy", "") or "").strip() or None
    strategy_total = len(strategy_sequence) if strategy_sequence else (1 if current_strategy else 0)
    strategy_index = 0
    if current_strategy and strategy_sequence:
        try:
            strategy_index = strategy_sequence.index(current_strategy) + 1
        except ValueError:
            strategy_index = 1
    elif current_strategy:
        strategy_index = 1

    # `db_upserts_done` can be zero on a fully successful re-sync when all items
    # were unchanged in DB. For admin progress we need "successfully processed",
    # not only "written to DB".
    processed_products = int(job.db_upserts_done or 0)
    if processed_products <= 0:
        processed_products = int(job.products_success or 0)
    expected_products = int(job.expected_db_upserts or 0)
    if expected_products <= 0:
        expected_products = max(int(job.products_success or 0) + int(job.products_error or 0), 0)

    response_status = job.status
    if response_status == "failed" and _source_has_password_gate_status(job.current_source_name):
        response_status = "password_protected"

    return {
        "job_id": job.job_id,
        "status": response_status,
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
        "processed_products": processed_products,
        "expected_products": expected_products,
        "failed_products": job.failed_products,
        "products_progress_percent": round(products_progress, 2),
        "current_source_name": job.current_source_name,
        "current_source_parser_type": _STRATEGY_RU.get(str(current_strategy or "").strip(), current_strategy),
        "current_strategy_index": strategy_index,
        "current_strategy_total": strategy_total,
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


def _source_has_password_gate_status(source_key: str | None) -> bool:
    key = str(source_key or "").strip()
    if not key:
        return False
    db = SessionLocal()
    try:
        source = _find_backend_source(db, key)
        if source is None:
            return False
        return str(getattr(source, "last_sync_status", "") or "").strip().lower() == "password_protected"
    except Exception:
        return False
    finally:
        db.close()


def _is_password_related_error(error_text: str | None) -> bool:
    raw = str(error_text or "").strip().lower()
    if not raw:
        return False
    patterns = (
        "storefront_password_gate",
        "/password",
        "password",
        "http 401",
        "status code 401",
        " 401 ",
        "unauthorized",
        "forbidden",
    )
    return any(p in raw for p in patterns)


def _probe_source_password_gate(source_key: str | None) -> bool:
    key = str(source_key or "").strip()
    if not key:
        return False
    db = SessionLocal()
    source_url = ""
    try:
        source = _find_backend_source(db, key)
        if source is None:
            return False
        source_url = str(getattr(source, "url", "") or "").strip()
    finally:
        db.close()
    if not source_url:
        return False
    try:
        res = requests.get(source_url, timeout=(5, 10), allow_redirects=True)
        final_url = str(res.url or "").lower()
        body = str(res.text or "").lower()
        if res.status_code in {401, 403}:
            return True
        if "/password" in final_url:
            return True
        markers = (
            'action="/password"',
            "action='/password'",
            'name="password"',
            "name='password'",
            'id="password"',
            "id='password'",
            "shopify-section-password",
            "enter using password",
            "storefront password",
        )
        return any(m in body for m in markers)
    except Exception:
        return False


def _default_supplier_id(db) -> int | None:
    from app.models.pricing import ParserSupplier

    row = (
        db.query(ParserSupplier.id)
        .filter(ParserSupplier.key == "eu")
        .order_by(ParserSupplier.id.asc())
        .first()
    )
    if row is not None:
        return int(row[0])
    row_any = db.query(ParserSupplier.id).order_by(ParserSupplier.id.asc()).first()
    if row_any is None:
        return None
    return int(row_any[0])


def _ensure_backend_source(db, source_key: str, sample_item: dict[str, Any] | None = None) -> ParserSource | None:
    existing = _find_backend_source(db, source_key)
    if existing is not None:
        return existing
    supplier_id = _default_supplier_id(db)
    if supplier_id is None:
        LOGGER.error("jobs_ingest: no supplier found, cannot create source key=%s", source_key)
        return None
    normalized_key = str(source_key or "").strip().lower()
    if not normalized_key:
        return None
    sample_url = ""
    if isinstance(sample_item, dict):
        sample_url = str(sample_item.get("source_product_url") or sample_item.get("url") or "").strip()
    base_url = f"https://{normalized_key}/"
    name = normalized_key
    row = ParserSource(
        name=name,
        url=base_url,
        enabled=True,
        supplier_id=supplier_id,
    )
    db.add(row)
    db.flush()
    LOGGER.info(
        "jobs_ingest: auto-created parser_source id=%s key=%s url=%s sample=%s",
        row.id,
        source_key,
        base_url,
        sample_url or "-",
    )
    return row


def _update_source_sync_telemetry(
    source_key: str,
    *,
    status: str | None = None,
    error_code: str | None = None,
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
            normalized_status = str(status).strip().lower()
            normalized_error = str(error_code or "").strip().lower()
            if normalized_error == "storefront_blocked:storefront_password_gate" or _is_password_related_error(normalized_error):
                # Explicit status for UI badges/labels.
                normalized_status = "password_protected"
            source.last_sync_status = normalized_status[:32] or None
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


def _variant_currency(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()[:3]
    if len(normalized) == 3:
        return normalized
    return None


def _derive_product_currency_from_variants(variants: Any) -> str | None:
    parsed = variants if isinstance(variants, list) else []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        normalized = _variant_currency(item.get("currency"))
        if normalized:
            return normalized
    return None


def _normalize_variants_with_source_lineage(
    *,
    source_key: str,
    source_product_url: str,
    variants: Any,
) -> list[dict[str, Any]]:
    parsed = variants if isinstance(variants, list) else []
    out: list[dict[str, Any]] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["source_key"] = str(item.get("source_key") or source_key).strip() or source_key
        item["source_product_url"] = str(item.get("source_product_url") or source_product_url).strip() or source_product_url
        source_variant_id = str(item.get("source_variant_id") or item.get("id") or "").strip()
        item["source_variant_id"] = source_variant_id or None
        source_variant_title = str(item.get("source_variant_title") or item.get("title") or "").strip()
        item["source_variant_title"] = source_variant_title or None
        item["currency"] = _variant_currency(item.get("currency"))
        out.append(item)
    return out


def _normalize_source_key_from_source(source: ParserSource) -> str:
    return str(urlparse(str(source.url or "")).netloc or source.name or "").strip().lower()


def _variant_fingerprint_payload(variant: dict[str, Any]) -> str:
    parts = [
        str(variant.get("sku") or "").strip().lower(),
        str(variant.get("title") or "").strip().lower(),
        str(variant.get("option1") or "").strip().lower(),
        str(variant.get("option2") or "").strip().lower(),
        str(variant.get("option3") or "").strip().lower(),
        str(variant.get("price") or "").strip().lower(),
        str(variant.get("currency") or "").strip().upper(),
    ]
    return "|".join(parts)


def _fallback_source_variant_id(variant: dict[str, Any]) -> str:
    sku = str(variant.get("sku") or "").strip()
    if sku:
        return sku
    raw = _variant_fingerprint_payload(variant)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"auto-{digest}"


def _fallback_source_variant_title(variant: dict[str, Any], source_variant_id: str) -> str:
    title = str(variant.get("title") or "").strip()
    if title:
        return title
    options = [
        str(variant.get("option1") or "").strip(),
        str(variant.get("option2") or "").strip(),
        str(variant.get("option3") or "").strip(),
    ]
    option_title = " / ".join([x for x in options if x])
    if option_title:
        return option_title
    return source_variant_id


def _origin_key(
    *,
    source_id: int,
    source_product_url: str,
    source_variant_id: str | None,
    source_variant_title: str | None,
) -> str:
    return "|".join(
        [
            str(int(source_id)),
            str(source_product_url or "").strip(),
            str(source_variant_id or "").strip(),
            str(source_variant_title or "").strip().lower(),
        ]
    )


def _materialize_product_variants_from_origins(db, *, product_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(ParserProductOriginVariant)
        .filter(ParserProductOriginVariant.product_id == int(product_id))
        .order_by(ParserProductOriginVariant.id.asc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload) if isinstance(row.payload, dict) else {}
        source_row = row.source
        source_key = str(payload.get("source_key") or "").strip()
        if not source_key and source_row is not None:
            source_key = _normalize_source_key_from_source(source_row)
        source_variant_id = (
            str(row.source_variant_id or payload.get("source_variant_id") or payload.get("id") or payload.get("sku") or "").strip()
            or _fallback_source_variant_id(payload)
        )
        source_variant_title = (
            str(row.source_variant_title or payload.get("source_variant_title") or payload.get("title") or "").strip()
            or _fallback_source_variant_title(payload, source_variant_id)
        )
        item = {
            "id": source_variant_id,
            "title": source_variant_title,
            "sku": str(row.sku or "").strip() or None,
            "price": _safe_float(row.price),
            "currency": str(row.currency or "").strip().upper() or None,
            "available": bool(row.available),
            "source_key": source_key or None,
            "source_product_url": str(row.source_product_url or "").strip() or None,
            "source_variant_id": source_variant_id,
            "source_variant_title": source_variant_title,
        }
        for k, v in payload.items():
            if k not in item:
                item[k] = v
        out.append(item)
    return out


def _upsert_origin_variants(
    db,
    *,
    product: ParserProduct,
    source: ParserSource,
    source_product_url: str,
    variants: list[dict[str, Any]],
) -> None:
    source_id = int(source.id)
    source_key = _normalize_source_key_from_source(source)
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        source_variant_id = (
            str(variant.get("source_variant_id") or variant.get("id") or "").strip()
            or _fallback_source_variant_id(variant)
        )
        source_variant_title = (
            str(variant.get("source_variant_title") or variant.get("title") or "").strip()
            or _fallback_source_variant_title(variant, source_variant_id)
        )
        candidate_source_url = str(variant.get("source_product_url") or source_product_url or "").strip() or source_product_url
        key = _origin_key(
            source_id=source_id,
            source_product_url=candidate_source_url,
            source_variant_id=source_variant_id,
            source_variant_title=source_variant_title,
        )
        row = (
            db.query(ParserProductOriginVariant)
            .filter(ParserProductOriginVariant.origin_key == key)
            .first()
        )
        if row is None:
            row = ParserProductOriginVariant(
                origin_key=key,
                product_id=int(product.id),
                source_id=source_id,
                source_product_url=candidate_source_url,
                source_variant_id=source_variant_id,
                source_variant_title=source_variant_title,
            )
            db.add(row)
        row.product_id = int(product.id)
        row.source_id = source_id
        row.source_product_url = candidate_source_url
        row.source_variant_id = source_variant_id
        row.source_variant_title = source_variant_title
        row.sku = str(variant.get("sku") or "").strip() or None
        row.price = _safe_float(variant.get("price"))
        row.currency = _variant_currency(variant.get("currency"))
        row.available = bool(variant.get("available", True))
        payload = dict(variant)
        payload["source_key"] = str(variant.get("source_key") or source_key).strip() or source_key
        payload["source_product_url"] = candidate_source_url
        payload["source_variant_id"] = source_variant_id
        payload["source_variant_title"] = source_variant_title
        row.payload = payload


def _upsert_products_from_items(source_key: str, items: list[dict[str, Any]]) -> tuple[int, int]:
    expected = len(items)
    if expected == 0:
        return (0, 0)
    db = SessionLocal()
    upserts = 0
    try:
        source = _ensure_backend_source(db, source_key, items[0] if items else None)
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
            query = db.query(ParserProduct).filter(ParserProduct.deleted_at.is_(None))
            row = None
            # Multi-source aggregation: match product globally first.
            if external_id:
                row = query.filter(ParserProduct.source_external_id == external_id).first()
            if row is None and canonical_url:
                row = query.filter(ParserProduct.canonical_url == canonical_url).first()
            if row is None and source_product_url:
                row = query.filter(ParserProduct.url == source_product_url).first()
            if row is None:
                row = (
                    query.join(ParserProductOriginVariant, ParserProductOriginVariant.product_id == ParserProduct.id)
                    .filter(ParserProductOriginVariant.source_id == int(source.id))
                    .filter(ParserProductOriginVariant.source_product_url == source_product_url)
                    .first()
                )
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
            images = item.get("images") if isinstance(item.get("images"), list) else []
            incoming_image_urls = [str(url).strip() for url in images if str(url).strip()]
            row.variants = _normalize_variants_with_source_lineage(
                source_key=source_key,
                source_product_url=source_product_url,
                variants=item.get("variants"),
            )
            incoming_currency = _derive_product_currency_from_variants(row.variants)
            if not incoming_currency and row.id is None:
                # Missing currency on all variants is ingest error.
                continue
            if row.id is None:
                db.flush()
            _upsert_origin_variants(
                db,
                product=row,
                source=source,
                source_product_url=source_product_url,
                variants=row.variants,
            )
            # Keep legacy JSON field in sync from normalized origin rows for existing UI/API consumers.
            row.variants = _materialize_product_variants_from_origins(db, product_id=int(row.id))
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

            incoming_status = str(item.get("status") or "").strip().lower()
            unavailable_reason = str(item.get("unavailable_reason") or "").strip().lower()
            weight_missing_in_payload = parsed_weight is None or (isinstance(parsed_weight, float) and parsed_weight <= 0.0)
            unavailable_due_weight = (
                incoming_status == "unavailable"
                and (
                    weight_missing_in_payload
                    or "weight" in unavailable_reason
                    or "missing_weight" in unavailable_reason
                )
            )

            # Manual moderation states must survive sync.
            if current_status in {"hidden", "unavailable"}:
                pass
            elif unavailable_due_weight:
                row.status = "unavailable"
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
    real_failed_sources: dict[str, str] = {}
    password_blocked_sources: set[str] = set()
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
        payload_stage = str(payload.get("current_stage") or "").strip()
        if payload_stage and not _stage_is_generic(payload_stage):
            payload_has_pct = _extract_percent_from_stage_text(payload_stage) is not None
            current_has_pct = _extract_percent_from_stage_text(agg_job.current_stage) is not None
            if payload_has_pct or not current_has_pct:
                agg_job.current_stage = payload_stage
        payload_progress = _safe_float(payload.get("progress_percent"))
        if payload_progress is not None and 0.0 <= payload_progress <= 100.0:
            agg_job.current_source_progress_percent = payload_progress
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
                source_key = str(evt_payload.get("source_key") or agg_job.current_source_name or "").strip() or agg_job.current_source_name
                if strategy_value:
                    agg_job.current_strategy = strategy_value
                event_progress = _safe_float(evt_payload.get("progress_percent"))
                if event_progress is None:
                    event_progress = _safe_float(evt_payload.get("percent"))
                if event_progress is None:
                    event_progress = _safe_float(evt_payload.get("pct"))
                if event_progress is not None and 0.0 <= event_progress <= 100.0:
                    agg_job.current_source_progress_percent = event_progress
                agg_job.current_stage = _format_stage_for_source(
                    source_key=source_key,
                    strategy_value=strategy_value,
                    evt_type=evt_type,
                    evt_payload=evt_payload,
                )
                stage_pct = _extract_percent_from_stage_text(agg_job.current_stage)
                if stage_pct is not None:
                    agg_job.current_source_progress_percent = stage_pct
                agg_job.current_source_name = source_key
                agg_job.current_source_index = int(evt_payload.get("source_index") or agg_job.current_source_index or 0)
            if evt_type == "source_started":
                source_key = str(evt_payload.get("source_key") or "").strip()
                if source_key:
                    source_started_at[source_key] = _utcnow()
            if evt_type == "source_finished":
                source_key = str(evt_payload.get("source_key") or "").strip()
                source_status = str(evt_payload.get("status") or "").strip().lower() or "completed"
                source_error = str(evt_payload.get("error") or "").strip() or None
                if source_status == "failed":
                    password_like = _is_password_related_error(source_error) or _source_has_password_gate_status(source_key)
                    if not password_like and source_error and "read timed out" in source_error.lower():
                        password_like = _probe_source_password_gate(source_key)
                    if password_like:
                        source_status = "password_protected"
                        source_error = "storefront_blocked:storefront_password_gate"
                        password_blocked_sources.add(source_key)
                        real_failed_sources.pop(source_key, None)
                    else:
                        real_failed_sources[source_key] = source_error or "failed"
                        password_blocked_sources.discard(source_key)
                else:
                    real_failed_sources.pop(source_key, None)
                    if source_status != "password_protected":
                        password_blocked_sources.discard(source_key)
                finished_at = _utcnow()
                started_at = source_started_at.get(source_key)
                duration_sec = None
                if started_at is not None:
                    duration_sec = int(max(0.0, (finished_at - started_at).total_seconds()))
                if source_key:
                    _update_source_sync_telemetry(
                        source_key,
                        status=source_status,
                        error_code=source_error,
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
            _rebuild_category_index_after_sync()
            agg_job.completed_at = _utcnow()
            agg_job.can_cancel = False
            if agg_job.status == "failed":
                if not real_failed_sources:
                    agg_job.status = "completed"
                    if password_blocked_sources:
                        names = ", ".join(sorted([x for x in password_blocked_sources if x])) or "источник"
                        agg_job.current_stage = f"Завершено (запароленные: {names})"
                    else:
                        agg_job.current_stage = "Синхронизация завершена"
                else:
                    failed_names = ", ".join(sorted(real_failed_sources.keys()))
                    agg_job.current_stage = f"Ошибки на источниках: {failed_names}"
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
                source_error = str(evt_payload.get("error") or "").strip() or None
                duration_sec = None
                if source_key in started:
                    duration_sec = int(max(0.0, (ts - started[source_key]).total_seconds()))
                _update_source_sync_telemetry(
                    source_key,
                    status=status,
                    error_code=source_error,
                    finished_at=ts,
                    duration_sec=duration_sec,
                )
        if next_cursor <= cursor:
            break
        cursor = next_cursor


def _rebuild_category_index_after_sync() -> None:
    db = SessionLocal()
    try:
        CategoryIndexService(db).rebuild_full()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to rebuild category index after sync")
    finally:
        db.close()


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
    selected_for_manual_mode = list(source_keys)
    if not selected_for_manual_mode:
        selected_for_manual_mode = [
            str(item.get("key") or "").strip()
            for item in _service_sources_list()
            if bool(item.get("enabled", True)) and bool(item.get("sync_enabled", True)) and str(item.get("key") or "").strip()
        ]
    manual_candidate_urls = _build_manual_candidate_urls_by_source(source_keys=selected_for_manual_mode)
    if manual_candidate_urls:
        request_payload["candidate_urls_by_source"] = manual_candidate_urls
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
