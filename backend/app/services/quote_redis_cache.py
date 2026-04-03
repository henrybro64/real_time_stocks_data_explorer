"""Optional Redis cache for quote payloads (speeds portfolio / watchlist refresh)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings
from app.models.quote import QuoteRecord

logger = logging.getLogger(__name__)

_KEY_PREFIX = "quote:v1:"
_client: Any = None  # redis.Redis | Literal[False] after first init attempt


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


def _serialize(quote: QuoteRecord) -> dict[str, Any]:
    return {
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
    }


def _deserialize(data: dict[str, Any]) -> QuoteRecord:
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


def get_cached_quote(stock_id: str) -> QuoteRecord | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(_KEY_PREFIX + stock_id)
        if not raw:
            return None
        return _deserialize(json.loads(raw))
    except Exception:
        return None


def set_cached_quote(stock_id: str, quote: QuoteRecord) -> None:
    r = _redis()
    if r is None:
        return
    ttl = settings.quote_cache_ttl_seconds
    try:
        r.setex(
            _KEY_PREFIX + stock_id,
            ttl,
            json.dumps(_serialize(quote)),
        )
    except Exception as exc:
        logger.debug("Redis set quote %s: %s", stock_id, exc)
