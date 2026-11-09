"""OHLCV coverage and research readiness validation (#320).

Having enough candle *rows* is not the same as sufficient trading-session
coverage. Gaps are judged against the Taiwan trading calendar (holidays
excluded), not every weekday.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.services.market_clock import (
    ensure_tw_holiday_calendars_loaded,
    is_trading_day,
    preload_tw_holiday_calendars,
)
from app.services.tw_research.constants import (
    MIN_DAILY_BARS_FOR_MACD_KD,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
    PRICE_BASIS,
)

_MARKET_TZ = {
    "TW": ZoneInfo("Asia/Taipei"),
    "US": ZoneInfo("America/New_York"),
}

Readiness = Literal[
    "processed",
    "technically_qualified",
    "source_incomplete",
    "verified_ready",
    "rejected",
]


@dataclass(frozen=True)
class CoverageReport:
    ok: bool
    reason: str | None
    bar_count: int
    missing_trading_days: tuple[str, ...] = ()
    duplicate_dates: tuple[str, ...] = ()
    invalid_bars: int = 0
    price_basis: str = PRICE_BASIS


def candle_date(candle: CandleRecord) -> date:
    return datetime.fromisoformat(candle.time).date()


def validate_ohlcv_series(
    candles: list[CandleRecord],
    *,
    market: str = "TW",
    require_min_bars: int | None = None,
    check_calendar_gaps: bool = True,
    allow_holiday_fetch: bool = False,
    expect_through: date | None = None,
) -> CoverageReport:
    """Validate order, uniqueness, finite OHLC consistency, and optional gaps.

    When ``expect_through`` is set, history must cover trading sessions through
    that date (typically the scan's trading day). Trailing gaps are otherwise
    invisible if the series simply ends early.
    """
    if not candles:
        return CoverageReport(ok=False, reason="no_candles", bar_count=0)

    dates: list[date] = []
    invalid = 0
    for candle in candles:
        try:
            day = candle_date(candle)
        except ValueError:
            invalid += 1
            continue
        dates.append(day)
        values = (candle.open, candle.high, candle.low, candle.close)
        if any(not math.isfinite(float(value)) for value in values):
            invalid += 1
            continue
        if float(candle.high) < float(candle.low):
            invalid += 1
            continue
        if not (float(candle.low) <= float(candle.close) <= float(candle.high)):
            invalid += 1
            continue
        if float(candle.volume) < 0:
            invalid += 1

    if invalid:
        return CoverageReport(
            ok=False,
            reason="invalid_ohlcv_values",
            bar_count=len(candles),
            invalid_bars=invalid,
        )

    if dates != sorted(dates):
        return CoverageReport(ok=False, reason="dates_not_ascending", bar_count=len(candles))

    seen: set[date] = set()
    duplicates: list[str] = []
    for day in dates:
        if day in seen:
            duplicates.append(day.isoformat())
        seen.add(day)
    if duplicates:
        return CoverageReport(
            ok=False,
            reason="duplicate_session_dates",
            bar_count=len(candles),
            duplicate_dates=tuple(duplicates),
        )

    min_bars = require_min_bars if require_min_bars is not None else MIN_DAILY_BARS_FOR_MACD_KD
    if len(candles) < min_bars:
        return CoverageReport(
            ok=False,
            reason="insufficient_history",
            bar_count=len(candles),
        )

    missing: list[str] = []
    range_end = dates[-1]
    if expect_through is not None and expect_through > range_end:
        range_end = expect_through
    if check_calendar_gaps and len(dates) >= 1 and (
        len(dates) >= 2 or expect_through is not None
    ):
        if not _calendar_ready(dates[0], range_end, market=market):
            return CoverageReport(
                ok=False,
                reason="trading_calendar_unavailable",
                bar_count=len(candles),
            )
        missing = _missing_trading_days(
            dates[0],
            range_end,
            present=set(dates),
            market=market,
            allow_holiday_fetch=allow_holiday_fetch,
        )
        if missing:
            return CoverageReport(
                ok=False,
                reason="missing_trading_sessions",
                bar_count=len(candles),
                missing_trading_days=tuple(missing),
            )

    return CoverageReport(ok=True, reason=None, bar_count=len(candles))


def validate_closed_month_coverage(
    daily_candles: list[CandleRecord],
    *,
    year: int,
    month: int,
    market: str = "TW",
    allow_holiday_fetch: bool = False,
) -> CoverageReport:
    """A closed calendar month is only complete when every TW trading day is present."""
    month_days = [
        candle
        for candle in daily_candles
        if (d := candle_date(candle)).year == year and d.month == month
    ]
    if not month_days:
        return CoverageReport(ok=False, reason="month_has_no_bars", bar_count=0)

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)

    if not _calendar_ready(start, end, market=market):
        return CoverageReport(
            ok=False,
            reason="trading_calendar_unavailable",
            bar_count=len(month_days),
        )
    present = {candle_date(candle) for candle in month_days}
    expected = _trading_days_in_range(
        start,
        end,
        market=market,
        allow_holiday_fetch=allow_holiday_fetch,
    )
    missing = sorted(day.isoformat() for day in expected if day not in present)
    if missing:
        return CoverageReport(
            ok=False,
            reason="incomplete_closed_month",
            bar_count=len(month_days),
            missing_trading_days=tuple(missing),
        )
    return CoverageReport(ok=True, reason=None, bar_count=len(month_days))


def trim_truncated_leading_months(
    daily_candles: list[CandleRecord],
    monthly_bars: list[CandleRecord],
    *,
    market: str = "TW",
    allow_holiday_fetch: bool = False,
) -> list[CandleRecord]:
    """Drop leading months that are incomplete solely because history is sliced mid-month.

    The preferred daily history limit often starts partway through a month; that
    truncated boundary must not poison monthly MACD/KD or coverage (#320).
    """
    if not daily_candles or not monthly_bars:
        return list(monthly_bars)
    trimmed = list(monthly_bars)
    first_day = candle_date(daily_candles[0])
    while trimmed:
        lead = candle_date(trimmed[0])
        if (lead.year, lead.month) != (first_day.year, first_day.month):
            break
        report = validate_closed_month_coverage(
            daily_candles,
            year=lead.year,
            month=lead.month,
            market=market,
            allow_holiday_fetch=allow_holiday_fetch,
        )
        if report.ok:
            break
        if report.reason == "trading_calendar_unavailable" and first_day.day <= 1:
            break
        trimmed = trimmed[1:]
        break  # only the leading boundary month is eligible for this trim
    return trimmed


def monthly_series_verified(
    daily_candles: list[CandleRecord],
    monthly_bars: list[CandleRecord],
    *,
    market: str = "TW",
    allow_holiday_fetch: bool = False,
) -> CoverageReport:
    bars = trim_truncated_leading_months(
        daily_candles,
        monthly_bars,
        market=market,
        allow_holiday_fetch=allow_holiday_fetch,
    )
    if len(bars) < MIN_MONTHLY_BARS_FOR_MACD_KD:
        return CoverageReport(
            ok=False,
            reason="insufficient_monthly_history",
            bar_count=len(bars),
        )
    for bar in bars:
        day = candle_date(bar)
        report = validate_closed_month_coverage(
            daily_candles,
            year=day.year,
            month=day.month,
            market=market,
            allow_holiday_fetch=allow_holiday_fetch,
        )
        if not report.ok:
            return report
    return CoverageReport(ok=True, reason=None, bar_count=len(bars))


def derive_readiness(
    *,
    stage: str,
    daily_pass: str | None,
    monthly_pass: str | None,
    daily_coverage_ok: bool,
    monthly_coverage_ok: bool,
    rejection_reason: str | None = None,
    coverage_checked: bool = True,
) -> Readiness:
    """Map signal + coverage into honest readiness (#320).

    - ``processed``: scanned, not dual-pass
    - ``technically_qualified``: dual-pass signals, coverage not yet asserted
    - ``source_incomplete``: dual-pass signals but calendar/OHLCV coverage failed
    - ``verified_ready``: dual-pass signals and coverage validated (symbol-level)
    - ``rejected``: universe/catalog/budget rejection

    Scan-level ``quality_status=verified_ready`` is reserved for a successful
    immutable finalize (#318); dual-pass+coverage alone is not enough at scan level.
    """
    if stage == "rejected" or rejection_reason:
        return "rejected"
    dual_pass = daily_pass == "pass" and monthly_pass == "pass"
    if not dual_pass:
        return "processed"
    if not coverage_checked:
        return "technically_qualified"
    if not daily_coverage_ok or not monthly_coverage_ok:
        return "source_incomplete"
    return "verified_ready"


def preflight_tw_trading_calendars(
    *,
    trading_day: str,
    history_years: int = 4,
    allow_network: bool = True,
    max_fetches: int = 4,
) -> dict[str, object]:
    """Bounded calendar preflight for a research trading day (#320).

    Loads disk-backed calendars first, then optionally fetches a small number of
    still-missing years. Missing years remain explicit so coverage can fail closed
    as ``trading_calendar_unavailable`` instead of inventing weekday sessions.
    """
    try:
        day = date.fromisoformat(trading_day)
    except ValueError:
        return {
            "ok": False,
            "loaded": [],
            "missing": [],
            "fetched": 0,
            "error": "invalid_trading_day",
        }
    years = set(range(day.year - max(0, int(history_years)), day.year + 1))
    return preload_tw_holiday_calendars(
        years,
        allow_network=allow_network,
        max_fetches=max_fetches,
    )


def _calendar_ready(start: date, end: date, *, market: str) -> bool:
    if market != "TW":
        return True
    years = set(range(start.year, end.year + 1))
    return ensure_tw_holiday_calendars_loaded(years)


def _trading_days_in_range(
    start: date,
    end: date,
    *,
    market: str,
    allow_holiday_fetch: bool,
) -> list[date]:
    days: list[date] = []
    tz = _MARKET_TZ.get(market, ZoneInfo("Asia/Taipei"))
    probe = start
    while probe <= end:
        # is_trading_day expects a timezone-aware datetime, not a bare date.
        stamp = datetime.combine(probe, time(12, 0), tzinfo=tz)
        if is_trading_day(market, stamp, allow_holiday_fetch=allow_holiday_fetch):
            days.append(probe)
        probe += timedelta(days=1)
    return days


def _missing_trading_days(
    start: date,
    end: date,
    *,
    present: set[date],
    market: str,
    allow_holiday_fetch: bool,
) -> list[str]:
    expected = _trading_days_in_range(
        start,
        end,
        market=market,
        allow_holiday_fetch=allow_holiday_fetch,
    )
    return [day.isoformat() for day in expected if day not in present]
