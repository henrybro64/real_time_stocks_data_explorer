"""Simple moving-average series for stock price charts.

Calculations use completed trading-day closes only. Warm-up candles must be
supplied by the caller; this module does not fetch market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models.quote import CandleRecord
from app.services.market_clock import MARKET_TIMEZONES, MARKET_WINDOWS, market_local_time

SmaDirection = Literal["up", "down", "flat"]

SMA_PERIODS = (10, 20, 60)
SMA_WARMUP_CANDLES = 60

VISIBLE_DAILY_LIMITS: dict[str, int] = {
    "1M": 30,
    "3M": 65,
    "6M": 130,
    "1Y": 260,
}

DAILY_SMA_RANGES = frozenset(VISIBLE_DAILY_LIMITS)


@dataclass(frozen=True)
class SmaPointRecord:
    time: str
    value: float | None
    direction: SmaDirection | None


@dataclass(frozen=True)
class MovingAverageSeriesRecord:
    sma10: tuple[SmaPointRecord, ...]
    sma20: tuple[SmaPointRecord, ...]
    sma60: tuple[SmaPointRecord, ...]


def supports_daily_moving_averages(range_name: str) -> bool:
    return range_name in DAILY_SMA_RANGES


def visible_daily_limit(range_name: str) -> int:
    return VISIBLE_DAILY_LIMITS.get(range_name, 30)


def history_fetch_limit(range_name: str) -> int | None:
    """Return provider candle limit including SMA warm-up, or None for intraday."""
    if not supports_daily_moving_averages(range_name):
        return None
    return visible_daily_limit(range_name) + SMA_WARMUP_CANDLES


def is_daily_candle_complete(
    candle: CandleRecord,
    market: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether ``candle`` is a finished daily session for ``market``."""
    timezone = MARKET_TIMEZONES.get(market)
    window = MARKET_WINDOWS.get(market)
    if timezone is None or window is None:
        return True

    current = market_local_time(market, now)
    candle_dt = datetime.fromisoformat(candle.time)
    if candle_dt.tzinfo is None:
        candle_dt = candle_dt.replace(tzinfo=timezone)
    candle_local = candle_dt.astimezone(timezone)
    if candle_local.date() < current.date():
        return True
    if candle_local.date() > current.date():
        return False
    _, close = window
    current_time = current.time().replace(second=0, microsecond=0)
    return current_time >= close


def completed_daily_candles(
    candles: list[CandleRecord],
    market: str,
    *,
    now: datetime | None = None,
) -> list[CandleRecord]:
    """Drop any still-forming same-day daily bar from SMA inputs."""
    return [candle for candle in candles if is_daily_candle_complete(candle, market, now=now)]


def compute_sma_series(candles: list[CandleRecord], window: int) -> list[SmaPointRecord]:
    if window <= 0:
        raise ValueError("SMA window must be positive")

    closes = [float(candle.close) for candle in candles]
    points: list[SmaPointRecord] = []

    for index, candle in enumerate(candles):
        if index + 1 < window:
            points.append(SmaPointRecord(time=candle.time, value=None, direction=None))
            continue

        current = sum(closes[index + 1 - window : index + 1]) / window
        direction: SmaDirection | None = None
        if index + 1 > window:
            previous = sum(closes[index - window : index]) / window
            if current > previous:
                direction = "up"
            elif current < previous:
                direction = "down"
            else:
                direction = "flat"

        points.append(
            SmaPointRecord(
                time=candle.time,
                value=round(current, 2),
                direction=direction,
            )
        )

    return points


def build_moving_averages(candles: list[CandleRecord]) -> MovingAverageSeriesRecord:
    return MovingAverageSeriesRecord(
        sma10=tuple(compute_sma_series(candles, 10)),
        sma20=tuple(compute_sma_series(candles, 20)),
        sma60=tuple(compute_sma_series(candles, 60)),
    )


def align_moving_averages_to_candles(
    series: MovingAverageSeriesRecord,
    candles: list[CandleRecord],
) -> MovingAverageSeriesRecord:
    """Re-align SMA points to ``candles`` by time; missing times become null."""
    return MovingAverageSeriesRecord(
        sma10=tuple(_aligned_points(series.sma10, candles)),
        sma20=tuple(_aligned_points(series.sma20, candles)),
        sma60=tuple(_aligned_points(series.sma60, candles)),
    )


def _aligned_points(
    points: tuple[SmaPointRecord, ...],
    candles: list[CandleRecord],
) -> list[SmaPointRecord]:
    by_time = {point.time: point for point in points}
    aligned: list[SmaPointRecord] = []
    for candle in candles:
        existing = by_time.get(candle.time)
        if existing is None:
            aligned.append(SmaPointRecord(time=candle.time, value=None, direction=None))
        else:
            aligned.append(existing)
    return aligned


def trim_moving_averages(
    series: MovingAverageSeriesRecord,
    *,
    visible_count: int,
) -> MovingAverageSeriesRecord:
    if visible_count <= 0:
        return MovingAverageSeriesRecord(sma10=(), sma20=(), sma60=())
    return MovingAverageSeriesRecord(
        sma10=series.sma10[-visible_count:],
        sma20=series.sma20[-visible_count:],
        sma60=series.sma60[-visible_count:],
    )


def collect_sma_values(*series: MovingAverageSeriesRecord | None) -> list[float]:
    values: list[float] = []
    for item in series:
        if item is None:
            continue
        for points in (item.sma10, item.sma20, item.sma60):
            for point in points:
                if point.value is not None:
                    values.append(float(point.value))
    return values
