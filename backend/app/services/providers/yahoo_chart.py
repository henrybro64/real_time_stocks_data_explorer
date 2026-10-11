"""Shared Yahoo chart quote math for every market.

Quotes are read from ``/v8/finance/chart/{symbol}?interval=1d&range=5d``. With
a multi-day range, ``meta.chartPreviousClose`` is the close *before the
window's first bar*, several sessions back, not the prior session. The prior
session's close has to come from the daily bars (#258).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


def previous_session_close(
    block: dict,
    meta: dict,
    *,
    session_date: date,
    timezone_name: str,
) -> float | None:
    """Close of the last session before ``session_date``, or ``None``.

    Order: the close of the bar immediately before ``session_date``; then
    ``meta.previousClose``; then ``meta.chartPreviousClose`` only when the
    window holds no earlier bar (so it really is the prior session). An older
    bar is never used: if the prior session's close is missing, a fallback to
    it would turn a multi-day move into the daily change.
    """
    timestamps = block.get("timestamp")
    indicators = block.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    first = quotes[0] if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict) else {}
    closes = first.get("close")
    if not isinstance(closes, list):
        closes = []

    earlier_session_in_window = False
    if isinstance(timestamps, list):
        tz = ZoneInfo(timezone_name)
        for index in range(len(timestamps) - 1, -1, -1):
            raw_ts = timestamps[index]
            if not isinstance(raw_ts, (int, float)):
                continue
            if datetime.fromtimestamp(raw_ts, tz=tz).date() >= session_date:
                continue
            earlier_session_in_window = True
            close_value = closes[index] if index < len(closes) else None
            if isinstance(close_value, (int, float)) and close_value > 0:
                return float(close_value)
            break

    meta_previous = meta.get("previousClose")
    if isinstance(meta_previous, (int, float)) and meta_previous > 0:
        return float(meta_previous)
    chart_previous = meta.get("chartPreviousClose")
    if (
        not earlier_session_in_window
        and isinstance(chart_previous, (int, float))
        and chart_previous > 0
    ):
        return float(chart_previous)
    return None


def price_display_decimals(price: float) -> int:
    """Decimals for quote display: four under $1, otherwise two (#275)."""
    return 4 if abs(float(price)) < 1.0 else 2


def coherent_price_change(
    last_price: float,
    previous_close: float,
) -> tuple[float, float, float, float] | None:
    """Round and derive ``change`` / ``change_percent`` from one price pair.

    Computing all four values together means a replaced or fallback
    ``last_price`` can never keep a stale change (#258). Returns ``None`` when
    the previous close rounds to zero, since no meaningful percentage exists.
    Sub-dollar U.S. names keep four decimals so penny moves are not flattened (#275).
    """
    decimals = max(
        price_display_decimals(last_price),
        price_display_decimals(previous_close),
    )
    last = round(float(last_price), decimals)
    previous = round(float(previous_close), decimals)
    if previous <= 0:
        return None
    change = round(last - previous, decimals)
    change_percent = round((change / previous) * 100, 2)
    return last, previous, change, change_percent


def market_time(meta: dict, timezone_name: str) -> datetime | None:
    """``meta.regularMarketTime`` as a local datetime, or ``None``.

    Never substitute the fetch time: that would make an old price look live.
    """
    raw = meta.get("regularMarketTime")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    return datetime.fromtimestamp(raw, tz=ZoneInfo(timezone_name))


def chart_quote_price(
    block: dict,
    meta: dict,
    timezone_name: str,
) -> tuple[datetime, tuple[float, float, float, float]] | None:
    """Market time and coherent price change from one chart response, or ``None``.

    ``None`` when the price, its market time, or the prior session's close is
    missing: a quote is never built from a partial snapshot.
    """
    last_price = meta.get("regularMarketPrice")
    if not isinstance(last_price, (int, float)) or isinstance(last_price, bool):
        return None
    quote_time = market_time(meta, timezone_name)
    if quote_time is None:
        return None
    previous = previous_session_close(
        block,
        meta,
        session_date=quote_time.date(),
        timezone_name=timezone_name,
    )
    if previous is None:
        return None
    priced = coherent_price_change(float(last_price), previous)
    return None if priced is None else (quote_time, priced)
