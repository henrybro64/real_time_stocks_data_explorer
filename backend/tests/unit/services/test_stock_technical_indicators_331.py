"""Single-symbol MACD/KD profile values (#331)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.stock_technical_indicators import (
    StockTechnicalIndicatorsError,
    get_stock_technical_indicators,
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
    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
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
    assert payload["calculationVersion"] == CALCULATION_VERSION
    assert payload["upstreamFetch"] is False


def test_stale_when_bars_end_years_ago(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    expect = date(2024, 6, 28)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: expect,
    )
    end = date(2021, 6, 28)  # ~3 years before now
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
    """Store ending 28 Oct with now=3 Nov → September asOf or source_incomplete."""
    _patch_weekday_calendar(monkeypatch)
    now = datetime(2024, 11, 3, 16, 0, tzinfo=TZ)
    expect = date(2024, 11, 1)  # Fri before Mon Nov 3 (weekday calendar)
    monkeypatch.setattr(
        "app.services.stock_technical_indicators.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: expect,
    )
    end = date(2024, 10, 28)
    candles = _candles_ending(end, sessions=800)
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: _stock(),
        candles_loader=lambda _stock: (candles, False),
        now=now,
    )
    monthly = payload["monthly"]
    as_of = str(monthly.get("asOf") or "")
    if monthly["quality"] == "ok":
        assert not as_of.startswith("2024-10"), "must not expose partial October"
        assert as_of.startswith("2024-09") or as_of < "2024-10"
    else:
        assert monthly["quality"] in {"source_incomplete", "stale", "insufficient_history"}
        assert monthly["macdDif"] is None


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
