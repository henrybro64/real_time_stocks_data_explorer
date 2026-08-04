"""WTI crude oil (front-month futures) quote for the Market Snapshot UI.

Uses Yahoo Finance symbol ``CL=F``. This is an informational front-month
futures quote and may roll between contracts; it is not a physical spot feed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import logging
import math
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.services.http_client import HttpFetchError, fetch_json
from app.services.market_snapshot_cache import (
    clear_market_snapshot_cache_for_tests,
    get_cached_snapshot,
    make_cache_payload,
    payload_age_seconds,
    run_snapshot_refresh_singleflight,
    set_cached_snapshot,
)

logger = logging.getLogger(__name__)

_WTI_SYMBOL = "CL=F"
_WTI_NAME = "WTI Crude Oil"
_SOURCE = "Yahoo Finance"
_YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    f"{quote(_WTI_SYMBOL, safe='')}?interval=1d&range=5d"
)
_REQUEST_TIMEOUT_SECONDS = 8
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_SOFT_TTL_SECONDS = 60.0
_HARD_TTL_SECONDS = 900.0
_TZ_NEW_YORK = ZoneInfo("America/New_York")

QuoteStatus = Literal["ok", "stale"]

_CACHE_KEY = "commodity:wti_crude_oil"


@dataclass(frozen=True)
class CommodityQuoteRecord:
    symbol: str
    name: str
    price: float
    currency: str
    unit: str
    change: float | None
    change_percent: float | None
    timestamp: str
    source: str
    is_market_open: bool | None
    status: QuoteStatus = "ok"


class CommodityUnavailableError(Exception):
    """Raised when no usable WTI quote can be returned."""


def clear_wti_cache_for_tests() -> None:
    clear_market_snapshot_cache_for_tests()


def get_wti_crude_oil_quote() -> CommodityQuoteRecord:
    """Return a fresh, soft-cached, or hard-retained stale WTI quote."""
    cached_payload = get_cached_snapshot(_CACHE_KEY)
    cached_quote = _extract_quote(cached_payload)
    cached_age = payload_age_seconds(cached_payload) if cached_payload is not None else None
    if cached_quote is not None and cached_age is not None and cached_age < _SOFT_TTL_SECONDS:
        return replace(cached_quote, status="ok")

    return run_snapshot_refresh_singleflight(_CACHE_KEY, _refresh_wti_crude_oil_quote)


def _refresh_wti_crude_oil_quote() -> CommodityQuoteRecord:
    # Re-check after winning singleflight; another caller may have filled the cache.
    cached_payload = get_cached_snapshot(_CACHE_KEY)
    cached_quote = _extract_quote(cached_payload)
    cached_age = payload_age_seconds(cached_payload) if cached_payload is not None else None
    if cached_quote is not None and cached_age is not None and cached_age < _SOFT_TTL_SECONDS:
        return replace(cached_quote, status="ok")

    quote = _fetch_wti_crude_oil_quote()
    if quote is not None:
        set_cached_snapshot(
            _CACHE_KEY,
            make_cache_payload(_quote_to_snapshot(quote)),
            ttl_seconds=int(_HARD_TTL_SECONDS),
        )
        logger.info(
            "WTI crude oil quote refreshed",
            extra={
                "event": "commodity.wti.refreshed",
                "provider": "yahoo_finance",
                "symbol": _WTI_SYMBOL,
                "price": quote.price,
            },
        )
        return quote

    if cached_quote is not None and cached_age is not None and cached_age < _HARD_TTL_SECONDS:
        logger.warning(
            "WTI crude oil provider failed; serving retained quote as stale",
            extra={
                "event": "commodity.wti.stale_served",
                "provider": "yahoo_finance",
                "symbol": _WTI_SYMBOL,
                "cache_age_seconds": round(cached_age, 1),
            },
        )
        return replace(cached_quote, status="stale")

    logger.warning(
        "WTI crude oil quote unavailable",
        extra={
            "event": "commodity.wti.unavailable",
            "provider": "yahoo_finance",
            "symbol": _WTI_SYMBOL,
        },
    )
    raise CommodityUnavailableError("WTI crude oil quote unavailable")


def _fetch_wti_crude_oil_quote() -> CommodityQuoteRecord | None:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    try:
        payload = fetch_json(
            _YAHOO_CHART_URL,
            headers=headers,
            timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
            retries=2,
            provider_name="yahoo_finance",
            operation_name="commodity_wti",
            resource_id=_WTI_SYMBOL,
        )
    except HttpFetchError:
        return None
    return _normalize_yahoo_wti_payload(payload)


def _normalize_yahoo_wti_payload(payload: dict[str, Any]) -> CommodityQuoteRecord | None:
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error"):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return None
    block = results[0]
    if not isinstance(block, dict):
        return None
    meta = block.get("meta")
    if not isinstance(meta, dict):
        return None

    # Futures can trade at or below zero; reject only non-finite values.
    price = _coerce_finite(meta.get("regularMarketPrice"))
    if price is None:
        return None

    previous_close = _coerce_finite(
        meta.get("chartPreviousClose")
        if meta.get("chartPreviousClose") is not None
        else meta.get("previousClose")
    )
    change: float | None = None
    change_percent: float | None = None
    if previous_close is not None:
        change = round(price - previous_close, 4)
        if previous_close != 0:
            change_percent = round((change / previous_close) * 100, 4)

    market_time = meta.get("regularMarketTime")
    if isinstance(market_time, (int, float)):
        timestamp = datetime.fromtimestamp(market_time, tz=_TZ_NEW_YORK).isoformat()
    else:
        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    market_state = meta.get("marketState")
    is_market_open: bool | None = None
    if isinstance(market_state, str):
        is_market_open = market_state.upper() in {"REGULAR", "OPEN"}

    return CommodityQuoteRecord(
        symbol=_WTI_SYMBOL,
        name=_WTI_NAME,
        price=round(price, 4),
        currency="USD",
        unit="barrel",
        change=change,
        change_percent=change_percent,
        timestamp=timestamp,
        source=_SOURCE,
        is_market_open=is_market_open,
        status="ok",
    )


def _coerce_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def commodity_quote_to_api(quote: CommodityQuoteRecord) -> dict[str, Any]:
    return {
        "symbol": quote.symbol,
        "name": quote.name,
        "price": quote.price,
        "currency": quote.currency,
        "unit": quote.unit,
        "change": quote.change,
        "changePercent": quote.change_percent,
        "timestamp": quote.timestamp,
        "source": quote.source,
        "isMarketOpen": quote.is_market_open,
        "status": quote.status,
    }


def _quote_to_snapshot(quote: CommodityQuoteRecord) -> dict[str, Any]:
    return {
        "symbol": quote.symbol,
        "name": quote.name,
        "price": quote.price,
        "currency": quote.currency,
        "unit": quote.unit,
        "change": quote.change,
        "changePercent": quote.change_percent,
        "timestamp": quote.timestamp,
        "source": quote.source,
        "isMarketOpen": quote.is_market_open,
        "status": quote.status,
    }


def _extract_quote(payload: dict[str, Any] | None) -> CommodityQuoteRecord | None:
    if not isinstance(payload, dict):
        return None
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    symbol = snapshot.get("symbol")
    name = snapshot.get("name")
    price = snapshot.get("price")
    currency = snapshot.get("currency")
    unit = snapshot.get("unit")
    timestamp = snapshot.get("timestamp")
    source = snapshot.get("source")
    status = snapshot.get("status")
    if (
        not isinstance(symbol, str)
        or not isinstance(name, str)
        or not isinstance(price, (int, float))
        or not isinstance(currency, str)
        or not isinstance(unit, str)
        or not isinstance(timestamp, str)
        or not isinstance(source, str)
        or status not in {"ok", "stale"}
    ):
        return None
    is_market_open = snapshot.get("isMarketOpen")
    if not isinstance(is_market_open, (bool, type(None))):
        is_market_open = None
    change = snapshot.get("change")
    if not isinstance(change, (int, float, type(None))):
        change = None
    change_percent = snapshot.get("changePercent")
    if not isinstance(change_percent, (int, float, type(None))):
        change_percent = None
    return CommodityQuoteRecord(
        symbol=symbol,
        name=name,
        price=round(float(price), 4),
        currency=currency,
        unit=unit,
        change=None if change is None else float(change),
        change_percent=None if change_percent is None else float(change_percent),
        timestamp=timestamp,
        source=source,
        is_market_open=is_market_open,
        status=status,
    )
