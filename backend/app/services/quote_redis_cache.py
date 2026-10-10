"""Optional Redis (and in-process) cache for quote payloads with SWR.

Hard TTL keeps entries available for stale-while-revalidate. Soft TTL
marks them fresh vs stale. Refresh locks prevent stampede when many
watchlist polls overlap.

Redis is optional: when ``REDIS_URL`` is unset the in-process cache is used.
When Redis is configured, transient connect/runtime failures are recoverable
after a rate-limited backoff without restarting the backend process.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import json
import logging
import random
import threading
import time
from typing import Any, Callable, Literal, TypeVar
from uuid import uuid4

from app.core.config import settings
from app.models.quote import QuoteRecord
from app.services.market_clock import MARKET_TIMEZONES, is_market_open, quote_session_is_live
from app.services.tw_price_limits import restore_cached_price_limit

logger = logging.getLogger(__name__)

# v2 drops entries cached before Taiwan quote integrity checks (#258).
_KEY_PREFIX = "quote:v2:"
_LOCK_PREFIX = "quote:refresh:v1:"

RedisSuccessKind = Literal["ping", "read", "write"]
T = TypeVar("T")

# redis.Redis | False after a failed/disabled init attempt | None before first attempt
_client: Any = None
_client_lock = threading.Lock()
_connect_cond = threading.Condition(_client_lock)
_connecting = False
# When connect fails, retry after this monotonic deadline (0 = retry immediately).
_client_retry_after_monotonic: float = 0.0
_consecutive_failures: int = 0
_last_success_at: datetime | None = None
_last_success_ping_at: datetime | None = None
_last_success_read_at: datetime | None = None
_last_success_write_at: datetime | None = None
_last_error_at: datetime | None = None
_last_error_category: str | None = None

# In-process fallback when Redis is unavailable (tests / no REDIS_URL).
_memory_lock = threading.Lock()
_memory_quotes: dict[str, tuple[float, str]] = {}
# stock_id -> (expires_at_monotonic, owner_token)
_memory_refresh_locks: dict[str, tuple[float, str]] = {}
# stock_id -> fail_until_monotonic (backoff after failed background refresh)
_memory_refresh_failures: dict[str, float] = {}

_RELEASE_IF_OWNER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""

_RENEW_IF_OWNER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("expire", KEYS[1], tonumber(ARGV[2]))
end
return 0
"""


@dataclass(frozen=True)
class CachedQuoteEntry:
    quote: QuoteRecord
    cached_at: datetime
    age_seconds: float
    stale: bool


def redis_configured() -> bool:
    return bool(settings.redis_url and settings.redis_url.strip())


def redis_enabled() -> bool:
    """True when a live Redis client is currently available."""
    return _redis() is not None


def soft_ttl_seconds() -> int:
    return max(int(settings.quote_cache_ttl_seconds), 1)


def hard_ttl_seconds() -> int:
    return max(int(settings.quote_cache_hard_ttl_seconds), soft_ttl_seconds())


def refresh_lock_seconds() -> int:
    return max(int(settings.quote_refresh_lock_seconds), 1)


def refresh_failure_backoff_seconds() -> int:
    return refresh_lock_seconds()


_UTC_Z_SUFFIX = "Z"
_UTC_OFFSET_SUFFIX = "+00:00"


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace(_UTC_OFFSET_SUFFIX, _UTC_Z_SUFFIX)


def get_quote_cache_health() -> dict[str, Any]:
    """Diagnostic view of quote-cache Redis state (no credentials/URL)."""
    configured = redis_configured()
    with _client_lock:
        client = _client
        retry_after = _client_retry_after_monotonic
        failures = _consecutive_failures
        last_success = _last_success_at
        last_ping = _last_success_ping_at
        last_read = _last_success_read_at
        last_write = _last_success_write_at
        last_error = _last_error_at
        last_category = _last_error_category

    connected = client is not None and client is not False
    now_mono = time.monotonic()
    if not configured:
        retry_after_seconds: float | None = None
        state = "not_configured"
    elif connected:
        retry_after_seconds = 0.0
        state = "connected"
    elif retry_after > now_mono:
        retry_after_seconds = round(retry_after - now_mono, 3)
        state = "retry_wait"
    else:
        retry_after_seconds = 0.0
        state = "disconnected"

    return {
        "configured": configured,
        "state": state,
        "connected": connected if configured else False,
        "backend": "redis" if connected else "memory",
        "last_success_at": _iso_utc(last_success),
        "last_success_ping_at": _iso_utc(last_ping),
        "last_success_read_at": _iso_utc(last_read),
        "last_success_write_at": _iso_utc(last_write),
        "last_error_at": _iso_utc(last_error),
        "last_error_category": last_category,
        "consecutive_failures": failures,
        "retry_after_seconds": retry_after_seconds,
    }


def _retry_base_seconds() -> float:
    return max(float(settings.quote_redis_retry_base_seconds), 1.0)


def _retry_max_seconds() -> float:
    return max(float(settings.quote_redis_retry_max_seconds), _retry_base_seconds())


def _retry_jitter_seconds() -> float:
    return max(float(settings.quote_redis_retry_jitter_seconds), 0.0)


def _next_retry_delay_seconds(failure_count: int) -> float:
    """Exponential backoff with jitter; failure_count is 1-based after a failure.

    Doubles from the base until the max cap so large failure counts cannot
    overflow ``2**exponent`` before ``min()`` applies.
    """
    delay = _retry_base_seconds()
    max_delay = _retry_max_seconds()
    for _ in range(max(failure_count - 1, 0)):
        if delay >= max_delay:
            delay = max_delay
            break
        delay = min(delay * 2.0, max_delay)
    jitter = random.uniform(0.0, _retry_jitter_seconds()) if _retry_jitter_seconds() else 0.0
    return min(delay + jitter, max_delay + _retry_jitter_seconds())


def _classify_redis_error(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timeout" in message:
        return "timeout"
    if (
        "connect" in name
        or "connection" in name
        or "connection" in message
        or isinstance(exc, (ConnectionError, OSError))
    ):
        return "connect"
    if "auth" in name or "auth" in message or "password" in message:
        return "auth"
    return "unknown"


def _is_connection_failure(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    try:
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError

        if isinstance(exc, (RedisConnectionError, RedisTimeoutError)):
            return True
    except ImportError:
        pass
    category = _classify_redis_error(exc)
    return category in {"connect", "timeout"}


def _note_redis_success(kind: RedisSuccessKind) -> None:
    global _consecutive_failures, _last_success_at, _client_retry_after_monotonic
    global _last_success_ping_at, _last_success_read_at, _last_success_write_at
    now = datetime.now(UTC)
    with _client_lock:
        _consecutive_failures = 0
        _client_retry_after_monotonic = 0.0
        _last_success_at = now
        if kind == "ping":
            _last_success_ping_at = now
        elif kind == "read":
            _last_success_read_at = now
        else:
            _last_success_write_at = now


def _note_redis_failure(
    exc: BaseException,
    *,
    invalidate: bool,
    clear_connecting: bool = False,
) -> None:
    global _client, _client_retry_after_monotonic, _consecutive_failures
    global _last_error_at, _last_error_category, _connecting
    category = _classify_redis_error(exc)
    with _client_lock:
        _consecutive_failures += 1
        failures = _consecutive_failures
        try:
            delay = _next_retry_delay_seconds(failures)
        except Exception:
            delay = _retry_max_seconds() + _retry_jitter_seconds()
        _client_retry_after_monotonic = time.monotonic() + delay
        _last_error_at = datetime.now(UTC)
        _last_error_category = category
        if invalidate:
            _client = False
        if clear_connecting:
            _connecting = False
            _connect_cond.notify_all()
    logger.warning(
        "Quote Redis cache failure (category=%s, failures=%s, retry_in=%.1fs): %s",
        category,
        failures,
        delay,
        exc,
    )


def _redis() -> Any:
    """Lazy Redis client; reconnects after cooldown when connect/runtime fails.

    Concurrent callers singleflight reconnect attempts so a cooldown expiry does
    not stampede ``from_url``/``ping`` inside one process.
    """
    global _client, _connecting
    if not redis_configured():
        with _client_lock:
            _client = False
        return None

    with _client_lock:
        while True:
            if _client is not None and _client is not False:
                return _client
            if _client is False and time.monotonic() < _client_retry_after_monotonic:
                return None
            if _connecting:
                _connect_cond.wait(timeout=3.0)
                continue
            _connecting = True
            break

    try:
        import redis as redis_lib

        r = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        r.ping()
        with _client_lock:
            _client = r
            _connecting = False
            _connect_cond.notify_all()
        _note_redis_success("ping")
        logger.info("Quote Redis cache enabled")
        return r
    except Exception as exc:
        _note_redis_failure(exc, invalidate=True, clear_connecting=True)
        return None


def _run_redis(
    operation: str,
    fn: Callable[[Any], T],
    *,
    kind: RedisSuccessKind,
) -> T | None:
    """Execute a Redis callable; invalidate client on connection/runtime failures."""
    client = _redis()
    if client is None:
        return None
    try:
        result = fn(client)
        _note_redis_success(kind)
        return result
    except Exception as exc:
        if _is_connection_failure(exc):
            _note_redis_failure(exc, invalidate=True)
        else:
            logger.debug("Redis %s failed: %s", operation, exc)
        return None


def run_redis(
    operation: str,
    fn: Callable[[Any], T],
    *,
    kind: RedisSuccessKind = "read",
) -> T | None:
    """Public shared-client helper for Redis ops outside this module.

    Prefer this over calling ``_redis()`` directly so connection drops invalidate
    the client and update ``/health`` quote_cache state.
    """
    return _run_redis(operation, fn, kind=kind)


def _serialize(quote: QuoteRecord, *, cached_at: datetime) -> dict[str, Any]:
    cached_at_utc = cached_at.astimezone(UTC)
    return {
        "cached_at": cached_at_utc.isoformat().replace(_UTC_OFFSET_SUFFIX, _UTC_Z_SUFFIX),
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
            "price_limit_status": quote.price_limit_status,
            "reference_price": quote.reference_price,
            "limit_up_price": quote.limit_up_price,
            "limit_down_price": quote.limit_down_price,
        },
    }


def _deserialize_quote(
    data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> QuoteRecord:
    def _of(key: str) -> float | None:
        v = data.get(key)
        return float(v) if v is not None else None

    vol = data.get("volume")
    last_price = float(data["last_price"])
    status, reference_price, limit_up_price, limit_down_price = restore_cached_price_limit(
        last_price=last_price,
        status=data.get("price_limit_status"),
        reference_price=data.get("reference_price"),
        limit_up_price=data.get("limit_up_price"),
        limit_down_price=data.get("limit_down_price"),
    )
    quote = QuoteRecord(
        id=data["id"],
        last_price=last_price,
        change=float(data["change"]),
        change_percent=float(data["change_percent"]),
        previous_close=float(data["previous_close"]),
        timestamp=str(data["timestamp"]),
        is_market_open=bool(data["is_market_open"]),
        open=_of("open"),
        high=_of("high"),
        low=_of("low"),
        volume=int(vol) if vol is not None else None,
        price_limit_status=status,
        reference_price=reference_price,
        limit_up_price=limit_up_price,
        limit_down_price=limit_down_price,
    )
    return _with_live_market_open(quote, now=now)


def _with_live_market_open(
    quote: QuoteRecord,
    *,
    now: datetime | None = None,
) -> QuoteRecord:
    """Derive ``is_market_open`` at read time so a cached closing print cannot stay open (#262)."""
    market = quote.id.split(":", 1)[0]
    if market not in MARKET_TIMEZONES:
        return quote
    try:
        quote_time = datetime.fromisoformat(quote.timestamp)
    except ValueError:
        return replace(quote, is_market_open=False)
    # Holiday calendars are preloaded before this runs under the cache lock.
    live = quote_session_is_live(
        market,
        quote_time,
        now=now,
        allow_holiday_fetch=False,
    )
    if live == quote.is_market_open:
        return quote
    return replace(quote, is_market_open=live)


def _parse_cached_at(raw: object, *, fallback: datetime) -> datetime:
    if isinstance(raw, str) and raw.strip():
        try:
            normalized = raw.replace(_UTC_Z_SUFFIX, _UTC_OFFSET_SUFFIX)
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
        if not isinstance(payload, dict):
            return None

        current = now or datetime.now(UTC)
        quote_payload = payload.get("quote")
        if isinstance(quote_payload, dict):
            quote = _deserialize_quote(quote_payload, now=current)
            cached_at = _parse_cached_at(
                payload.get("cached_at"),
                fallback=current,
            )
        else:
            # Legacy payload: quote fields at top level, no cached_at.
            # Treat as soft-stale so SWR refreshes instead of looking brand-new.
            quote = _deserialize_quote(payload, now=current)
            cached_at = current - timedelta(seconds=soft_ttl_seconds())

        age_seconds = max(0.0, (current - cached_at).total_seconds())
        return CachedQuoteEntry(
            quote=quote,
            cached_at=cached_at,
            age_seconds=age_seconds,
            stale=age_seconds >= soft_ttl_seconds(),
        )
    except (TypeError, ValueError, KeyError):
        return None


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
    entries = _run_redis(
        "mget quotes",
        lambda client: _mget_entries(client, stock_ids, now=now),
        kind="read",
    )
    if entries is not None:
        return entries
    if redis_configured():
        logger.debug("Redis quote read failed; falling back to memory cache")
    return _get_entries_from_memory(stock_ids, now=now)


def set_cached_quote(stock_id: str, quote: QuoteRecord) -> None:
    set_cached_quotes({stock_id: quote})


def set_cached_quotes(quotes: dict[str, QuoteRecord]) -> None:
    """Write one or more quotes (pipelined on Redis) with hard TTL retention."""
    if not quotes:
        return
    cached_at = datetime.now(UTC)
    hard_ttl = hard_ttl_seconds()
    serialized = {
        stock_id: json.dumps(_serialize(quote, cached_at=cached_at))
        for stock_id, quote in quotes.items()
    }

    def _pipeline_set(client: Any) -> bool:
        pipe = client.pipeline(transaction=False)
        for stock_id, payload in serialized.items():
            pipe.setex(_KEY_PREFIX + stock_id, hard_ttl, payload)
        pipe.execute()
        return True

    _run_redis("pipeline setex quotes", _pipeline_set, kind="write")

    with _memory_lock:
        _purge_expired_memory_locked()
        expires_at = time.monotonic() + hard_ttl
        for stock_id, payload in serialized.items():
            _memory_quotes[stock_id] = (expires_at, payload)
    for stock_id in quotes:
        clear_refresh_failure(stock_id)


def try_acquire_refresh_lock(stock_id: str) -> str | None:
    """Return an owner token when this caller should perform the refresh."""
    ttl = refresh_lock_seconds()
    token = uuid4().hex

    acquired = _run_redis(
        f"acquire refresh lock {stock_id}",
        lambda client: bool(client.set(_LOCK_PREFIX + stock_id, token, nx=True, ex=ttl)),
        kind="write",
    )
    if acquired is True:
        return token
    if acquired is False:
        return None

    now = time.monotonic()
    with _memory_lock:
        existing = _memory_refresh_locks.get(stock_id)
        if existing is not None and existing[0] > now:
            return None
        _memory_refresh_locks[stock_id] = (now + ttl, token)
        return token


def renew_refresh_lock(stock_id: str, token: str) -> bool:
    """Extend or reclaim the lease for ``token`` when the worker actually starts.

    Returns False when another owner holds the lease.
    """
    ttl = refresh_lock_seconds()

    def _renew(client: Any) -> bool:
        key = _LOCK_PREFIX + stock_id
        renewed = int(client.eval(_RENEW_IF_OWNER_SCRIPT, 1, key, token, str(ttl)))
        if renewed:
            return True
        # Lease expired while queued; try to reclaim with the same token.
        return bool(client.set(key, token, nx=True, ex=ttl))

    renewed = _run_redis(f"renew refresh lock {stock_id}", _renew, kind="write")
    if renewed is not None:
        return bool(renewed)

    now = time.monotonic()
    with _memory_lock:
        existing = _memory_refresh_locks.get(stock_id)
        if existing is None or existing[0] <= now:
            _memory_refresh_locks[stock_id] = (now + ttl, token)
            return True
        if existing[1] != token:
            return False
        _memory_refresh_locks[stock_id] = (now + ttl, token)
        return True


def release_refresh_lock(stock_id: str, token: str | None = None) -> None:
    """Release the refresh lease only when ``token`` still owns it."""
    if token is not None:
        _run_redis(
            f"release refresh lock {stock_id}",
            lambda client: client.eval(
                _RELEASE_IF_OWNER_SCRIPT, 1, _LOCK_PREFIX + stock_id, token
            ),
            kind="write",
        )
    else:
        _run_redis(
            f"release refresh lock {stock_id}",
            lambda client: client.delete(_LOCK_PREFIX + stock_id),
            kind="write",
        )
    with _memory_lock:
        if token is None:
            _memory_refresh_locks.pop(stock_id, None)
            return
        existing = _memory_refresh_locks.get(stock_id)
        if existing is not None and existing[1] == token:
            _memory_refresh_locks.pop(stock_id, None)


def record_refresh_failure(
    stock_id: str,
    *,
    backoff_seconds: float | None = None,
) -> None:
    """Backoff re-queueing after a failed background refresh.

    ``backoff_seconds`` may extend past the default lock TTL so Retry-After /
    soft circuit windows are honored before the next cache-miss re-queue.
    """
    seconds = int(max(float(backoff_seconds or refresh_failure_backoff_seconds()), 1))
    until = time.monotonic() + seconds
    with _memory_lock:
        _memory_refresh_failures[stock_id] = until
    _run_redis(
        f"refresh failure marker {stock_id}",
        lambda client: client.setex(
            f"{_LOCK_PREFIX}fail:{stock_id}",
            seconds,
            "1",
        ),
        kind="write",
    )


def clear_refresh_failure(stock_id: str) -> None:
    with _memory_lock:
        _memory_refresh_failures.pop(stock_id, None)
    _run_redis(
        f"clear refresh failure {stock_id}",
        lambda client: client.delete(f"{_LOCK_PREFIX}fail:{stock_id}"),
        kind="write",
    )


def refresh_failure_active(stock_id: str) -> bool:
    now = time.monotonic()
    with _memory_lock:
        until = _memory_refresh_failures.get(stock_id)
        if until is not None:
            if until > now:
                return True
            _memory_refresh_failures.pop(stock_id, None)
    exists = _run_redis(
        f"refresh failure check {stock_id}",
        lambda client: bool(client.exists(f"{_LOCK_PREFIX}fail:{stock_id}")),
        kind="read",
    )
    return bool(exists)


def clear_quote_cache_for_tests() -> None:
    """Test helper: drop in-process cache/locks and forget Redis client state."""
    global _client, _client_retry_after_monotonic, _consecutive_failures, _connecting
    global _last_success_at, _last_success_ping_at, _last_success_read_at
    global _last_success_write_at, _last_error_at, _last_error_category
    with _memory_lock:
        _memory_quotes.clear()
        _memory_refresh_locks.clear()
        _memory_refresh_failures.clear()
    with _client_lock:
        _client = None
        _connecting = False
        _client_retry_after_monotonic = 0.0
        _consecutive_failures = 0
        _last_success_at = None
        _last_success_ping_at = None
        _last_success_read_at = None
        _last_success_write_at = None
        _last_error_at = None
        _last_error_category = None
        _connect_cond.notify_all()


def _purge_expired_memory_locked() -> None:
    now_mono = time.monotonic()
    expired = [
        stock_id
        for stock_id, (expires_at, _raw) in _memory_quotes.items()
        if expires_at <= now_mono
    ]
    for stock_id in expired:
        _memory_quotes.pop(stock_id, None)


def _preload_market_calendars(stock_ids: list[str], now: datetime) -> None:
    """Fetch exchange calendars before quote deserialization takes ``_memory_lock``.

    Taiwan market status otherwise requests the TWSE holiday page, and an
    8-second timeout under the cache lock would stall every quote read, write,
    and refresh lock.
    """
    markets = {
        stock_id.split(":", 1)[0] for stock_id in stock_ids if ":" in stock_id
    }
    for market in markets:
        if market in MARKET_TIMEZONES:
            is_market_open(market, now)


def _mget_entries(
    client: Any,
    stock_ids: list[str],
    *,
    now: datetime,
) -> dict[str, CachedQuoteEntry]:
    _preload_market_calendars(stock_ids, now)
    keys = [_KEY_PREFIX + stock_id for stock_id in stock_ids]
    values = client.mget(keys)
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
    _preload_market_calendars(stock_ids, now)
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
