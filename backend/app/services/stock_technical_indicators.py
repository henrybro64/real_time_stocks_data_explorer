"""Bounded single-symbol daily/monthly MACD/KD for stock Profile (#331).

Reuses ``tw_research.indicators`` exactly. Reads **stored** daily history only
(``peek_daily_history``) — never opens a research scan or upstream month backfill.
Applies the same coverage gates as the TW research scan.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from app.models.stock import StockRecord
from app.services.daily_price_history_service import daily_price_history_service
from app.services.market_clock import last_completed_session_date
from app.services.moving_average import completed_daily_candles
from app.services.quote_service import quote_service
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
    PREFERRED_DAILY_HISTORY_LIMIT,
    PRICE_BASIS,
)
from app.services.tw_research.coverage import (
    candle_date,
    monthly_series_verified,
    trim_truncated_leading_months,
    validate_closed_month_coverage,
    validate_ohlcv_series,
)
from app.services.tw_research.indicators import (
    assemble_monthly_candles,
    daily_macd_kd_snapshot,
    monthly_macd_kd_snapshot,
    snapshot_from_candles,
)


class StockTechnicalIndicatorsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_stock_technical_indicators(
    stock_id: str,
    *,
    now: datetime | None = None,
    stock_loader=None,
    candles_loader=None,
) -> dict[str, Any]:
    """Return daily + completed-monthly MACD/KD for one catalog stock.

    Non-TW stocks raise ``unsupported_market``. Missing catalog raises
    ``not_found``. Insufficient/gapped/stale history returns quality flags —
    never fake zeros.
    """
    loader = stock_loader or quote_service.get_catalog_stock
    stock = loader(stock_id)
    if stock is None:
        raise StockTechnicalIndicatorsError("not_found", "stock_not_in_catalog")
    if stock.market != "TW" and not str(stock.id).startswith("TW:"):
        raise StockTechnicalIndicatorsError(
            "unsupported_market",
            "macd_kd_profile_values_are_taiwan_only",
        )

    candles, missing_upstream_months = _load_stored_candles(
        candles_loader or _default_stored_candles, stock
    )
    clock = now or datetime.now()
    completed = completed_daily_candles(candles, "TW", now=clock)
    expect_through = last_completed_session_date(
        "TW", clock, allow_holiday_fetch=False
    )

    daily_coverage = validate_ohlcv_series(
        completed,
        market="TW",
        check_calendar_gaps=True,
        allow_holiday_fetch=False,
        expect_through=expect_through,
    )
    daily_snap = daily_macd_kd_snapshot(candles, market="TW", now=clock)
    daily_payload = _apply_coverage_to_snapshot(
        daily_snap,
        coverage_ok=daily_coverage.ok,
        coverage_reason=daily_coverage.reason,
        expect_through=expect_through,
        missing_upstream_months=bool(missing_upstream_months),
    )

    monthly_bars = assemble_monthly_candles(completed, now=clock)
    monthly_bars = trim_truncated_leading_months(
        completed,
        monthly_bars,
        market="TW",
        allow_holiday_fetch=False,
    )
    # Drop trailing incomplete closed months (e.g. store ending 28 Oct must not
    # yield a partial October bar when now is 3 Nov).
    monthly_bars = _drop_incomplete_trailing_months(completed, monthly_bars)
    monthly_coverage = monthly_series_verified(
        completed,
        monthly_bars,
        market="TW",
        allow_holiday_fetch=False,
    )
    if monthly_bars:
        monthly_snap = snapshot_from_candles(
            monthly_bars,
            timeframe="monthly",
            min_bars=MIN_MONTHLY_BARS_FOR_MACD_KD,
        )
    else:
        monthly_snap = monthly_macd_kd_snapshot(candles, market="TW", now=clock)
    monthly_payload = _apply_coverage_to_snapshot(
        monthly_snap,
        coverage_ok=monthly_coverage.ok and not missing_upstream_months,
        coverage_reason=(
            "missing_upstream_months"
            if missing_upstream_months and monthly_coverage.ok
            else monthly_coverage.reason
        ),
        expect_through=expect_through,
        missing_upstream_months=bool(missing_upstream_months),
        timeframe="monthly",
    )

    return {
        "stockId": stock.id,
        "exchange": stock.exchange,
        "market": stock.market,
        "priceBasis": PRICE_BASIS,
        "calculationVersion": CALCULATION_VERSION,
        "historySource": "daily_price_history_service.peek_daily_history",
        "upstreamFetch": False,
        "storedBarCount": len(candles),
        "missingUpstreamMonths": bool(missing_upstream_months),
        "daily": daily_payload,
        "monthly": monthly_payload,
        "notes": [
            "MACD: DIF=EMA12−EMA26; Signal=EMA9(DIF); OSC=DIF−Signal (not ×2).",
            "KD(9,3,3) with RSV seed rules matching tw_research.indicators.",
            "Monthly uses last fully completed calendar months only.",
            "Coverage matches tw_research scan (validate_ohlcv_series / monthly_series_verified).",
            "quality=insufficient_history|source_incomplete|stale|unavailable means values are not validated.",
            "Does not start a TW research scan or unbounded upstream backfill.",
        ],
    }


def _default_stored_candles(stock: StockRecord):
    candles, missing = daily_price_history_service.peek_daily_history(
        stock,
        "MAX",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
    )
    return list(candles), bool(missing)


def _load_stored_candles(loader, stock: StockRecord) -> tuple[list, bool]:
    """Accept ``(candles, missing)`` or legacy ``candles``-only loaders."""
    result = loader(stock)
    if isinstance(result, tuple) and len(result) == 2:
        candles, missing = result
        return list(candles or []), bool(missing)
    if result is None:
        return [], False
    return list(result), False


def _drop_incomplete_trailing_months(daily_candles, monthly_bars):
    trimmed = list(monthly_bars)
    while trimmed:
        last = trimmed[-1]
        day = candle_date(last)
        report = validate_closed_month_coverage(
            daily_candles,
            year=day.year,
            month=day.month,
            market="TW",
            allow_holiday_fetch=False,
        )
        if report.ok:
            break
        trimmed = trimmed[:-1]
    return trimmed


_NUMERIC_KEYS = (
    "dif",
    "signal",
    "osc",
    "k",
    "d",
    "macdDif",
    "macdSignal",
    "macdHistogram",
    "kdK",
    "kdD",
)


def _apply_coverage_to_snapshot(
    snapshot,
    *,
    coverage_ok: bool,
    coverage_reason: str | None,
    expect_through: date | None,
    missing_upstream_months: bool,
    timeframe: str | None = None,
) -> dict[str, Any]:
    del timeframe
    payload = _snapshot_payload(snapshot)
    last_bar_date = payload.get("asOf")
    payload["lastBarDate"] = last_bar_date
    payload["missingUpstreamMonths"] = bool(missing_upstream_months)

    if coverage_ok and payload.get("quality") == "ok":
        payload["coverage"] = {"ok": True, "reason": None}
        return payload

    # Prefer indicator insufficient_history when bars are too few.
    if payload.get("quality") == "insufficient_history":
        payload["coverage"] = {
            "ok": False,
            "reason": coverage_reason or "insufficient_history",
        }
        payload["unavailableReason"] = "insufficient_history"
        return payload

    quality = "source_incomplete"
    reason = coverage_reason or "source_incomplete"
    if expect_through is not None and last_bar_date:
        try:
            last_day = date.fromisoformat(str(last_bar_date)[:10])
            if last_day < expect_through:
                quality = "stale"
                reason = coverage_reason or "latest_bar_before_last_completed_session"
        except ValueError:
            pass
    if missing_upstream_months and quality != "stale":
        quality = "source_incomplete"
        reason = "missing_upstream_months"

    # Null numerics / passStatus; keep asOf/lastBarDate.
    payload["quality"] = quality
    payload["unavailableReason"] = reason
    payload["passStatus"] = None
    payload["pass_status"] = None
    for key in _NUMERIC_KEYS:
        payload[key] = None
    payload["coverage"] = {"ok": False, "reason": reason}
    return payload


def _snapshot_payload(snapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    # Public API aliases for chart/analyst clarity (#331).
    payload["macdDif"] = payload.get("dif")
    payload["macdSignal"] = payload.get("signal")
    payload["macdHistogram"] = payload.get("osc")
    payload["kdK"] = payload.get("k")
    payload["kdD"] = payload.get("d")
    payload["passStatus"] = payload.get("pass_status")
    payload["asOf"] = payload.get("as_of")
    payload["priceBasis"] = payload.get("price_basis")
    payload["calculationVersion"] = payload.get("calculation_version")
    payload["barCount"] = payload.get("bar_count")
    # Keep snake_case + aliases for research parity. When quality is not ok,
    # force numeric fields to null so clients never treat missing as zero.
    if payload.get("quality") != "ok":
        payload["unavailableReason"] = payload.get("quality")
        payload["passStatus"] = None
        for key in _NUMERIC_KEYS:
            payload[key] = None
    else:
        for key in _NUMERIC_KEYS:
            value = payload.get(key)
            if value is None:
                continue
            # Reject accidental NaN / non-finite values rather than coercing to 0.
            try:
                if not isinstance(value, (int, float)) or value != value:  # noqa: PLR0124
                    payload[key] = None
                elif isinstance(value, float) and (
                    value == float("inf") or value == float("-inf")
                ):
                    payload[key] = None
            except (TypeError, ValueError):
                payload[key] = None
    return payload
