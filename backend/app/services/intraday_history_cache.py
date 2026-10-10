"""Short-lived Redis/memory cache for 1D/1W intraday history (#269)."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
import threading
import time
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.models.quote import CandleRecord
from app.services.quote_redis_cache import redis_configured

logger = logging.getLogger(__name__)

_KEY_PREFIX = "history:intraday:v1:"
_LOCK_PREFIX = "history:intraday:lock:v1:"
_DEFAULT_TTL_SECONDS = 45
_LOCK_SECONDS = 20

_memory_lock = threading.Lock()
_memory_entries: dict[str, tuple[float, str]] = {}
_memory_locks: dict[str, tuple[float, str]] = {}


def intraday_cache_ttl_seconds() -> int:
    return max(int(getattr(settings, "history_intraday_cache_ttl_seconds", _DEFAULT_TTL_SECONDS)), 15)


def cache_key(stock_id: str, range_name: str) -> str:
    return f"{stock_id}|{range_name}"


def get_cached_intraday(
    stock_id: str, range_name: str
) -> list[CandleRecord] | None:
    key = cache_key(stock_id, range_name)
    raw = _read_raw(key)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list):
            return None
        candles: list[CandleRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                return None
            candles.append(
                CandleRecord(
                    time=str(item["time"]),
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=int(item["volume"]),
                )
            )
        return candles
    except (TypeError, ValueError, KeyError):
        return None


def set_cached_intraday(
    stock_id: str, range_name: str, candles: list[CandleRecord]
) -> None:
    key = cache_key(stock_id, range_name)
    payload = json.dumps([asdict(candle) for candle in candles])
    ttl = intraday_cache_ttl_seconds()
    _write_raw(key, payload, ttl)


def try_acquire_intraday_lock(stock_id: str, range_name: str) -> str | None:
    key = cache_key(stock_id, range_name)
    token = uuid4().hex
    redis_state = _try_redis_lock(key, token)
    if redis_state is True:
        return token
    # Redis is the lock when it is configured. A held lock must not fall
    # through to the in-process lock, or every replica fetches.
    if redis_state is False:
        return None
    with _memory_lock:
        expires_at, owner = _memory_locks.get(key, (0.0, ""))
        now = time.monotonic()
        if expires_at > now and owner:
            return None
        _memory_locks[key] = (now + _LOCK_SECONDS, token)
        return token


def wait_for_cached_intraday(
    stock_id: str,
    range_name: str,
    *,
    timeout_seconds: float = _LOCK_SECONDS,
) -> list[CandleRecord] | None:
    """Poll until the lock holder fills the cache or the lock window ends."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        cached = get_cached_intraday(stock_id, range_name)
        if cached is not None:
            return cached
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def release_intraday_lock(stock_id: str, range_name: str, token: str) -> None:
    key = cache_key(stock_id, range_name)
    _release_redis_lock(key, token)
    with _memory_lock:
        expires_at, owner = _memory_locks.get(key, (0.0, ""))
        if owner == token:
            _memory_locks.pop(key, None)


def _read_raw(key: str) -> str | None:
    if redis_configured():
        try:
            from app.services.quote_redis_cache import _redis

            client = _redis()
            if client is not None:
                value = client.get(_KEY_PREFIX + key)
                if isinstance(value, bytes):
                    return value.decode("utf-8")
                if isinstance(value, str):
                    return value
        except Exception:  # noqa: BLE001
            logger.debug("Intraday history Redis read failed", exc_info=True)
    with _memory_lock:
        expires_at, raw = _memory_entries.get(key, (0.0, ""))
        if expires_at > time.monotonic() and raw:
            return raw
        _memory_entries.pop(key, None)
        return None


def _write_raw(key: str, payload: str, ttl: int) -> None:
    if redis_configured():
        try:
            from app.services.quote_redis_cache import _redis

            client = _redis()
            if client is not None:
                client.setex(_KEY_PREFIX + key, ttl, payload)
        except Exception:  # noqa: BLE001
            logger.debug("Intraday history Redis write failed", exc_info=True)
    with _memory_lock:
        _memory_entries[key] = (time.monotonic() + ttl, payload)


def _try_redis_lock(key: str, token: str) -> bool | None:
    """True when this caller owns the Redis lock, False when another caller does.

    None means Redis is not the lock (unconfigured, no client, or an error), so
    the caller may use the in-process lock.
    """
    if not redis_configured():
        return None
    try:
        from app.services.quote_redis_cache import _redis

        client = _redis()
        if client is None:
            return None
        return bool(
            client.set(_LOCK_PREFIX + key, token, nx=True, ex=_LOCK_SECONDS)
        )
    except Exception:  # noqa: BLE001
        return None


def _release_redis_lock(key: str, token: str) -> None:
    if not redis_configured():
        return
    try:
        from app.services.quote_redis_cache import _redis

        client = _redis()
        if client is None:
            return
        current = client.get(_LOCK_PREFIX + key)
        if current == token or (
            isinstance(current, bytes) and current.decode("utf-8") == token
        ):
            client.delete(_LOCK_PREFIX + key)
    except Exception:  # noqa: BLE001
        return
