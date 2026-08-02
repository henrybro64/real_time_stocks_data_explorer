from __future__ import annotations

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


def _series(count: int, start: float = 100.0) -> list[CandleRecord]:
    return [
        candle_record(
            time=f"2026-01-{(index % 28) + 1:02d}T16:00:00-05:00",
            open_price=start + index,
            high=start + index + 1,
            low=start + index - 1,
            close=start + index,
            volume=1_000 + index,
        )
        for index in range(count)
    ]


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

    payload = get_history_chart("US:AAPL", "1M")

    assert payload is not None
    assert captured["limit"] == 90
    assert len(payload.candles) == 30
    assert payload.candles[0].time == full[-30].time
    assert payload.moving_averages is not None
    assert len(payload.moving_averages.sma10) == 30
    assert payload.moving_averages.sma60[0].value is not None
    assert payload.chart_layout is not None
    # SMA values from earlier closes can extend the Y scale beyond visible highs/lows.
    assert payload.chart_layout["minY"] <= min(float(c.low) for c in payload.candles)


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


def test_incomplete_today_bar_stays_visible_but_sma_is_null(monkeypatch) -> None:
    stock = _stock()
    prior = _series(89)
    forming = candle_record(
        time="2026-08-01T16:00:00-04:00",
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

    payload = get_history_chart("US:AAPL", "1M")

    assert payload is not None
    assert payload.candles[-1].time == forming.time
    assert payload.moving_averages is not None
    assert payload.moving_averages.sma10[-1].value is None
    assert payload.moving_averages.sma60[-2].value is not None
