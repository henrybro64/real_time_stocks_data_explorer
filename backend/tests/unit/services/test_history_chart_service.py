from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.services import history_chart_service
from app.services.history_chart_service import get_history_chart
from tests.support import candle_record, stock_record


def _stock():
    return stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple",
    )


def _series(count: int, start: float = 100.0, *, end: datetime | None = None) -> list[CandleRecord]:
    tz = ZoneInfo("America/New_York")
    anchor = end or datetime(2026, 8, 2, 16, 0, tzinfo=tz)
    candles: list[CandleRecord] = []
    day_offset = 0
    while len(candles) < count:
        day = anchor - timedelta(days=day_offset)
        day_offset += 1
        if day.weekday() >= 5:
            continue
        index = count - len(candles) - 1
        candles.append(
            candle_record(
                time=day.replace(hour=16, minute=0, second=0, microsecond=0).isoformat(),
                open_price=start + index,
                high=start + index + 1,
                low=start + index - 1,
                close=start + index,
                volume=1_000 + index,
            )
        )
    candles.reverse()
    return candles


def test_intraday_ranges_return_null_moving_averages(monkeypatch) -> None:
    stock = _stock()
    candles = _series(8)

    monkeypatch.setattr(
        history_chart_service.quote_service,
        "get_history",
        lambda stock_id, range_name, *, limit=None: (stock, candles),
    )

    payload = get_history_chart("US:AAPL", "1D")

    assert payload is not None
    assert payload.moving_averages is None
    assert len(payload.candles) == 8


def test_daily_range_uses_warmup_then_trims_visible_candles(monkeypatch) -> None:
    stock = _stock()
    full = _series(90)
    captured: dict[str, object] = {}

    def fake_get_history(stock_id: str, range_name: str, *, limit: int | None = None):
        captured["limit"] = limit
        captured["range"] = range_name
        return stock, full

    monkeypatch.setattr(history_chart_service.quote_service, "get_history", fake_get_history)
    monkeypatch.setattr(
        history_chart_service,
        "visible_daily_candles",
        lambda candles, range_name, market, *, now=None: candles[-22:],
    )

    payload = get_history_chart("US:AAPL", "1M")

    assert payload is not None
    assert captured["limit"] == 90
    assert len(payload.candles) == 22
    assert payload.candles[0].time == full[-22].time
    assert payload.moving_averages is not None
    assert len(payload.moving_averages.sma10) == 22
    assert payload.moving_averages.sma60[0].value is not None
    assert payload.chart_layout is not None
    # SMA values from earlier closes can extend the Y scale beyond visible highs/lows.
    assert payload.chart_layout["minY"] <= min(float(c.low) for c in payload.candles)


def test_daily_range_preserves_calendar_window_not_fixed_session_count(monkeypatch) -> None:
    stock = _stock()
    now = datetime(2026, 8, 2, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    full = _series(90, end=now)
    monkeypatch.setattr(
        history_chart_service.quote_service,
        "get_history",
        lambda stock_id, range_name, *, limit=None: (stock, full),
    )
    monkeypatch.setattr(
        "app.services.moving_average.market_local_time",
        lambda market, current=None: now,
    )

    payload = get_history_chart("US:AAPL", "1M")

    assert payload is not None
    assert len(payload.candles) < 30
    cutoff = (now - timedelta(days=30)).date()
    assert all(datetime.fromisoformat(c.time).date() >= cutoff for c in payload.candles)
    assert payload.moving_averages is not None
    assert payload.moving_averages.sma60[0].value is not None


def test_empty_daily_history_returns_null_moving_averages(monkeypatch) -> None:
    stock = _stock()
    monkeypatch.setattr(
        history_chart_service.quote_service,
        "get_history",
        lambda stock_id, range_name, *, limit=None: (stock, []),
    )

    payload = get_history_chart("US:AAPL", "1M")

    assert payload is not None
    assert payload.candles == []
    assert payload.moving_averages is None
    assert payload.chart_layout is None


def test_tw_forming_daily_candle_is_visible_and_excluded_from_sma(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 7, 13, 11, 0, tzinfo=tz)
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    completed = _series(20, end=datetime(2026, 7, 10, 13, 30, tzinfo=tz))
    forming = candle_record(
        time="2026-07-13T13:30:00+08:00",
        open_price=946,
        high=955,
        low=940,
        close=950,
        volume=1_000_000,
    )
    monkeypatch.setattr(
        history_chart_service.quote_service,
        "get_history",
        lambda stock_id, range_name, *, limit=None: (stock, completed + [forming]),
    )
    monkeypatch.setattr(
        "app.services.moving_average.market_local_time",
        lambda market, current=None: now,
    )

    payload = get_history_chart("TW:2330", "1M")

    assert payload is not None
    assert payload.candles[-1].time == forming.time
    assert payload.moving_averages is not None
    assert payload.moving_averages.sma10[-1].time == forming.time
    assert payload.moving_averages.sma10[-1].value is None
    assert payload.moving_averages.sma10[-2].value is not None


def test_incomplete_today_bar_stays_visible_but_sma_is_null(monkeypatch) -> None:
    stock = _stock()
    prior = _series(89)
    forming = candle_record(
        time="2026-08-02T16:00:00-04:00",
        open_price=190,
        high=192,
        low=189,
        close=191,
        volume=1_100,
    )
    full = prior + [forming]
    monkeypatch.setattr(
        history_chart_service.quote_service,
        "get_history",
        lambda stock_id, range_name, *, limit=None: (stock, full),
    )
    monkeypatch.setattr(
        history_chart_service,
        "completed_daily_candles",
        lambda candles, market, *, now=None: candles[:-1],
    )
    monkeypatch.setattr(
        history_chart_service,
        "visible_daily_candles",
        lambda candles, range_name, market, *, now=None: candles[-30:],
    )

    payload = get_history_chart("US:AAPL", "1M")

    assert payload is not None
    assert payload.candles[-1].time == forming.time
    assert payload.moving_averages is not None
    assert payload.moving_averages.sma10[-1].value is None
    assert payload.moving_averages.sma60[-2].value is not None
