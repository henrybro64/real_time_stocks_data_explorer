"""Redis-backed cache for the periodic rankings quote snapshot.

The rankings page can rank a wider universe than the per-request live-quote path
can fetch synchronously. A CronJob calls the refresh endpoint every ~30 min,
which writes the full snapshot here as a single JSON blob. The rankings
endpoint reads it back in O(1) and sorts in memory.

Falls back gracefully when Redis is not configured (``REDIS_URL`` empty) or
unreachable: writes become no-ops and reads return ``None``, which lets the
rankings service fall through to its curated-universe path.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord

logger = logging.getLogger(__name__)

_KEY_PREFIX = "rankings:snapshot:v1:"
_client: Any = None  # redis.Redis | Literal[False] after first init attempt


@dataclass(frozen=True)
class SnapshotEntry:
    stock: StockRecord
    quote: QuoteRecord


@dataclass(frozen=True)
class RankingsSnapshot:
    market: str
    fetched_at: datetime
    market_open: bool
    entries: list[SnapshotEntry]

    def age_minutes(self, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - self.fetched_at).total_seconds() / 60.0)


def _redis() -> Any:
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

        client = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        client.ping()
        _client = client
        logger.info("Rankings snapshot Redis cache enabled")
        return _client
    except Exception as exc:
        logger.warning("Rankings snapshot Redis cache disabled: %s", exc)
        _client = False
        return None


def _stock_to_dict(stock: StockRecord) -> dict[str, Any]:
    return {
        "id": stock.id,
        "market": stock.market,
        "symbol": stock.symbol,
        "name": stock.name,
        "exchange": stock.exchange,
        "currency": stock.currency,
        "timezone": stock.timezone,
        "name_en": stock.name_en,
        "name_zh_tw": stock.name_zh_tw,
    }


def _stock_from_dict(data: dict[str, Any]) -> StockRecord:
    return StockRecord(
        id=str(data["id"]),
        market=str(data["market"]),
        symbol=str(data["symbol"]),
        name=str(data["name"]),
        exchange=str(data["exchange"]),
        currency=str(data["currency"]),
        timezone=str(data["timezone"]),
        name_en=data.get("name_en"),
        name_zh_tw=data.get("name_zh_tw"),
    )


def _quote_to_dict(quote: QuoteRecord) -> dict[str, Any]:
    return {
        "id": quote.id,
        "last_price": quote.last_price,
        "change": quote.change,
        "change_percent": quote.change_percent,
        "previous_close": quote.previous_close,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "volume": quote.volume,
        "timestamp": quote.timestamp,
        "is_market_open": quote.is_market_open,
    }


def _quote_from_dict(data: dict[str, Any]) -> QuoteRecord:
    def _optional_float(key: str) -> float | None:
        value = data.get(key)
        return float(value) if value is not None else None

    volume = data.get("volume")
    return QuoteRecord(
        id=str(data["id"]),
        last_price=float(data["last_price"]),
        change=float(data["change"]),
        change_percent=float(data["change_percent"]),
        previous_close=float(data["previous_close"]),
        open=_optional_float("open"),
        high=_optional_float("high"),
        low=_optional_float("low"),
        volume=int(volume) if volume is not None else None,
        timestamp=str(data["timestamp"]),
        is_market_open=bool(data["is_market_open"]),
    )


def _serialize(snapshot: RankingsSnapshot) -> str:
    return json.dumps(
        {
            "market": snapshot.market,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "market_open": snapshot.market_open,
            "entries": [
                {
                    "stock": _stock_to_dict(entry.stock),
                    "quote": _quote_to_dict(entry.quote),
                }
                for entry in snapshot.entries
            ],
        },
        ensure_ascii=False,
    )


def _deserialize(raw: str) -> RankingsSnapshot | None:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    try:
        fetched_at = datetime.fromisoformat(str(data["fetched_at"]))
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        entries: list[SnapshotEntry] = []
        for raw_entry in data.get("entries", []):
            entries.append(
                SnapshotEntry(
                    stock=_stock_from_dict(raw_entry["stock"]),
                    quote=_quote_from_dict(raw_entry["quote"]),
                )
            )
        return RankingsSnapshot(
            market=str(data["market"]),
            fetched_at=fetched_at,
            market_open=bool(data.get("market_open", False)),
            entries=entries,
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Rankings snapshot deserialize failed: %s", exc)
        return None


def get_snapshot(market: str) -> RankingsSnapshot | None:
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(_KEY_PREFIX + market)
    except Exception as exc:
        logger.debug("Rankings snapshot Redis get %s: %s", market, exc)
        return None
    if not raw:
        return None
    return _deserialize(raw)


def set_snapshot(snapshot: RankingsSnapshot) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.set(_KEY_PREFIX + snapshot.market, _serialize(snapshot))
    except Exception as exc:
        logger.debug("Rankings snapshot Redis set %s: %s", snapshot.market, exc)


def reset_client_for_tests() -> None:
    """Test hook so unit tests can re-evaluate the Redis URL setting."""
    global _client
    _client = None
