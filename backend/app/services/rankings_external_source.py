"""Pre-ranked rankings data from Yahoo Finance.

Yahoo already publishes top-N volume / movers; we scrape or screen instead of
ranking every symbol in Python. Providers:

* Taiwan: HTML at ``tw.stock.yahoo.com/rank/{volume,change-up,change-down}``.
  Tables mix ``.TW`` (TWSE) and ``.TWO`` (TPEx); we parse rows with ``exchange``
  set, then ``_filter_tw_board`` keeps the requested board.
  SSR HTML is stripped to text (same approach as ``tw_insights_service``).
* U.S.: ``yfinance.screen("most_actives" | "day_gainers" | "day_losers")`` with
  Yahoo's crumb handling; JSON rows include quote fields.

Responses are cached in Redis (or in-memory) for a bounded TTL (see settings).
On failure, ``fetch`` returns ``None`` and the caller uses quote-snapshot
fallback (batched or per-symbol quotes).
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.core.diagnostics import elapsed_ms
from app.core.config import settings
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.rankings import RankingType
from app.services.http_client import HttpFetchError, fetch_text
from app.services.market_clock import is_market_open

logger = logging.getLogger(__name__)


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_REQUEST_TIMEOUT_SECONDS = 8.0
_REQUEST_RETRIES = 1
_CACHE_TTL_SECONDS = 600  # Default 10 min; production uses ``settings.rankings_external_cache_ttl_seconds``.
_CACHE_KEY_PREFIX = "rankings:external:v1:"
_US_FETCH_BATCH = 50  # Yahoo U.S. screeners are already top-N and the UI tops out at 50.


# Yahoo TW rank pages we know how to parse
_TW_RANK_PATH = {
    RankingType.volume: "volume",
    RankingType.change_up: "change-up",
    RankingType.change_down: "change-down",
}
_TW_BASE_URL = "https://tw.stock.yahoo.com/rank/{slug}"
_TW_TZ = ZoneInfo("Asia/Taipei")
_TW_SYMBOL_PATTERN = re.compile(r"^(\d{4,5})\.(TW|TWO)$")
_TW_PERCENT_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?%$")
_TW_DECIMAL_PATTERN = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")


# Yahoo U.S. predefined screeners
_US_SCREENER_ID = {
    RankingType.volume: "most_actives",
    RankingType.change_up: "day_gainers",
    RankingType.change_down: "day_losers",
}
_US_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class _CachedEnvelopes:
    fetched_at: float  # epoch seconds
    envelopes: list[tuple[StockRecord, QuoteRecord]]
    requested_limit: int


class RankingsExternalSource:
    """Fetches pre-sorted (stock, quote) pairs for one ranking type."""

    def __init__(
        self,
        *,
        tw_html_fetcher: Callable[[str], str] | None = None,
        us_screener_fetcher: Callable[[str, int], dict[str, Any]] | None = None,
        cache_ttl_seconds: int = _CACHE_TTL_SECONDS,
    ) -> None:
        self._tw_html_fetcher = tw_html_fetcher or _default_tw_html_fetcher
        self._us_screener_fetcher = us_screener_fetcher or _default_us_screener_fetcher
        self._cache_ttl_seconds = max(1, cache_ttl_seconds)
        self._memory_cache: dict[str, _CachedEnvelopes] = {}

    def fetch(
        self,
        market: str,
        ranking_type: RankingType,
        limit: int,
        now: datetime | None = None,
        *,
        tw_board: str | None = None,
    ) -> list[tuple[StockRecord, QuoteRecord]] | None:
        started_at = perf_counter()
        if ranking_type not in _TW_RANK_PATH and ranking_type not in _US_SCREENER_ID:
            return None
        if market not in {"US", "TW"}:
            return None

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cache_key = self._cache_key(market, ranking_type, tw_board)
        shared_cache_key = (
            self._cache_key(market, ranking_type, None)
            if market == "TW" and tw_board in {"TWSE", "TPEx"}
            else None
        )

        cached_entry = self._read_cache_entry(cache_key, current)
        if cached_entry is None and shared_cache_key is not None:
            cached_entry = self._read_cache_entry(shared_cache_key, current)
        if cached_entry is not None:
            board_filtered = self._filter_tw_board(cached_entry.envelopes, market, tw_board)
            filtered = board_filtered[:limit]
            if self._should_use_cached_envelopes(
                cached_entry,
                filtered,
                limit,
                board_filtered_count=len(board_filtered),
            ):
                logger.debug(
                    "External rankings cache hit",
                    extra={
                        "event": "rankings.external.cache.hit",
                        "market": market,
                        "ranking_type": ranking_type.value,
                        "tw_board": tw_board,
                        "item_count": len(filtered),
                    },
                )
                return filtered

        envelopes = self._fetch_uncached(market, ranking_type, current, limit)
        if envelopes is None:
            logger.warning(
                "External rankings source was unavailable",
                extra={
                    "event": "rankings.external.fetch.unavailable",
                    "market": market,
                    "ranking_type": ranking_type.value,
                    "tw_board": tw_board,
                    "duration_ms": elapsed_ms(started_at),
                },
            )
            return None

        if market == "TW":
            self._write_cache(
                self._cache_key(market, ranking_type, None),
                envelopes,
                current,
                requested_limit=limit,
            )
            if tw_board in {"TWSE", "TPEx"}:
                self._write_cache(
                    cache_key,
                    self._filter_tw_board(envelopes, market, tw_board),
                    current,
                    requested_limit=limit,
                )
        else:
            self._write_cache(cache_key, envelopes, current, requested_limit=limit)
        filtered = self._filter_tw_board(envelopes, market, tw_board)[:limit]
        logger.info(
            "External rankings fetch completed",
            extra={
                "event": "rankings.external.fetch.completed",
                "market": market,
                "ranking_type": ranking_type.value,
                "tw_board": tw_board,
                "duration_ms": elapsed_ms(started_at),
                "item_count": len(filtered),
            },
        )
        return filtered

    @staticmethod
    def _cache_key(
        market: str,
        ranking_type: RankingType,
        tw_board: str | None,
    ) -> str:
        if market != "TW":
            return f"{_CACHE_KEY_PREFIX}{market}:{ranking_type.value}"
        board_key = tw_board if tw_board in {"TWSE", "TPEx"} else "all"
        return f"{_CACHE_KEY_PREFIX}{market}:{ranking_type.value}:{board_key}"

    @staticmethod
    def _should_use_cached_envelopes(
        cached: _CachedEnvelopes,
        filtered: list[tuple[StockRecord, QuoteRecord]],
        limit: int,
        *,
        board_filtered_count: int | None = None,
    ) -> bool:
        if len(filtered) >= limit:
            return True
        if len(cached.envelopes) < cached.requested_limit:
            return True
        if cached.requested_limit < limit:
            return False
        if board_filtered_count is not None and board_filtered_count < limit:
            return True
        return False

    @staticmethod
    def _filter_tw_board(
        envelopes: list[tuple[StockRecord, QuoteRecord]],
        market: str,
        tw_board: str | None,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        if market != "TW" or tw_board not in {"TWSE", "TPEx"}:
            return envelopes
        return [pair for pair in envelopes if pair[0].exchange == tw_board]

    def _fetch_uncached(
        self,
        market: str,
        ranking_type: RankingType,
        now: datetime,
        limit: int,
    ) -> list[tuple[StockRecord, QuoteRecord]] | None:
        if market == "TW":
            slug = _TW_RANK_PATH.get(ranking_type)
            if slug is None:
                return None
            try:
                html_text = self._tw_html_fetcher(slug)
            except (HttpFetchError, TimeoutError) as exc:
                logger.warning(
                    "Yahoo TW rank page fetch failed",
                    extra={
                        "event": "rankings.external.tw.failed",
                        "market": "TW",
                        "ranking_type": ranking_type.value,
                        "provider": "yahoo_finance",
                        "slug": slug,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                return None
            envelopes = _parse_tw_rank_page(html_text, ranking_type, now)
            if not envelopes:
                logger.warning(
                    "Yahoo TW rank page returned no parseable rows",
                    extra={
                        "event": "rankings.external.tw.empty",
                        "market": "TW",
                        "ranking_type": ranking_type.value,
                        "provider": "yahoo_finance",
                        "slug": slug,
                    },
                )
                return None
            return envelopes

        # market == "US"
        scr_id = _US_SCREENER_ID.get(ranking_type)
        if scr_id is None:
            return None
        fetch_count = max(limit, _US_FETCH_BATCH)
        try:
            response = self._us_screener_fetcher(scr_id, fetch_count)
        except Exception as exc:  # noqa: BLE001 - yfinance can raise many shapes
            logger.warning(
                "Yahoo U.S. screener failed",
                extra={
                    "event": "rankings.external.us.failed",
                    "market": "US",
                    "ranking_type": ranking_type.value,
                    "provider": "yahoo_finance",
                    "screener_id": scr_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None
        envelopes = _parse_us_screener_response(response, now)
        if not envelopes:
            logger.warning(
                "Yahoo U.S. screener returned no parseable quotes",
                extra={
                    "event": "rankings.external.us.empty",
                    "market": "US",
                    "ranking_type": ranking_type.value,
                    "provider": "yahoo_finance",
                    "screener_id": scr_id,
                },
            )
            return None
        return envelopes[:fetch_count]

    def _read_cache_entry(
        self,
        key: str,
        now: datetime,
    ) -> _CachedEnvelopes | None:
        now_epoch = now.timestamp()
        entry = self._memory_cache.get(key)
        if entry is not None and now_epoch - entry.fetched_at < self._cache_ttl_seconds:
            return entry
        raw = _redis_get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            fetched_at = float(payload["fetched_at"])
            if now_epoch - fetched_at >= self._cache_ttl_seconds:
                return None
            envelopes = _deserialize_envelopes(payload["envelopes"])
            requested_limit = int(payload.get("requested_limit", len(envelopes)))
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("Rankings external cache deserialize failed for %s: %s", key, exc)
            return None
        entry = _CachedEnvelopes(
            fetched_at=fetched_at,
            envelopes=envelopes,
            requested_limit=requested_limit,
        )
        self._memory_cache[key] = entry
        return entry

    def _read_cache(
        self,
        key: str,
        now: datetime,
    ) -> list[tuple[StockRecord, QuoteRecord]] | None:
        entry = self._read_cache_entry(key, now)
        return entry.envelopes if entry is not None else None

    def _write_cache(
        self,
        key: str,
        envelopes: list[tuple[StockRecord, QuoteRecord]],
        now: datetime,
        *,
        requested_limit: int,
    ) -> None:
        fetched_at = now.timestamp()
        self._memory_cache[key] = _CachedEnvelopes(
            fetched_at=fetched_at,
            envelopes=envelopes,
            requested_limit=requested_limit,
        )
        payload = {
            "fetched_at": fetched_at,
            "requested_limit": requested_limit,
            "envelopes": _serialize_envelopes(envelopes),
        }
        _redis_setex(key, self._cache_ttl_seconds, json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Default fetchers (real network); replaced by tests with deterministic fakes
# ---------------------------------------------------------------------------


def _default_tw_html_fetcher(slug: str) -> str:
    url = _TW_BASE_URL.format(slug=slug)
    return fetch_text(
        url,
        headers={
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=_REQUEST_RETRIES,
        provider_name="yahoo_finance",
        operation_name="rankings_tw_html",
        resource_id=slug,
    )


def _default_us_screener_fetcher(scr_id: str, count: int) -> dict[str, Any]:
    """Wrap ``yfinance.screen``. Imported lazily so unit tests don't need yfinance."""
    import yfinance as yf  # pylint: disable=import-outside-toplevel

    return yf.screen(scr_id, count=count)


# ---------------------------------------------------------------------------
# TW rank-page parser
# ---------------------------------------------------------------------------


def _strip_html_to_lines(raw_html: str) -> list[str]:
    no_scripts = re.sub(
        r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>",
        " ",
        raw_html,
        flags=re.IGNORECASE,
    )
    no_styles = re.sub(
        r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>",
        " ",
        no_scripts,
        flags=re.IGNORECASE,
    )
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", no_styles))
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_tw_volume_lots(token: str) -> int | None:
    cleaned = token.replace(",", "").strip()
    if not cleaned:
        return None
    multiplier = 1
    if cleaned.endswith("B"):
        multiplier = 1_000_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("K"):
        multiplier = 1_000
        cleaned = cleaned[:-1]
    try:
        return int(float(cleaned) * multiplier)
    except ValueError:
        return None


def _parse_tw_decimal(token: str) -> float | None:
    if not _TW_DECIMAL_PATTERN.match(token):
        return None
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _parse_tw_rank_page(
    html_text: str,
    ranking_type: RankingType,
    now: datetime,
) -> list[tuple[StockRecord, QuoteRecord]]:
    lines = _strip_html_to_lines(html_text)
    sign = -1.0 if ranking_type == RankingType.change_down else 1.0
    market_open = is_market_open("TW", now)
    quote_timestamp = now.astimezone(_TW_TZ).isoformat()

    envelopes: list[tuple[StockRecord, QuoteRecord]] = []
    seen_ids: set[str] = set()

    for idx, line in enumerate(lines):
        match = _TW_SYMBOL_PATTERN.match(line)
        if not match:
            continue
        if idx == 0 or idx + 7 >= len(lines):
            continue

        symbol = match.group(1)
        exchange = "TPEx" if match.group(2) == "TWO" else "TWSE"
        stock_id = f"TW:{symbol}"
        if stock_id in seen_ids:
            continue

        name = lines[idx - 1]
        if (
            not name
            or _TW_DECIMAL_PATTERN.match(name)
            or _TW_PERCENT_PATTERN.match(name)
            or len(name) > 30
        ):
            continue

        last_price = _parse_tw_decimal(lines[idx + 1])
        change_abs_raw = _parse_tw_decimal(lines[idx + 2])
        change_pct_token = lines[idx + 3]
        high = _parse_tw_decimal(lines[idx + 4])
        low = _parse_tw_decimal(lines[idx + 5])
        volume_lots = _parse_tw_volume_lots(lines[idx + 7])

        if (
            last_price is None
            or change_abs_raw is None
            or not _TW_PERCENT_PATTERN.match(change_pct_token)
        ):
            continue
        change_pct_value = _parse_tw_decimal(change_pct_token[:-1])
        if change_pct_value is None:
            continue

        change_abs = sign * change_abs_raw
        change_pct = sign * change_pct_value
        previous_close = round(last_price - change_abs, 2)
        if previous_close <= 0:
            continue

        volume_shares = volume_lots * 1000 if volume_lots is not None else None

        stock = StockRecord(
            id=stock_id,
            market="TW",
            symbol=symbol,
            name=name,
            exchange=exchange,
            currency="TWD",
            timezone="Asia/Taipei",
            name_en=None,
            name_zh_tw=name,
        )
        quote = QuoteRecord(
            id=stock_id,
            last_price=round(last_price, 2),
            change=round(change_abs, 2),
            change_percent=round(change_pct, 2),
            previous_close=previous_close,
            timestamp=quote_timestamp,
            is_market_open=market_open,
            open=None,
            high=round(high, 2) if high is not None else None,
            low=round(low, 2) if low is not None else None,
            volume=volume_shares,
        )
        envelopes.append((stock, quote))
        seen_ids.add(stock_id)

    return envelopes


# ---------------------------------------------------------------------------
# U.S. screener parser
# ---------------------------------------------------------------------------


def _us_exchange(raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        return "NYSE"
    upper = raw.upper()
    if "NASDAQ" in upper or upper.startswith("NMS") or upper.startswith("NGM"):
        return "NASDAQ"
    if "NYSE" in upper or upper.startswith("NYS"):
        return "NYSE"
    if "AMEX" in upper or "ARCA" in upper or upper.startswith("ASE"):
        return "AMEX"
    return raw


def _parse_us_screener_response(
    response: Any,
    now: datetime,
) -> list[tuple[StockRecord, QuoteRecord]]:
    if not isinstance(response, dict):
        return []
    raw_quotes = response.get("quotes") or []
    if not isinstance(raw_quotes, list):
        return []
    market_open = is_market_open("US", now)

    envelopes: list[tuple[StockRecord, QuoteRecord]] = []
    seen_ids: set[str] = set()

    for raw_quote in raw_quotes:
        if not isinstance(raw_quote, dict):
            continue
        symbol = raw_quote.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        # Skip non-equity rows (ETF, mutual fund, etc.) — Yahoo includes
        # mixed types in the predefined screeners for some queries.
        quote_type = raw_quote.get("quoteType")
        if isinstance(quote_type, str) and quote_type.upper() not in {"EQUITY", ""}:
            continue
        stock_id = f"US:{symbol}"
        if stock_id in seen_ids:
            continue

        last_price = _coerce_float(raw_quote.get("regularMarketPrice"))
        previous_close = _coerce_float(raw_quote.get("regularMarketPreviousClose"))
        if last_price is None or previous_close is None or previous_close <= 0:
            continue
        change_abs = _coerce_float(raw_quote.get("regularMarketChange"))
        change_pct = _coerce_float(raw_quote.get("regularMarketChangePercent"))
        if change_abs is None:
            change_abs = last_price - previous_close
        if change_pct is None:
            change_pct = (change_abs / previous_close) * 100.0

        market_time_raw = raw_quote.get("regularMarketTime")
        if isinstance(market_time_raw, (int, float)) and market_time_raw > 0:
            quote_time = datetime.fromtimestamp(market_time_raw, tz=_US_TZ)
        else:
            quote_time = now.astimezone(_US_TZ)

        volume = raw_quote.get("regularMarketVolume")
        name = (
            raw_quote.get("shortName")
            or raw_quote.get("longName")
            or raw_quote.get("displayName")
            or symbol
        )

        stock = StockRecord(
            id=stock_id,
            market="US",
            symbol=symbol,
            name=str(name),
            exchange=_us_exchange(raw_quote.get("fullExchangeName") or raw_quote.get("exchange")),
            currency=str(raw_quote.get("currency") or "USD"),
            timezone="America/New_York",
            name_en=str(name),
            name_zh_tw=None,
        )
        quote = QuoteRecord(
            id=stock_id,
            last_price=round(float(last_price), 2),
            change=round(float(change_abs), 2),
            change_percent=round(float(change_pct), 2),
            previous_close=round(float(previous_close), 2),
            timestamp=quote_time.isoformat(),
            is_market_open=market_open,
            open=_coerce_float(raw_quote.get("regularMarketOpen")),
            high=_coerce_float(raw_quote.get("regularMarketDayHigh")),
            low=_coerce_float(raw_quote.get("regularMarketDayLow")),
            volume=int(volume) if isinstance(volume, (int, float)) else None,
        )
        envelopes.append((stock, quote))
        seen_ids.add(stock_id)

    return envelopes


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Redis cache plumbing (shared client with quote_redis_cache)
# ---------------------------------------------------------------------------


def _redis_get(key: str) -> str | None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    return run_redis(
        f"rankings external get {key}",
        lambda client: client.get(key),
        kind="read",
    )


def _redis_setex(key: str, ttl_seconds: int, value: str) -> None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    run_redis(
        f"rankings external setex {key}",
        lambda client: client.setex(key, ttl_seconds, value),
        kind="write",
    )


def _serialize_envelopes(
    envelopes: list[tuple[StockRecord, QuoteRecord]],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for stock, quote in envelopes:
        serialized.append(
            {
                "stock": {
                    "id": stock.id,
                    "market": stock.market,
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "exchange": stock.exchange,
                    "currency": stock.currency,
                    "timezone": stock.timezone,
                    "name_en": stock.name_en,
                    "name_zh_tw": stock.name_zh_tw,
                },
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
        )
    return serialized


def _deserialize_envelopes(
    raw: list[dict[str, Any]],
) -> list[tuple[StockRecord, QuoteRecord]]:
    envelopes: list[tuple[StockRecord, QuoteRecord]] = []
    for entry in raw:
        stock_data = entry["stock"]
        quote_data = entry["quote"]
        stock = StockRecord(
            id=str(stock_data["id"]),
            market=str(stock_data["market"]),
            symbol=str(stock_data["symbol"]),
            name=str(stock_data["name"]),
            exchange=str(stock_data["exchange"]),
            currency=str(stock_data["currency"]),
            timezone=str(stock_data["timezone"]),
            name_en=stock_data.get("name_en"),
            name_zh_tw=stock_data.get("name_zh_tw"),
        )
        quote = QuoteRecord(
            id=str(quote_data["id"]),
            last_price=float(quote_data["last_price"]),
            change=float(quote_data["change"]),
            change_percent=float(quote_data["change_percent"]),
            previous_close=float(quote_data["previous_close"]),
            timestamp=str(quote_data["timestamp"]),
            is_market_open=bool(quote_data["is_market_open"]),
            open=_coerce_float(quote_data.get("open")),
            high=_coerce_float(quote_data.get("high")),
            low=_coerce_float(quote_data.get("low")),
            volume=int(quote_data["volume"]) if quote_data.get("volume") is not None else None,
        )
        envelopes.append((stock, quote))
    return envelopes


rankings_external_source = RankingsExternalSource(
    cache_ttl_seconds=settings.rankings_external_cache_ttl_seconds,
)


__all__ = [
    "RankingsExternalSource",
    "rankings_external_source",
]
