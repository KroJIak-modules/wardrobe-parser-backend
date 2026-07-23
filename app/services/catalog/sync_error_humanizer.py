from __future__ import annotations


def normalize_sync_error_code(error_message: str | None, error_code: str | None = None) -> str | None:
    code = str(error_code or "").strip().lower()
    message = str(error_message or "").strip()
    text = f"{code}\n{message}".lower()
    if not text.strip():
        return None
    if "backend_restarted" in text:
        return "backend_restarted"
    if code in {"canceled", "cancelled"} or "canceled" in text or "cancelled" in text:
        return "canceled"
    if (
        code in {"connecttimeout", "readtimeout", "timeout", "service_timeout"}
        or "timed out" in text
        or "read timeout" in text
        or "connect timeout" in text
    ):
        return "service_timeout"
    if code in {"storefront_blocked", "waf_blocked", "access_denied"} or any(
        marker in text
        for marker in ("storefront_blocked", "waf", "cloudflare", "access denied", "forbidden", "captcha")
    ):
        return "source_blocked"
    if code in {"invalid_response", "parse_error", "schema_error", "decode_error"} or any(
        marker in text
        for marker in ("invalid json", "jsondecode", "unexpected response", "schema validation")
    ):
        return "source_response_invalid"
    if code in {"product_not_found", "catalog_empty", "no_products_found"} or any(
        marker in text
        for marker in ("product_not_found", "catalog_empty", "no products found")
    ):
        return "catalog_empty"
    if code in {"configerror", "source_config_invalid"} or any(
        marker in text
        for marker in ("missing source.config", "invalid source.config", "source disabled", "unknown strategy", "unknown adapter_key")
    ):
        return "source_config_invalid"
    if code in {"browser_runtime_failed", "browser_extension_failed"} or any(
        marker in text
        for marker in ("browser_extension_", "chromium not found", "xvfb not found")
    ):
        return "browser_runtime_failed"
    if code in {"backend_sources_unavailable", "backend_source_bootstrap_failed"} or any(
        marker in text
        for marker in ("backend_sources_unavailable", "backend_source_bootstrap_failed")
    ):
        return "source_registry_unavailable"
    if (
        code in {"connectionerror", "newconnectionerror", "configerror", "service_unavailable"}
        or "httpconnectionpool(" in text
        or "failed to establish a new connection" in text
        or "connection refused" in text
        or "failed to resolve" in text
        or "name resolution error" in text
        or "nameresolutionerror" in text
        or "service api unavailable" in text
    ):
        return "service_unavailable"
    if code in {"source_failed", "source_report_error"} or "source run failed without reported error" in text:
        return "source_failed"
    return code or None


def humanize_sync_error(error_message: str | None, error_code: str | None = None) -> str | None:
    raw = str(error_message or "").strip()
    code = normalize_sync_error_code(raw, error_code)
    if not raw and not code:
        return None
    if code == "backend_restarted":
        return "Синхронизация прервалась из-за перезапуска сервера."
    if code == "service_unavailable":
        return "Сервис синхронизации временно недоступен. Попробуй повторить запуск позже."
    if code == "service_timeout":
        return "Сервис синхронизации отвечает слишком долго. Попробуй повторить запуск позже."
    if code == "source_blocked":
        return "Сайт источника временно ограничил автоматический доступ. Попробуй повторить синхронизацию позже."
    if code == "source_response_invalid":
        return "Источник вернул данные в неожиданном формате. Его настройки требуют проверки."
    if code == "catalog_empty":
        return "Источник не вернул доступных товаров. Проверь витрину источника и повтори запуск позже."
    if code == "source_config_invalid":
        return "Настройки источника требуют проверки. Синхронизация этого источника не выполнена."
    if code == "browser_runtime_failed":
        return "Не удалось выполнить браузерную проверку источника. Попробуй повторить запуск позже."
    if code == "source_registry_unavailable":
        return "Не удалось получить настройки источников. Попробуй повторить запуск позже."
    if code == "canceled":
        return "Синхронизация была отменена."
    if code == "source_failed":
        return "Источник завершился с ошибкой. Попробуй повторить синхронизацию позже."
    if code:
        return "Во время синхронизации произошла ошибка. Попробуй повторить запуск позже."
    return raw or None
