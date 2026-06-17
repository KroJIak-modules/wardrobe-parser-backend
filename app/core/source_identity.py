from __future__ import annotations

from urllib.parse import urlparse


def normalize_host(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = str(parsed.hostname or parsed.netloc or parsed.path or "").strip().lower()
    except Exception:
        host = value.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_listing_url(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = normalize_host(value)
    path = (parsed.path or "/").strip() or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return f"{host}{path}" if host else path

