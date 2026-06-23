from __future__ import annotations

import logging
import time

import redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.session_hooks import register_after_commit


LOGGER = logging.getLogger(__name__)
_QUEUE_KEY = "catalog:filter-assignments:product-ids"
_SESSION_PENDING_IDS_KEY = "filter_assignment_pending_product_ids"
_SESSION_FLUSH_REGISTERED_KEY = "filter_assignment_pending_product_ids_registered"


class ProductFilterAssignmentQueue:
    def __init__(self, client: redis.Redis | None = None) -> None:
        self._client = client or redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
            health_check_interval=30,
        )

    @staticmethod
    def _normalize_product_ids(product_ids: set[int] | list[int] | tuple[int, ...]) -> list[int]:
        normalized: list[int] = []
        seen: set[int] = set()
        for raw_product_id in product_ids:
            product_id = int(raw_product_id)
            if product_id <= 0 or product_id in seen:
                continue
            seen.add(product_id)
            normalized.append(product_id)
        return normalized

    def enqueue_product_ids(self, product_ids: set[int] | list[int] | tuple[int, ...]) -> int:
        normalized = self._normalize_product_ids(product_ids)
        if not normalized:
            return 0
        now_score = float(time.time())
        payload = {str(product_id): now_score for product_id in normalized}
        return int(self._client.zadd(_QUEUE_KEY, payload))

    def enqueue_product_ids_after_commit(self, session: Session, product_ids: set[int] | list[int] | tuple[int, ...]) -> None:
        normalized = self._normalize_product_ids(product_ids)
        if not normalized:
            return
        pending = session.info.setdefault(_SESSION_PENDING_IDS_KEY, set())
        pending.update(normalized)
        if session.info.get(_SESSION_FLUSH_REGISTERED_KEY):
            return
        session.info[_SESSION_FLUSH_REGISTERED_KEY] = True

        def _flush() -> None:
            queued_ids = sorted({int(product_id) for product_id in pending if int(product_id) > 0})
            pending.clear()
            session.info.pop(_SESSION_PENDING_IDS_KEY, None)
            session.info.pop(_SESSION_FLUSH_REGISTERED_KEY, None)
            if not queued_ids:
                return
            try:
                self.enqueue_product_ids(queued_ids)
            except Exception:
                LOGGER.exception("Failed to enqueue filter assignment recalculation for %s products", len(queued_ids))

        register_after_commit(session, _flush)

    def pop_ready_batch(self, *, limit: int, debounce_sec: int) -> list[int]:
        batch_size = max(1, int(limit))
        cutoff = float(time.time() - max(0, int(debounce_sec)))
        raw_items = self._client.zrangebyscore(_QUEUE_KEY, min=0, max=cutoff, start=0, num=batch_size)
        if not raw_items:
            return []

        product_ids: list[int] = []
        seen: set[int] = set()
        for raw_value in raw_items:
            try:
                product_id = int(raw_value)
            except (TypeError, ValueError):
                LOGGER.warning("Filter assignment queue received non-integer item: %r", raw_value)
                continue
            if product_id <= 0 or product_id in seen:
                continue
            seen.add(product_id)
            product_ids.append(product_id)
        if product_ids:
            self._client.zrem(_QUEUE_KEY, *[str(product_id) for product_id in product_ids])
        return product_ids

    def size(self) -> int:
        return int(self._client.zcard(_QUEUE_KEY))
