from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.services.moving_average import (
    SMA_WARMUP_CANDLES,
    align_moving_averages_to_candles,
    build_moving_averages,
    completed_daily_candles,
    compute_sma_series,
    history_fetch_limit,
    is_daily_candle_complete,
    supports_daily_moving_averages,
    trim_moving_averages,
    visible_daily_limit,
)


def _candles(closes: list[float]) -> list[CandleRecord]:
    return [
        CandleRecord(
            time=f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}T13:30:00+08:00",
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1_000,
        )
        for index, close in enumerate(closes)
    ]


def test_supports_daily_ranges_only() -> None:
    assert supports_daily_moving_averages("1M")
    assert supports_daily_moving_averages("1Y")
    assert not supports_daily_moving_averages("1D")
    assert not supports_daily_moving_averages("1W")
    assert history_fetch_limit("1M") == visible_daily_limit("1M") + SMA_WARMUP_CANDLES
    assert history_fetch_limit("1D") is None


def test_sma10_exact_calculation() -> None:
    closes = [float(index) for index in range(1, 12)]
    points = compute_sma_series(_candles(closes), 10)

    assert points[8].value is None
    assert points[9].value == 5.5
    assert points[10].value == 6.5


def test_sma20_exact_calculation() -> None:
    closes = [10.0] * 19 + [30.0]
    points = compute_sma_series(_candles(closes), 20)

    assert points[18].value is None
    assert points[19].value == 11.0  # (10*19 + 30) / 20


def test_sma60_exact_calculation() -> None:
    closes = [10.0] * 20 + [20.0] * 40
    points = compute_sma_series(_candles(closes), 60)

    assert points[58].value is None
    assert points[59].value == round((10.0 * 20 + 20.0 * 40) / 60, 2)


def test_null_when_insufficient_history() -> None:
    points = compute_sma_series(_candles([1.0, 2.0, 3.0]), 10)
    assert all(point.value is None and point.direction is None for point in points)


def test_directions_up_down_flat() -> None:
    rising = [float(index) for index in range(1, 21)]
    flat_tail = [20.0] * 10
    falling = [20.0 - index for index in range(1, 21)]
    points = compute_sma_series(_candles(rising + flat_tail + falling), 10)

    assert points[9].direction is None
    assert points[10].direction == "up"
    assert any(point.direction == "flat" for point in points)
    assert any(point.direction == "down" for point in points)


def test_warmup_produces_first_visible_sma_and_is_trimmed_out() -> None:
    closes = [100.0 + index * 0.5 for index in range(90)]
    full = build_moving_averages(_candles(closes))
    visible = trim_moving_averages(full, visible_count=30)

    assert len(visible.sma60) == 30
    assert visible.sma60[0].value is not None
    assert visible.sma60[0].time == full.sma60[-30].time
    assert visible.sma10[0].time != full.sma10[0].time


def test_missing_sessions_are_just_absent_trading_days() -> None:
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0]
    points = compute_sma_series(_candles(closes), 10)
    assert points[-1].value == 15.5
    assert len(points) == 11


def test_direction_uses_unrounded_values_when_rounded_values_collide() -> None:
    # Previous window mean = 10.001 → rounds to 10.0
    # Current window mean = 10.003 → rounds to 10.0
    # Direction must still be "up" from the unrounded comparison.
    closes = [10.0] * 9 + [10.01, 10.02]
    points = compute_sma_series(_candles(closes), 10)

    assert points[-2].value == 10.0
    assert points[-1].value == 10.0
    assert points[-1].direction == "up"


def test_incomplete_same_day_daily_bar_is_excluded_from_sma_inputs() -> None:
    tz = ZoneInfo("America/New_York")
    prior = CandleRecord(
        time="2026-07-15T16:00:00-04:00",
        open=100,
        high=101,
        low=99,
        close=100.5,
        volume=1_000,
    )
    forming = CandleRecord(
        time="2026-07-16T16:00:00-04:00",
        open=100.5,
        high=102,
        low=100,
        close=101.25,
        volume=900,
    )
    during_session = datetime(2026, 7, 16, 11, 0, tzinfo=tz)
    after_close = datetime(2026, 7, 16, 16, 5, tzinfo=tz)

    assert is_daily_candle_complete(prior, "US", now=during_session)
    assert not is_daily_candle_complete(forming, "US", now=during_session)
    assert is_daily_candle_complete(forming, "US", now=after_close)

    completed = completed_daily_candles([prior, forming], "US", now=during_session)
    assert completed == [prior]

    averages = align_moving_averages_to_candles(
        build_moving_averages(completed),
        [prior, forming],
    )
    assert averages.sma10[-1].time == forming.time
    assert averages.sma10[-1].value is None
