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
    if code == "canceled":
        return "Синхронизация была отменена."
    if code == "source_failed":
        return "Источник завершился с ошибкой. Попробуй повторить синхронизацию позже."
    if code:
        return "Во время синхронизации произошла ошибка. Попробуй повторить запуск позже."
    return raw or None
