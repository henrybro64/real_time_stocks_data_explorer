"""Deterministic daily/monthly MACD and KD for Taiwan research (#311).

Conventions (documented for reference matching):
- Price basis: unadjusted completed-session closes (same filter as chart SMAs).
- EMA seed: SMA of the first ``period`` values, then recursive EMA.
- MACD: DIF = EMA12 − EMA26; Signal = EMA9(DIF); OSC = DIF − Signal
  (not scaled by 2).
- KD(9,3,3): RSV = 100 * (C − L9) / (H9 − L9); when H9 == L9, RSV = 50.
  K and D use Wilder-style 1/3 smoothing with seed 50.
- Monthly bars: calendar-month OHLCV from completed daily candles; the current
  calendar month is never included.
- Missing/partial/insufficient history → status ``unknown`` / ``insufficient_history``.
- Pass rule (day or month independently): DIF > 0 AND OSC > 0 AND K > 50 AND D > 50.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.models.quote import CandleRecord
from app.services.moving_average import completed_daily_candles
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    KD_D_SMOOTH,
    KD_K_SMOOTH,
    KD_PERIOD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    MIN_DAILY_BARS_FOR_MACD_KD,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
    PRICE_BASIS,
)

PassStatus = Literal["pass", "fail", "unknown"]
QualityStatus = Literal["ok", "insufficient_history", "unavailable"]


@dataclass(frozen=True)
class MacdKdSnapshot:
    as_of: str | None
    dif: float | None
    signal: float | None
    osc: float | None
    k: float | None
    d: float | None
    pass_status: PassStatus
    quality: QualityStatus
    price_basis: str
    calculation_version: str
    bar_count: int
    timeframe: Literal["daily", "monthly"]


@dataclass(frozen=True)
class IndicatorSeriesPoint:
    as_of: str
    dif: float
    signal: float
    osc: float
    k: float
    d: float


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA with SMA seed at index ``period - 1``; earlier values are None."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / float(period)
    out[period - 1] = seed
    multiplier = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = (values[index] - previous) * multiplier + previous
        out[index] = previous
    return out


def compute_macd_series(
    closes: list[float],
    *,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> list[tuple[float, float, float] | None]:
    """Return per-bar (DIF, Signal, OSC) or None until Signal is defined."""
    if slow < fast:
        raise ValueError("slow period must be >= fast period")
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    difs: list[float | None] = []
    for index, close in enumerate(closes):
        del close
        fast_value = fast_ema[index]
        slow_value = slow_ema[index]
        if fast_value is None or slow_value is None:
            difs.append(None)
        else:
            difs.append(fast_value - slow_value)

    # Signal EMA over the DIF series, skipping leading Nones by only seeding
    # once ``slow`` bars exist (first non-None DIF index is slow-1).
    signal_values: list[float | None] = [None] * len(closes)
    first_dif_index = slow - 1
    if len(closes) >= slow + signal - 1:
        window = [float(difs[index]) for index in range(first_dif_index, first_dif_index + signal)]
        seed = sum(window) / float(signal)
        signal_index = first_dif_index + signal - 1
        signal_values[signal_index] = seed
        multiplier = 2.0 / (signal + 1.0)
        previous = seed
        for index in range(signal_index + 1, len(closes)):
            dif_value = difs[index]
            assert dif_value is not None
            previous = (dif_value - previous) * multiplier + previous
            signal_values[index] = previous

    out: list[tuple[float, float, float] | None] = []
    for index in range(len(closes)):
        dif_value = difs[index]
        signal_value = signal_values[index]
        if dif_value is None or signal_value is None:
            out.append(None)
        else:
            out.append((dif_value, signal_value, dif_value - signal_value))
    return out


def compute_kd_series(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    period: int = KD_PERIOD,
    k_smooth: int = KD_K_SMOOTH,
    d_smooth: int = KD_D_SMOOTH,
) -> list[tuple[float, float] | None]:
    """Return per-bar (K, D) after the first RSV exists; None before that."""
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must be the same length")
    if period <= 0 or k_smooth <= 0 or d_smooth <= 0:
        raise ValueError("KD periods must be positive")

    k_alpha = 1.0 / float(k_smooth)
    d_alpha = 1.0 / float(d_smooth)
    out: list[tuple[float, float] | None] = [None] * len(closes)
    previous_k = 50.0
    previous_d = 50.0
    for index in range(len(closes)):
        if index + 1 < period:
            continue
        window_high = max(highs[index + 1 - period : index + 1])
        window_low = min(lows[index + 1 - period : index + 1])
        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = 100.0 * (closes[index] - window_low) / (window_high - window_low)
        k_value = (1.0 - k_alpha) * previous_k + k_alpha * rsv
        d_value = (1.0 - d_alpha) * previous_d + d_alpha * k_value
        previous_k = k_value
        previous_d = d_value
        out[index] = (k_value, d_value)
    return out


def evaluate_pass(
    *,
    dif: float | None,
    osc: float | None,
    k: float | None,
    d: float | None,
    quality: QualityStatus,
) -> PassStatus:
    if quality != "ok" or None in (dif, osc, k, d):
        return "unknown"
    assert dif is not None and osc is not None and k is not None and d is not None
    if dif > 0 and osc > 0 and k > 50 and d > 50:
        return "pass"
    return "fail"


def assemble_monthly_candles(
    daily: list[CandleRecord],
    *,
    now: datetime | None = None,
) -> list[CandleRecord]:
    """Build fully closed calendar-month OHLCV bars from completed daily candles."""
    if not daily:
        return []
    current = (now or datetime.now()).date()
    current_month = (current.year, current.month)

    buckets: dict[tuple[int, int], list[CandleRecord]] = {}
    for candle in daily:
        candle_date = _candle_date(candle)
        key = (candle_date.year, candle_date.month)
        if key == current_month:
            continue
        buckets.setdefault(key, []).append(candle)

    months: list[CandleRecord] = []
    for key in sorted(buckets):
        rows = buckets[key]
        months.append(
            CandleRecord(
                time=_month_end_iso(rows[-1]),
                open=float(rows[0].open),
                high=max(float(row.high) for row in rows),
                low=min(float(row.low) for row in rows),
                close=float(rows[-1].close),
                volume=int(sum(int(row.volume) for row in rows)),
            )
        )
    return months


def snapshot_from_candles(
    candles: list[CandleRecord],
    *,
    timeframe: Literal["daily", "monthly"],
    min_bars: int,
) -> MacdKdSnapshot:
    if not candles:
        return _empty_snapshot(timeframe=timeframe, quality="unavailable", bar_count=0)
    if len(candles) < min_bars:
        return _empty_snapshot(
            timeframe=timeframe,
            quality="insufficient_history",
            bar_count=len(candles),
            as_of=_candle_date(candles[-1]).isoformat(),
        )

    closes = [float(candle.close) for candle in candles]
    highs = [float(candle.high) for candle in candles]
    lows = [float(candle.low) for candle in candles]
    macd = compute_macd_series(closes)
    kd = compute_kd_series(highs, lows, closes)
    latest_macd = macd[-1]
    latest_kd = kd[-1]
    if latest_macd is None or latest_kd is None:
        return _empty_snapshot(
            timeframe=timeframe,
            quality="insufficient_history",
            bar_count=len(candles),
            as_of=_candle_date(candles[-1]).isoformat(),
        )

    dif, signal, osc = latest_macd
    k_value, d_value = latest_kd
    quality: QualityStatus = "ok"
    pass_status = evaluate_pass(dif=dif, osc=osc, k=k_value, d=d_value, quality=quality)
    return MacdKdSnapshot(
        as_of=_candle_date(candles[-1]).isoformat(),
        dif=dif,
        signal=signal,
        osc=osc,
        k=k_value,
        d=d_value,
        pass_status=pass_status,
        quality=quality,
        price_basis=PRICE_BASIS,
        calculation_version=CALCULATION_VERSION,
        bar_count=len(candles),
        timeframe=timeframe,
    )


def daily_macd_kd_snapshot(
    candles: list[CandleRecord],
    *,
    market: str = "TW",
    now: datetime | None = None,
) -> MacdKdSnapshot:
    completed = completed_daily_candles(candles, market, now=now)
    return snapshot_from_candles(
        completed,
        timeframe="daily",
        min_bars=MIN_DAILY_BARS_FOR_MACD_KD,
    )


def monthly_macd_kd_snapshot(
    daily_candles: list[CandleRecord],
    *,
    market: str = "TW",
    now: datetime | None = None,
) -> MacdKdSnapshot:
    completed = completed_daily_candles(daily_candles, market, now=now)
    monthly = assemble_monthly_candles(completed, now=now)
    # Align with coverage: drop a mid-month history-slice boundary month.
    from app.services.tw_research.coverage import trim_truncated_leading_months

    monthly = trim_truncated_leading_months(
        completed,
        monthly,
        market=market,
        allow_holiday_fetch=False,
    )
    return snapshot_from_candles(
        monthly,
        timeframe="monthly",
        min_bars=MIN_MONTHLY_BARS_FOR_MACD_KD,
    )


def latest_sma(candles: list[CandleRecord], window: int) -> float | None:
    if len(candles) < window or window <= 0:
        return None
    closes = [float(candle.close) for candle in candles[-window:]]
    return sum(closes) / float(window)


def fifty_two_week_position(candles: list[CandleRecord]) -> float | None:
    """0..1 position of latest close in the trailing ~252-session range."""
    if len(candles) < 2:
        return None
    window = candles[-252:] if len(candles) >= 252 else candles
    high = max(float(candle.high) for candle in window)
    low = min(float(candle.low) for candle in window)
    close = float(window[-1].close)
    if high == low:
        return 0.5
    return (close - low) / (high - low)


def _empty_snapshot(
    *,
    timeframe: Literal["daily", "monthly"],
    quality: QualityStatus,
    bar_count: int,
    as_of: str | None = None,
) -> MacdKdSnapshot:
    return MacdKdSnapshot(
        as_of=as_of,
        dif=None,
        signal=None,
        osc=None,
        k=None,
        d=None,
        pass_status="unknown",
        quality=quality,
        price_basis=PRICE_BASIS,
        calculation_version=CALCULATION_VERSION,
        bar_count=bar_count,
        timeframe=timeframe,
    )


def _candle_date(candle: CandleRecord) -> date:
    return datetime.fromisoformat(candle.time).date()


def _month_end_iso(candle: CandleRecord) -> str:
    return datetime.fromisoformat(candle.time).isoformat()
