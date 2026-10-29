"""Small optional Redis cache. Cache failures never make API reads fail."""
import json
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)
_redis = None
_initialized = False


def _client():
    global _redis, _initialized
    if not _initialized:
        _initialized = True
        if settings.redis_url:
            try:
                from redis import Redis
                _redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2)
            except Exception:
                logger.exception("Redis cache initialization failed")
    return _redis


def get_json(key: str):
    client = _client()
    if client is None:
        return None
    try:
        value = client.get(key)
        return json.loads(value) if value is not None else None
    except Exception:
        logger.warning("Redis cache read failed", exc_info=True)
        return None


def set_json(key: str, value, ttl: int | None = None) -> None:
    client = _client()
    if client is not None:
        try:
            client.setex(key, ttl or settings.cache_ttl_seconds, json.dumps(value, separators=(",", ":")))
        except Exception:
            logger.warning("Redis cache write failed", exc_info=True)


def invalidate_documents() -> None:
    client = _client()
    if client is not None:
        try:
            keys = list(client.scan_iter(match="documents:v1:*", count=200))
            if keys:
                client.delete(*keys)
        except Exception:
            logger.warning("Redis cache invalidation failed", exc_info=True)
