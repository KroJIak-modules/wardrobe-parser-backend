from __future__ import annotations

import logging
import time

import redis

from app.core.config import settings


LOGGER = logging.getLogger(__name__)
_QUEUE_KEY = "catalog:weight-recalc:product-ids"


class WeightRuleRecalcQueue:
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
                LOGGER.warning("Weight recalc queue received non-integer item: %r", raw_value)
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
