"""Redis-backed login limiter with an in-process fallback for local development."""
import hashlib
import threading
import time

from app.core.config import settings
from app.services.cache import _client

_lock = threading.Lock()
_local: dict[str, tuple[int, float]] = {}


def login_limit_exceeded(identity: str) -> bool:
    key = "ratelimit:login:" + hashlib.sha256(identity.encode()).hexdigest()
    client = _client()
    if client is not None:
        try:
            count = client.incr(key)
            if count == 1:
                client.expire(key, settings.login_rate_window_seconds)
            return count > settings.login_rate_limit
        except Exception:
            pass
    now = time.monotonic()
    with _lock:
        if len(_local) > 10000:
            for old_key, (_, expiry) in list(_local.items()):
                if expiry <= now:
                    _local.pop(old_key, None)
        count, expires = _local.get(key, (0, now + settings.login_rate_window_seconds))
        if expires <= now:
            count, expires = 0, now + settings.login_rate_window_seconds
        count += 1
        _local[key] = (count, expires)
        return count > settings.login_rate_limit
