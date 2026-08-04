"""USD/TWD FX snapshot for portfolio conversion and Market Snapshot."""

from __future__ import annotations

from datetime import datetime
import logging
import math
from typing import Any

from zoneinfo import ZoneInfo
from app.services.http_client import HttpFetchError, fetch_json
from app.services.market_snapshot_cache import (
    clear_market_snapshot_cache_for_tests,
    get_cached_snapshot,
    make_cache_payload,
    payload_age_seconds,
    set_cached_snapshot,
)

_YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    "TWD%3DX?interval=1d&range=5d"
)
_REQUEST_TIMEOUT_SECONDS = 8
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_FALLBACK_USD_TWD_RATE = 32.0
_SOURCE = "Yahoo Finance"
_SOFT_TTL_SECONDS = 60.0
_HARD_TTL_SECONDS = 900.0

logger = logging.getLogger(__name__)

_CACHE_KEY = "fx:usd_twd"


def clear_fx_cache_for_tests() -> None:
    clear_market_snapshot_cache_for_tests()


def get_usd_twd_fx_snapshot() -> dict[str, Any]:
    cached_payload = get_cached_snapshot(_CACHE_KEY)
    cached_snapshot = _extract_snapshot(cached_payload)
    cached_age = payload_age_seconds(cached_payload) if cached_payload is not None else None
    if cached_snapshot is not None and cached_age is not None and cached_age < _SOFT_TTL_SECONDS:
        return {**cached_snapshot, "status": "ok"}

    parsed = _fetch_usd_twd_snapshot()
    if parsed is not None:
        payload = {**parsed, "status": "ok"}
        set_cached_snapshot(
            _CACHE_KEY,
            make_cache_payload(payload),
            ttl_seconds=int(_HARD_TTL_SECONDS),
        )
        return payload

    if cached_snapshot is not None and cached_age is not None and cached_age < _HARD_TTL_SECONDS:
        logger.warning(
            "USD/TWD FX fetch failed; serving retained quote as stale",
            extra={
                "event": "fx.usd_twd.stale_served",
                "provider": "yahoo_finance",
                "cache_age_seconds": round(cached_age, 1),
            },
        )
        return {**cached_snapshot, "status": "stale"}

    logger.warning(
        "USD/TWD FX fetch failed; using fallback rate",
        extra={
            "event": "fx.usd_twd.fallback_used",
            "provider": "yahoo_finance",
            "fallback_rate": _FALLBACK_USD_TWD_RATE,
        },
    )
    return {
        "pair": "USD/TWD",
        "rate": _FALLBACK_USD_TWD_RATE,
        "timestamp": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "change": None,
        "changePercent": None,
        "source": _SOURCE,
        "status": "fallback",
    }


def _fetch_usd_twd_rate() -> float | None:
    snapshot = _fetch_usd_twd_snapshot()
    return None if snapshot is None else float(snapshot["rate"])


def _fetch_usd_twd_snapshot() -> dict[str, Any] | None:
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
            operation_name="fx_usd_twd",
            resource_id="USD/TWD",
        )
    except HttpFetchError:
        return None
    return _normalize_yahoo_fx_payload(payload)


def _normalize_yahoo_fx_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
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
    rate = _coerce_positive_finite(meta.get("regularMarketPrice"))
    if rate is None:
        return None

    previous_close = _coerce_positive_finite(
        meta.get("chartPreviousClose")
        if meta.get("chartPreviousClose") is not None
        else meta.get("previousClose")
    )
    change: float | None = None
    change_percent: float | None = None
    if previous_close is not None:
        change = round(rate - previous_close, 4)
        change_percent = round((change / previous_close) * 100, 4)

    market_time = meta.get("regularMarketTime")
    tz = ZoneInfo("Asia/Taipei")
    if isinstance(market_time, (int, float)):
        timestamp = datetime.fromtimestamp(market_time, tz=tz).isoformat()
    else:
        timestamp = datetime.now(tz).isoformat()

    return {
        "pair": "USD/TWD",
        "rate": round(rate, 4),
        "timestamp": timestamp,
        "change": change,
        "changePercent": change_percent,
        "source": _SOURCE,
    }


def _coerce_positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _extract_snapshot(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    return snapshot
