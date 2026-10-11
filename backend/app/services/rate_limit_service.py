"""Redis-backed sliding counters with in-process fallback (#273)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

_memory_lock = threading.Lock()
# key -> list of monotonic timestamps within the window
_memory_hits: dict[str, list[float]] = {}


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int = 0


class RateLimitExceededError(Exception):
    def __init__(self, detail: str, *, retry_after_seconds: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after_seconds = retry_after_seconds


def peek_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    """Return whether ``key`` still has room. Does not consume a slot."""
    if limit <= 0:
        return RateLimitDecision(allowed=True, limit=limit, remaining=0)
    redis_decision = _peek_redis(key, limit=limit, window_seconds=window_seconds)
    if redis_decision is not None:
        return redis_decision
    return _peek_memory(key, limit=limit, window_seconds=window_seconds)


def check_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    """Increment the counter for ``key`` and return whether the call is allowed."""
    if limit <= 0:
        return RateLimitDecision(allowed=True, limit=limit, remaining=0)
    redis_decision = _check_redis(key, limit=limit, window_seconds=window_seconds)
    if redis_decision is not None:
        return redis_decision
    return _check_memory(key, limit=limit, window_seconds=window_seconds)


def require_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
    detail: str,
) -> None:
    decision = check_rate_limit(key, limit=limit, window_seconds=window_seconds)
    if decision.allowed:
        return
    raise RateLimitExceededError(
        detail,
        retry_after_seconds=max(1, decision.retry_after_seconds),
    )


def clear_rate_limits_for_tests() -> None:
    with _memory_lock:
        _memory_hits.clear()


def refund_rate_limit(key: str) -> None:
    """Give back one previously consumed slot. No-op when the bucket is empty."""
    if _refund_redis(key):
        return
    _refund_memory(key)


def _check_redis(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision | None:
    if not settings.redis_url.strip():
        return None
    try:
        from app.services.quote_redis_cache import run_redis

        redis_key = f"ratelimit:v1:{key}"

        def _incr(client):
            pipe = client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, int(window_seconds), nx=True)
            count, _ = pipe.execute()
            return int(count)

        count = run_redis(f"rate limit incr {key}", _incr, kind="write")
        if count is None:
            return None
        remaining = max(0, limit - count)
        if count <= limit:
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=remaining,
            )
        ttl = run_redis(
            f"rate limit ttl {key}",
            lambda client: int(client.ttl(redis_key) or window_seconds),
            kind="read",
        )
        return RateLimitDecision(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after_seconds=max(1, int(ttl or window_seconds)),
        )
    except Exception as exc:  # noqa: BLE001 - fall back to memory
        logger.debug("Rate limit Redis path failed: %s", exc)
        return None


def _refund_redis(key: str) -> bool:
    if not settings.redis_url.strip():
        return False
    try:
        from app.services.quote_redis_cache import run_redis

        redis_key = f"ratelimit:v1:{key}"

        def _decr(client):
            current = client.get(redis_key)
            if current is None or int(current) <= 0:
                return False
            client.decr(redis_key)
            return True

        result = run_redis(f"rate limit refund {key}", _decr, kind="write")
        if result is None:
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - fall back to memory
        logger.debug("Rate limit Redis refund failed: %s", exc)
        return False


def _refund_memory(key: str) -> None:
    with _memory_lock:
        hits = _memory_hits.get(key)
        if not hits:
            return
        hits.pop()
        if hits:
            _memory_hits[key] = hits
        else:
            _memory_hits.pop(key, None)


def _check_memory(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    now = time.monotonic()
    cutoff = now - float(window_seconds)
    with _memory_lock:
        hits = [ts for ts in _memory_hits.get(key, []) if ts >= cutoff]
        hits.append(now)
        count = len(hits)
        # Keep the hits that filled the bucket. Rejected extras are not stored.
        if len(hits) > limit:
            hits = hits[:limit]
        _memory_hits[key] = hits
        if count <= limit:
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=max(0, limit - count),
            )
        oldest = hits[0]
        retry_after = max(1, int(oldest + window_seconds - now) + 1)
        return RateLimitDecision(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after_seconds=retry_after,
        )


def _peek_redis(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision | None:
    if not settings.redis_url.strip():
        return None
    try:
        from app.services.quote_redis_cache import run_redis

        redis_key = f"ratelimit:v1:{key}"
        count = run_redis(
            f"rate limit peek {key}",
            lambda client: int(client.get(redis_key) or 0),
            kind="read",
        )
        if count is None:
            return None
        if count < limit:
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=max(0, limit - count),
            )
        ttl = run_redis(
            f"rate limit ttl {key}",
            lambda client: int(client.ttl(redis_key) or window_seconds),
            kind="read",
        )
        return RateLimitDecision(
            allowed=False,
            limit=limit,
            remaining=0,
            retry_after_seconds=max(1, int(ttl or window_seconds)),
        )
    except Exception as exc:  # noqa: BLE001 - fall back to memory
        logger.debug("Rate limit Redis peek failed: %s", exc)
        return None


def _peek_memory(
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    now = time.monotonic()
    cutoff = now - float(window_seconds)
    with _memory_lock:
        hits = [ts for ts in _memory_hits.get(key, []) if ts >= cutoff]
        _memory_hits[key] = hits
        count = len(hits)
    if count < limit:
        return RateLimitDecision(
            allowed=True,
            limit=limit,
            remaining=max(0, limit - count),
        )
    oldest = hits[0]
    retry_after = max(1, int(oldest + float(window_seconds) - now) + 1)
    return RateLimitDecision(
        allowed=False,
        limit=limit,
        remaining=0,
        retry_after_seconds=retry_after,
    )
