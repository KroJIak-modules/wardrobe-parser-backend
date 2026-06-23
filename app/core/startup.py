"""Startup helpers for backend runtime bootstrap."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError


T = TypeVar("T")

LOGGER = logging.getLogger(__name__)
_RETRYABLE_OPERATIONAL_ERROR_MARKERS = (
    "could not translate host name",
    "could not connect to server",
    "connection refused",
    "connection timed out",
    "server closed the connection unexpectedly",
    "the database system is starting up",
)


def _is_retryable_db_error(exc: OperationalError) -> bool:
    message_parts = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        message_parts.append(str(orig))
    message = " ".join(part for part in message_parts if part).lower()
    return any(marker in message for marker in _RETRYABLE_OPERATIONAL_ERROR_MARKERS)


def run_db_bootstrap_with_retry(action: Callable[[], T], *, label: str, initial_delay_sec: float = 1.0, max_delay_sec: float = 10.0) -> T:
    """Run a bootstrap action and retry only on transient DB connectivity failures."""

    delay_sec = max(0.1, float(initial_delay_sec))
    max_delay_sec = max(delay_sec, float(max_delay_sec))
    attempt = 0

    while True:
        try:
            return action()
        except OperationalError as exc:
            if not _is_retryable_db_error(exc):
                raise
            attempt += 1
            LOGGER.warning(
                "%s delayed until database is reachable (attempt %s): %s",
                label,
                attempt,
                exc,
            )
            time.sleep(delay_sec)
            delay_sec = min(max_delay_sec, delay_sec * 2.0)
