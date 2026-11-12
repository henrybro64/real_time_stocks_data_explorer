"""Bounded single-symbol daily/monthly MACD/KD for stock Profile (#331).

Reuses ``tw_research.indicators`` exactly. Reads **stored** daily history only
(``peek_daily_history``) — never opens a research scan or upstream month backfill.
Applies the same coverage gates as the TW research scan.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.models.stock import StockRecord
from app.services.daily_price_history_service import daily_price_history_service
from app.services.market_clock import (
    is_trading_day,
    last_completed_session_date,
)
from app.services.tw_calendar_preload import (
    ensure_tw_calendars_bounded,
    reset_for_tests as _reset_calendar_preload_for_tests,
)
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

_TAIPEI = ZoneInfo("Asia/Taipei")


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
    clock = now or datetime.now(tz=_TAIPEI)
    completed = completed_daily_candles(candles, "TW", now=clock)

    # Bounded calendar preload once per process per year set (never per-symbol loop).
    calendar_years = _calendar_years_for_profile(completed, clock)
    ensure_profile_tw_holiday_calendars(calendar_years)

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
    monthly_payload = _apply_monthly_recency(
        monthly_payload,
        monthly_bars=monthly_bars,
        now=clock,
        expect_through=expect_through,
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


def ensure_profile_tw_holiday_calendars(years: list[int] | tuple[int, ...]) -> bool:
    """Thin alias of ``ensure_tw_calendars_bounded`` for Profile (#331) callers/tests."""
    return ensure_tw_calendars_bounded(years)


def reset_profile_calendar_preload_state_for_tests() -> None:
    """Clear negative cache between unit tests."""
    _reset_calendar_preload_for_tests()


def _calendar_years_for_profile(completed_candles, clock: datetime) -> list[int]:
    years = {_taipei_now(clock).year}
    for candle in completed_candles:
        day = candle_date(candle)
        years.add(day.year)
    return sorted(years)


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


def _taipei_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=_TAIPEI)
    return now.astimezone(_TAIPEI)


def _last_trading_session_of_month(year: int, month: int) -> date | None:
    """Last TW trading day of ``year``-``month`` (weekday fallback if calendar missing)."""
    if month == 12:
        day = date(year, 12, 31)
    else:
        day = date(year, month + 1, 1) - timedelta(days=1)
    for _ in range(31):
        probe = datetime.combine(day, dt_time.min, tzinfo=_TAIPEI)
        if is_trading_day("TW", probe, allow_holiday_fetch=False):
            return day
        day -= timedelta(days=1)
    return None


def _expected_completed_month(
    *,
    now: datetime,
    expect_through: date | None,
) -> tuple[int, int] | None:
    """Latest calendar month strictly before the current Taipei month.

    Requires that month's last trading session ``<= expect_through``. Uses local
    calendar helpers only (no network). Missing holiday calendars fall back to
    weekday sessions via ``is_trading_day(..., allow_holiday_fetch=False)``.
    """
    local = _taipei_now(now)
    year, month = local.year, local.month
    for _ in range(36):
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
        last_session = _last_trading_session_of_month(year, month)
        if last_session is None:
            continue
        if expect_through is None or last_session <= expect_through:
            return (year, month)
    return None


def _apply_monthly_recency(
    payload: dict[str, Any],
    *,
    monthly_bars,
    now: datetime,
    expect_through: date | None,
) -> dict[str, Any]:
    """Fail monthly quality when the tip month is older than the expected completed month."""
    if payload.get("quality") != "ok":
        return payload
    expected = _expected_completed_month(now=now, expect_through=expect_through)
    if expected is None or not monthly_bars:
        return payload
    last = candle_date(monthly_bars[-1])
    last_month = (last.year, last.month)
    if last_month >= expected:
        return payload
    expected_label = f"{expected[0]:04d}-{expected[1]:02d}"
    nulled = _null_snapshot_fields(
        payload,
        quality="stale",
        reason="latest_completed_month_missing",
    )
    nulled["expectedMonth"] = expected_label
    return nulled


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


def _null_snapshot_fields(
    payload: dict[str, Any],
    *,
    quality: str,
    reason: str,
) -> dict[str, Any]:
    """Same nulling path as coverage failure: keep asOf/lastBarDate, clear numerics."""
    out = dict(payload)
    out["quality"] = quality
    out["unavailableReason"] = reason
    out["passStatus"] = None
    out["pass_status"] = None
    for key in _NUMERIC_KEYS:
        out[key] = None
    out["coverage"] = {"ok": False, "reason": reason}
    return out


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

    return _null_snapshot_fields(payload, quality=quality, reason=reason)


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
