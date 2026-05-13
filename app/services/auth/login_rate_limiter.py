from __future__ import annotations

import logging

import redis

from app.core.config import settings


LOGGER = logging.getLogger(__name__)
_WINDOW_SEC = 300
_MAX_ATTEMPTS = 10


class LoginRateLimiter:
    def __init__(self) -> None:
        self._client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.5,
            socket_timeout=1.5,
            health_check_interval=30,
        )

    @staticmethod
    def _key(client_key: str) -> str:
        return f"auth:login:attempts:{client_key}"

    def is_limited(self, client_key: str) -> bool:
        key = self._key(client_key)
        try:
            current = self._client.get(key)
            count = int(current) if current is not None else 0
            return count >= _MAX_ATTEMPTS
        except (redis.RedisError, ValueError):
            LOGGER.exception("Redis login rate-limit read failed")
            return False

    def register_failed_attempt(self, client_key: str) -> None:
        key = self._key(client_key)
        try:
            with self._client.pipeline() as pipe:
                pipe.incr(key)
                pipe.expire(key, _WINDOW_SEC)
                pipe.execute()
        except redis.RedisError:
            LOGGER.exception("Redis login rate-limit write failed")
