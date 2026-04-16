"""Redis-backed cache for image payloads."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache

import redis

from app.core.config import settings


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedImagePayload:
    body: bytes
    content_type: str


class RedisImageCache:
    def __init__(self, client: redis.Redis, ttl_sec: int):
        self._client = client
        self._ttl_sec = int(ttl_sec)

    @staticmethod
    def _build_key(*, image_id: int, etag: str) -> str:
        digest = hashlib.sha1(etag.encode("utf-8")).hexdigest()
        return f"image:gateway:v1:{int(image_id)}:{digest}"

    def get(self, *, image_id: int, etag: str) -> CachedImagePayload | None:
        key = self._build_key(image_id=image_id, etag=etag)
        try:
            data = self._client.hgetall(key)
        except redis.RedisError:
            LOGGER.exception("Redis image cache read failed")
            return None
        if not data:
            return None
        body = data.get(b"body")
        content_type_raw = data.get(b"content_type")
        if not body or not content_type_raw:
            return None
        return CachedImagePayload(body=bytes(body), content_type=content_type_raw.decode("utf-8", errors="ignore") or "image/jpeg")

    def set(self, *, image_id: int, etag: str, body: bytes, content_type: str) -> None:
        key = self._build_key(image_id=image_id, etag=etag)
        payload = {
            "body": body,
            "content_type": (content_type or "image/jpeg"),
        }
        try:
            with self._client.pipeline() as pipe:
                pipe.hset(key, mapping=payload)
                pipe.expire(key, self._ttl_sec)
                pipe.execute()
        except redis.RedisError:
            LOGGER.exception("Redis image cache write failed")


@lru_cache(maxsize=1)
def get_image_cache() -> RedisImageCache:
    client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=1.5,
        socket_timeout=1.5,
        health_check_interval=30,
    )
    return RedisImageCache(client=client, ttl_sec=settings.image_cache_redis_ttl_sec)
