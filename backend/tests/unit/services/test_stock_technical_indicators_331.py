"""Single-symbol MACD/KD profile values (#331)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.stock_technical_indicators import (
    StockTechnicalIndicatorsError,
    ensure_profile_tw_holiday_calendars,
    get_stock_technical_indicators,
    reset_profile_calendar_preload_state_for_tests,
)
from app.services.tw_research.constants import CALCULATION_VERSION
from app.services.tw_research.indicators import (
    daily_macd_kd_snapshot,
    monthly_macd_kd_snapshot,
)
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


def _stock():
    return stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _candles_ending(
    end: date,
    *,
    sessions: int = 800,
    rising: bool = True,
    skip_months: set[tuple[int, int]] | None = None,
) -> list[CandleRecord]:
    """Build weekday bars ending on ``end`` (inclusive when end is a weekday)."""
    skip_months = skip_months or set()
    out: list[CandleRecord] = []
    price = 50.0 if rising else 200.0
    day = end
    while len(out) < sessions:
        if day.weekday() < 5 and (day.year, day.month) not in skip_months:
            price = price + (0.12 if rising else -0.1)
            if price < 5:
                price = 5
            out.append(
                CandleRecord(
                    time=datetime(day.year, day.month, day.day, 13, 30, tzinfo=TZ).isoformat(),
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=1000,
                )
            )
        day -= timedelta(days=1)
    out.reverse()
    return out


def _is_weekday_session(market, day, allow_holiday_fetch=False) -> bool:  # noqa: ARG001
    probe = day
    if isinstance(probe, datetime):
        probe = probe.date()
    return probe.weekday() < 5


def _patch_weekday_calendar(monkeypatch) -> None:
    reset_profile_calendar_preload_state_for_tests()
    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_calendar_preload.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.is_trading_day",
        _is_weekday_session,
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.is_trading_day",
        _is_weekday_session,
    )
    monkeypatch.setattr(
        "app.services.market_clock.is_trading_day",
        _is_weekday_session,
    )


def test_fresh_fixture_daily_and_monthly_ok(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    # Last completed weekday before/at now Friday 2024-06-28 after close → 2024-06-28.
    end = date(2024, 6, 28)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: end,
    )
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    assert payload["daily"]["quality"] == "ok"
    assert payload["monthly"]["quality"] == "ok"
    assert payload["daily"]["macdDif"] is not None
    assert payload["monthly"]["macdDif"] is not None
    assert payload["daily"]["coverage"]["ok"] is True
    assert payload["monthly"]["coverage"]["ok"] is True
    # Expected completed month is May 2024 (strictly before current Taipei June).
    as_of = str(payload["monthly"].get("asOf") or "")
    assert as_of.startswith("2024-05")
    assert payload["calculationVersion"] == CALCULATION_VERSION
    assert payload["upstreamFetch"] is False


def test_open_month_behind_does_not_null_completed_monthly(monkeypatch) -> None:
    """Current-month daily backfill gap must not discard verified completed months."""
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 11, 12, 16, 0, tzinfo=TZ)
    expect = date(2024, 11, 12)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: expect,
    )
    # History through Nov 11 — completed October month is intact; open November is behind.
    end = date(2024, 11, 11)
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, True),
        now=now,
    )
    assert payload["missingUpstreamMonths"] is True
    assert payload["monthly"]["missingUpstreamMonths"] is True
    assert payload["monthly"]["quality"] == "ok"
    assert payload["monthly"]["macdDif"] is not None
    assert payload["monthly"]["coverage"]["ok"] is True


def test_stale_when_bars_end_years_ago(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2026, 11, 12, 16, 0, tzinfo=TZ)
    expect = date(2026, 11, 12)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: expect,
    )
    end = date(2021, 12, 31)
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    assert payload["daily"]["quality"] == "stale"
    assert payload["daily"]["macdDif"] is None
    assert payload["daily"]["passStatus"] is None
    assert payload["daily"]["asOf"] is not None
    assert payload["daily"]["coverage"]["ok"] is False
    assert payload["monthly"]["quality"] == "stale"
    assert payload["monthly"]["unavailableReason"] == "latest_completed_month_missing"
    assert payload["monthly"]["macdDif"] is None
    assert payload["monthly"]["expectedMonth"] == "2026-10"


def test_source_incomplete_when_four_months_missing(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    end = date(2024, 6, 28)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: end,
    )
    skip = {(2024, 1), (2024, 2), (2024, 3), (2024, 4)}
    candles = _candles_ending(end, sessions=800, skip_months=skip)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    assert payload["daily"]["quality"] == "source_incomplete"
    assert payload["daily"]["macdDif"] is None
    assert payload["daily"]["unavailableReason"]


def test_truncated_october_never_partial_month(monkeypatch) -> None:
    """Store ending 28 Oct with now=3 Nov → monthly stale; expectedMonth=October."""
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2026, 11, 3, 16, 0, tzinfo=TZ)
    expect = date(2026, 11, 2)  # Mon before Tue Nov 3 (weekday calendar)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: expect,
    )
    end = date(2026, 10, 28)
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    monthly = payload["monthly"]
    assert monthly["quality"] == "stale"
    assert monthly["unavailableReason"] == "latest_completed_month_missing"
    assert monthly["expectedMonth"] == "2026-10"
    assert monthly["macdDif"] is None
    assert monthly["passStatus"] is None
    as_of = str(monthly.get("asOf") or monthly.get("lastBarDate") or "")
    assert not as_of.startswith("2026-10"), "must not expose partial October as ok"


def test_profile_parity_with_tw_research_snapshots(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    end = date(2024, 6, 28)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: end,
    )
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    assert payload["daily"]["quality"] == "ok"
    assert payload["monthly"]["quality"] == "ok"
    daily = daily_macd_kd_snapshot(candles, market="TW", now=now)
    monthly = monthly_macd_kd_snapshot(candles, market="TW", now=now)
    assert payload["daily"]["dif"] == pytest.approx(float(daily.dif))
    assert payload["daily"]["signal"] == pytest.approx(float(daily.signal))
    assert payload["daily"]["k"] == pytest.approx(float(daily.k))
    assert payload["monthly"]["dif"] == pytest.approx(float(monthly.dif))
    assert payload["monthly"]["signal"] == pytest.approx(float(monthly.signal))
    assert payload["monthly"]["k"] == pytest.approx(float(monthly.k))


def test_insufficient_history_is_unavailable_not_zero(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: date(2024, 6, 28),
    )
    candles = _candles_ending(date(2024, 6, 28), sessions=10)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: candles,  # legacy list-only loader
        now=now,
    )
    assert payload["daily"]["quality"] in {"insufficient_history", "source_incomplete", "stale"}
    for key in (
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
    ):
        assert payload["daily"][key] is None
    assert payload["daily"]["passStatus"] is None


def test_us_stock_rejected() -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    with pytest.raises(StockTechnicalIndicatorsError) as exc:
        get_stock_technical_indicators(
            "US:AAPL",
            stock_loader=lambda _sid: stock,
            candles_loader=lambda _stock: [],
        )
    assert exc.value.code == "unsupported_market"


def test_calendar_preload_called_once_across_consecutive_calls(monkeypatch) -> None:
    reset_profile_calendar_preload_state_for_tests()
    calls: list[tuple[int, ...]] = []

    monkeypatch.setattr(
        "app.services.tw_calendar_preload.ensure_tw_holiday_calendars_loaded",
        lambda years: False,
    )

    def _preload(years, *, allow_network=True, max_fetches=4):
        del allow_network, max_fetches
        key = tuple(sorted(int(y) for y in years))
        calls.append(key)
        return {"ok": False, "loaded": [], "missing": list(key), "fetched": 0}

    monkeypatch.setattr(
        "app.services.tw_calendar_preload.preload_tw_holiday_calendars",
        _preload,
    )
    years = [2024, 2025]
    for _ in range(5):
        assert ensure_profile_tw_holiday_calendars(years) is False
    assert len(calls) == 1
    assert calls[0] == (2024, 2025)


def test_calendar_preload_failure_yields_trading_calendar_unavailable(
    monkeypatch,
) -> None:
    reset_profile_calendar_preload_state_for_tests()
    monkeypatch.setattr(
        "app.services.tw_calendar_preload.ensure_tw_holiday_calendars_loaded",
        lambda years: False,
    )
    monkeypatch.setattr(
        "app.services.tw_calendar_preload.preload_tw_holiday_calendars",
        lambda years, *, allow_network=True, max_fetches=4: {
            "ok": False,
            "loaded": [],
            "missing": list(years),
            "fetched": 0,
        },
    )
    # Coverage path also consults ensure_tw_holiday via tw_research.coverage.
    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: False,
    )
    now = datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    end = date(2024, 6, 28)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: end,
    )
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    assert payload["daily"]["unavailableReason"] == "trading_calendar_unavailable"
    assert payload["daily"]["macdDif"] is None
    assert payload["monthly"]["quality"] != "ok"
    assert payload["monthly"]["unavailableReason"] in {
        "trading_calendar_unavailable",
        "latest_completed_month_missing",
        "source_incomplete",
        "insufficient_history",
        "insufficient_monthly_history",
    }
    assert payload["monthly"]["macdDif"] is None


def test_calendar_present_skips_preload(monkeypatch) -> None:
    reset_profile_calendar_preload_state_for_tests()
    preload_calls: list[object] = []
    monkeypatch.setattr(
        "app.services.tw_calendar_preload.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_calendar_preload.preload_tw_holiday_calendars",
        lambda years, *, allow_network=True, max_fetches=4: preload_calls.append(years)
        or {"ok": True, "loaded": list(years), "missing": [], "fetched": 0},
    )
    assert ensure_profile_tw_holiday_calendars([2024]) is True
    assert preload_calls == []
