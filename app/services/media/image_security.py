"""Security and rate limit helpers for image gateway."""

from __future__ import annotations

import socket
from collections import defaultdict, deque
from ipaddress import ip_address
from threading import Lock
from time import monotonic
from urllib.parse import urlparse

from fastapi import HTTPException, status

_RATE_LIMITER_LOCK = Lock()
_RATE_LIMITER_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def ensure_allowed_url(source_url: str) -> None:
    """Reject non-http(s) and private/reserved hosts to mitigate SSRF."""
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported image URL scheme")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed image URL")

    try:
        addresses = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Image host DNS resolution failed") from exc

    for entry in addresses:
        resolved_ip = ip_address(entry[4][0])
        if (
            resolved_ip.is_private
            or resolved_ip.is_loopback
            or resolved_ip.is_link_local
            or resolved_ip.is_multicast
            or resolved_ip.is_reserved
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Blocked image host address")


def check_rate_limit(client_ip: str, *, per_minute_limit: int) -> None:
    """Simple in-memory per-IP sliding window limiter."""
    now = monotonic()
    window_start = now - 60.0

    with _RATE_LIMITER_LOCK:
        bucket = _RATE_LIMITER_BUCKETS[client_ip]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= per_minute_limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
        bucket.append(now)
