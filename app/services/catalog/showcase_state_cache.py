"""Кэш состояния витрины в памяти процесса.

Витрина пересобирает таксономию и список дизайнеров в каждом HTTP-запросе:
это около двух с половиной секунд SQL и Python, потому что считаются
дерево разделов и количество товаров по каждому бренду. Главная страница
делает пять таких запросов параллельно — и все пять повторяют одну и ту же
работу.

Данные меняются только при правках в админке, поэтому они хранятся в
памяти процесса, а при изменении таксономии кэш сбрасывается явно. Срок
жизни — страховка на случай изменения каталога извне (импорт, пересчёт цен),
которое не проходит через админку.
"""

from __future__ import annotations

import threading
import time

from app.core.config import settings

TAXONOMY_STATE_CACHE_KEY = "taxonomy"
DESIGNER_STATE_CACHE_KEY = "designers"


class ShowcaseStateCache:
    """Процессный кэш состояний витрины с общим сроком жизни."""

    def __init__(self, ttl_sec: float = 60.0) -> None:
        self._ttl_sec = max(1.0, float(ttl_sec))
        self._lock = threading.Lock()
        self._values: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= time.monotonic():
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._values[key] = (time.monotonic() + self._ttl_sec, value)

    def invalidate(self) -> None:
        with self._lock:
            self._values.clear()


_state_cache = ShowcaseStateCache(ttl_sec=float(getattr(settings, "showcase_state_cache_ttl_sec", 60) or 60))


def state_cache() -> ShowcaseStateCache:
    return _state_cache
