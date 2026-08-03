"""Optional Redis (and in-process) cache for quote payloads with SWR.

Hard TTL keeps entries available for stale-while-revalidate. Soft TTL
marks them fresh vs stale. Refresh locks prevent stampede when many
watchlist polls overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import threading
import time
from typing import Any

from app.core.config import settings
from app.models.quote import QuoteRecord

logger = logging.getLogger(__name__)

_KEY_PREFIX = "quote:v1:"
_LOCK_PREFIX = "quote:refresh:v1:"
# redis.Redis | False after first init attempt
_client: Any = None

# In-process fallback when Redis is unavailable (tests / no REDIS_URL).
_memory_lock = threading.Lock()
_memory_quotes: dict[str, tuple[float, str]] = {}
_memory_refresh_locks: dict[str, float] = {}


@dataclass(frozen=True)
class CachedQuoteEntry:
    quote: QuoteRecord
    cached_at: datetime
    age_seconds: float
    stale: bool


def redis_enabled() -> bool:
    return _redis() is not None


def soft_ttl_seconds() -> int:
    return max(int(settings.quote_cache_ttl_seconds), 1)


def hard_ttl_seconds() -> int:
    return max(int(settings.quote_cache_hard_ttl_seconds), soft_ttl_seconds())


def refresh_lock_seconds() -> int:
    return max(int(settings.quote_refresh_lock_seconds), 1)


def _redis() -> Any:
    """Lazy Redis client; False means disabled (no URL or connect failed)."""
    global _client
    if _client is False:
        return None
    if _client is not None:
        return _client
    if not settings.redis_url:
        _client = False
        return None
    try:
        import redis as redis_lib

        r = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        r.ping()
        _client = r
        logger.info("Quote Redis cache enabled")
        return _client
    except Exception as exc:
        logger.warning("Quote Redis cache disabled: %s", exc)
        _client = False
        return None


def _serialize(quote: QuoteRecord, *, cached_at: datetime) -> dict[str, Any]:
    cached_at_utc = cached_at.astimezone(UTC)
    return {
        "cached_at": cached_at_utc.isoformat().replace("+00:00", "Z"),
        "quote": {
            "id": quote.id,
            "last_price": quote.last_price,
            "change": quote.change,
            "change_percent": quote.change_percent,
            "previous_close": quote.previous_close,
            "timestamp": quote.timestamp,
            "is_market_open": quote.is_market_open,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "volume": quote.volume,
        },
    }


def _deserialize_quote(data: dict[str, Any]) -> QuoteRecord:
    def _of(key: str) -> float | None:
        v = data.get(key)
        return float(v) if v is not None else None

    vol = data.get("volume")
    return QuoteRecord(
        id=data["id"],
        last_price=float(data["last_price"]),
        change=float(data["change"]),
        change_percent=float(data["change_percent"]),
        previous_close=float(data["previous_close"]),
        timestamp=str(data["timestamp"]),
        is_market_open=bool(data["is_market_open"]),
        open=_of("open"),
        high=_of("high"),
        low=_of("low"),
        volume=int(vol) if vol is not None else None,
    )


def _parse_cached_at(raw: object, *, fallback: datetime) -> datetime:
    if isinstance(raw, str) and raw.strip():
        try:
            normalized = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            return fallback
    return fallback


def _entry_from_payload(
    raw: str,
    *,
    now: datetime | None = None,
) -> CachedQuoteEntry | None:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    current = now or datetime.now(UTC)
    quote_payload = payload.get("quote")
    if isinstance(quote_payload, dict):
        quote = _deserialize_quote(quote_payload)
        cached_at = _parse_cached_at(
            payload.get("cached_at"),
            fallback=current,
        )
    else:
        # Legacy payload: quote fields at top level, no cached_at.
        # Treat as soft-stale so SWR refreshes instead of looking brand-new.
        quote = _deserialize_quote(payload)
        cached_at = current - timedelta(seconds=soft_ttl_seconds())

    age_seconds = max(0.0, (current - cached_at).total_seconds())
    return CachedQuoteEntry(
        quote=quote,
        cached_at=cached_at,
        age_seconds=age_seconds,
        stale=age_seconds >= soft_ttl_seconds(),
    )


def get_cached_quote(stock_id: str) -> QuoteRecord | None:
    entry = get_cached_quote_entry(stock_id)
    return entry.quote if entry is not None else None


def get_cached_quote_entry(stock_id: str) -> CachedQuoteEntry | None:
    entries = get_cached_quote_entries([stock_id])
    return entries.get(stock_id)


def get_cached_quote_entries(
    stock_ids: list[str],
) -> dict[str, CachedQuoteEntry]:
    if not stock_ids:
        return {}
    now = datetime.now(UTC)
    r = _redis()
    if r is not None:
        return _get_entries_from_redis(r, stock_ids, now=now)
    return _get_entries_from_memory(stock_ids, now=now)


def set_cached_quote(stock_id: str, quote: QuoteRecord) -> None:
    cached_at = datetime.now(UTC)
    payload = json.dumps(_serialize(quote, cached_at=cached_at))
    hard_ttl = hard_ttl_seconds()
    r = _redis()
    if r is not None:
        try:
            r.setex(_KEY_PREFIX + stock_id, hard_ttl, payload)
        except Exception as exc:
            logger.debug("Redis set quote %s: %s", stock_id, exc)
    with _memory_lock:
        _memory_quotes[stock_id] = (time.monotonic() + hard_ttl, payload)


def try_acquire_refresh_lock(stock_id: str) -> bool:
    """Return True when this caller should perform the refresh."""
    ttl = refresh_lock_seconds()
    r = _redis()
    if r is not None:
        try:
            return bool(r.set(_LOCK_PREFIX + stock_id, "1", nx=True, ex=ttl))
        except Exception as exc:
            logger.debug("Redis refresh lock %s: %s", stock_id, exc)
    now = time.monotonic()
    with _memory_lock:
        expires_at = _memory_refresh_locks.get(stock_id)
        if expires_at is not None and expires_at > now:
            return False
        _memory_refresh_locks[stock_id] = now + ttl
        return True


def release_refresh_lock(stock_id: str) -> None:
    r = _redis()
    if r is not None:
        try:
            r.delete(_LOCK_PREFIX + stock_id)
        except Exception as exc:
            logger.debug("Redis release refresh lock %s: %s", stock_id, exc)
    with _memory_lock:
        _memory_refresh_locks.pop(stock_id, None)


def clear_quote_cache_for_tests() -> None:
    """Test helper: drop in-process cache/locks and forget Redis client."""
    global _client
    with _memory_lock:
        _memory_quotes.clear()
        _memory_refresh_locks.clear()
    _client = None


def _get_entries_from_redis(
    client: Any,
    stock_ids: list[str],
    *,
    now: datetime,
) -> dict[str, CachedQuoteEntry]:
    keys = [_KEY_PREFIX + stock_id for stock_id in stock_ids]
    try:
        values = client.mget(keys)
    except Exception as exc:
        logger.debug("Redis mget quotes failed: %s", exc)
        return {}
    entries: dict[str, CachedQuoteEntry] = {}
    for stock_id, raw in zip(stock_ids, values, strict=False):
        if not raw:
            continue
        entry = _entry_from_payload(raw, now=now)
        if entry is not None:
            entries[stock_id] = entry
    return entries


def _get_entries_from_memory(
    stock_ids: list[str],
    *,
    now: datetime,
) -> dict[str, CachedQuoteEntry]:
    entries: dict[str, CachedQuoteEntry] = {}
    now_mono = time.monotonic()
    with _memory_lock:
        for stock_id in stock_ids:
            item = _memory_quotes.get(stock_id)
            if item is None:
                continue
            expires_at, raw = item
            if expires_at <= now_mono:
                _memory_quotes.pop(stock_id, None)
                continue
            entry = _entry_from_payload(raw, now=now)
            if entry is not None:
                entries[stock_id] = entry
    return entries
