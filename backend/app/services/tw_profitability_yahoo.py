"""Yahoo Finance quarterly income adapters for Taiwan profitability margins.

MOPS / open-data XBRL remains the preferred long-term official source (#222), but
it is not yet wired: portal session + statement parsing is a larger follow-up.
v1 uses Yahoo fundamentals timeseries through the shared ``fetch_json`` client
(timeouts + retries), with a bounded yfinance fallback for deeper history.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import UTC, datetime
from typing import Any

from app.services.http_client import HttpFetchError, fetch_json

logger = logging.getLogger(__name__)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_YAHOO_TIMESERIES_URL = (
    "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
)
_TIMESERIES_TYPES = (
    "quarterlyTotalRevenue",
    "quarterlyGrossProfit",
    "quarterlyOperatingIncome",
    "quarterlyPretaxIncome",
    "quarterlyNetIncome",
)
_TIMESERIES_FIELD_MAP = {
    "quarterlyTotalRevenue": "Total Revenue",
    "quarterlyGrossProfit": "Gross Profit",
    "quarterlyOperatingIncome": "Operating Income",
    "quarterlyPretaxIncome": "Pretax Income",
    "quarterlyNetIncome": "Net Income",
}
_HTTP_TIMEOUT_SECONDS = 8.0
_HTTP_RETRIES = 1
_YFINANCE_TIMEOUT_SECONDS = 12.0
_SOURCE_YAHOO_HTTP = "yahoo_finance_quarterly_income"
_SOURCE_YFINANCE = "yfinance_quarterly_income"
# Shared pool: do not use a per-call ``with ThreadPoolExecutor`` — exiting the
# context waits on hung workers and can stall FastAPI request threads.
_YFINANCE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tw-profit-yf")


def fetch_quarterly_income_statement(symbol: str) -> tuple[Mapping[Any, Mapping[Any, Any]], str] | None:
    """Return ``(statement_mapping, source)`` for a bare TW symbol (e.g. ``2330``)."""
    bare = (symbol or "").strip().upper()
    if not bare:
        return None

    for suffix in (".TW", ".TWO"):
        yahoo_symbol = f"{bare}{suffix}"
        http_statement = _fetch_yahoo_timeseries_statement(yahoo_symbol)
        http_periods = _revenue_backed_period_count(http_statement)
        # Prefer shared HTTP client when it already covers the shortest UI range (8Q).
        if http_statement and http_periods >= 8:
            return http_statement, _SOURCE_YAHOO_HTTP

        yf_statement = _fetch_yfinance_statement_bounded(yahoo_symbol)
        yf_periods = _revenue_backed_period_count(yf_statement)
        if yf_statement and yf_periods > http_periods:
            return yf_statement, _SOURCE_YFINANCE
        if http_statement and http_periods > 0:
            return http_statement, _SOURCE_YAHOO_HTTP
    return None


def _revenue_backed_period_count(statement: Mapping[Any, Mapping[Any, Any]] | None) -> int:
    """Count periods that have usable revenue (required for every margin)."""
    if not statement:
        return 0
    revenue_series = None
    for label in ("Total Revenue", "Operating Revenue", "Revenue", "TotalRevenue"):
        candidate = statement.get(label)
        if isinstance(candidate, Mapping) and candidate:
            revenue_series = candidate
            break
    if revenue_series is None:
        return 0
    count = 0
    for value in revenue_series.values():
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isnan(numeric) or math.isinf(numeric):
            continue
        count += 1
    return count


def _yahoo_headers() -> dict[str, str]:
    return {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }


def _fetch_yahoo_timeseries_statement(yahoo_symbol: str) -> dict[str, dict[str, Any]] | None:
    period2 = int(datetime.now(UTC).timestamp())
    # 2000-01-01 UTC — Yahoo still returns a bounded recent window, but we ask wide.
    period1 = 946684800
    url = f"{_YAHOO_TIMESERIES_URL}{yahoo_symbol}"
    try:
        payload = fetch_json(
            url,
            params={
                "symbol": yahoo_symbol,
                "type": ",".join(_TIMESERIES_TYPES),
                "period1": str(period1),
                "period2": str(period2),
            },
            headers=_yahoo_headers(),
            timeout_seconds=_HTTP_TIMEOUT_SECONDS,
            retries=_HTTP_RETRIES,
            provider_name="yahoo_finance",
            operation_name="tw_profitability_timeseries",
            resource_id=yahoo_symbol,
        )
    except (HttpFetchError, TimeoutError, ValueError) as exc:
        logger.info(
            "tw_profitability yahoo_timeseries_failed symbol=%s reason=%s",
            yahoo_symbol,
            exc,
        )
        return None

    return _parse_yahoo_timeseries_payload(payload)


def _parse_yahoo_timeseries_payload(payload: object) -> dict[str, dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    timeseries = payload.get("timeseries")
    result = timeseries.get("result") if isinstance(timeseries, dict) else None
    if not isinstance(result, list) or not result:
        return None

    statement: dict[str, dict[str, Any]] = {
        label: {} for label in _TIMESERIES_FIELD_MAP.values()
    }
    for item in result:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta")
        type_names = meta.get("type") if isinstance(meta, dict) else None
        series_key = None
        if isinstance(type_names, list) and type_names and isinstance(type_names[0], str):
            series_key = type_names[0]
        elif isinstance(type_names, str):
            series_key = type_names
        if series_key is None:
            continue
        row_label = _TIMESERIES_FIELD_MAP.get(series_key)
        if row_label is None:
            continue
        points = item.get(series_key)
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            period_end = point.get("asOfDate")
            if not isinstance(period_end, str) or len(period_end) < 10:
                continue
            reported = point.get("reportedValue")
            raw = reported.get("raw") if isinstance(reported, dict) else None
            statement[row_label][period_end[:10]] = raw

    if not any(statement[label] for label in statement):
        return None
    return statement


def _fetch_yfinance_statement_bounded(yahoo_symbol: str) -> dict[str, dict[str, Any]] | None:
    future = _YFINANCE_EXECUTOR.submit(_fetch_yfinance_statement, yahoo_symbol)
    try:
        return future.result(timeout=_YFINANCE_TIMEOUT_SECONDS)
    except FuturesTimeout:
        future.cancel()
        logger.warning(
            "tw_profitability yfinance_timeout symbol=%s timeout_s=%s",
            yahoo_symbol,
            _YFINANCE_TIMEOUT_SECONDS,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - upstream shapes vary
        logger.info(
            "tw_profitability yfinance_failed symbol=%s reason=%s",
            yahoo_symbol,
            exc,
        )
        return None


def _fetch_yfinance_statement(yahoo_symbol: str) -> dict[str, dict[str, Any]] | None:
    import yfinance as yf  # pylint: disable=import-outside-toplevel

    ticker = yf.Ticker(yahoo_symbol)
    frame = ticker.quarterly_income_stmt
    if frame is None or getattr(frame, "empty", False):
        return None
    return {
        str(index): {
            column: (None if _is_na(value) else value) for column, value in row.items()
        }
        for index, row in frame.to_dict(orient="index").items()
    }


def _is_na(value: object) -> bool:
    try:
        import math

        if isinstance(value, float) and math.isnan(value):
            return True
    except Exception:  # noqa: BLE001
        return False
    try:
        import pandas as pd  # pylint: disable=import-outside-toplevel

        return bool(pd.isna(value))
    except Exception:  # noqa: BLE001
        return False
