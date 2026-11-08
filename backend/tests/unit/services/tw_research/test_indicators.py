"""MACD/KD reference cases for #311."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.tw_research.constants import (
    KD_VALUE_TOLERANCE,
    MACD_VALUE_TOLERANCE,
    MIN_DAILY_BARS_FOR_MACD_KD,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
)
from app.services.tw_research.indicators import (
    assemble_monthly_candles,
    compute_kd_series,
    compute_macd_series,
    daily_macd_kd_snapshot,
    ema_series,
    evaluate_pass,
    monthly_macd_kd_snapshot,
)


TZ = ZoneInfo("Asia/Taipei")


def _daily_candles(closes: list[float], *, end: datetime | None = None) -> list[CandleRecord]:
    anchor = end or datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
    dates: list[datetime] = []
    offset = 0
    while len(dates) < len(closes):
        day = anchor - timedelta(days=offset)
        offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day.replace(hour=13, minute=30, second=0, microsecond=0))
    dates.reverse()
    candles: list[CandleRecord] = []
    for index, close in enumerate(closes):
        candles.append(
            CandleRecord(
                time=dates[index].isoformat(),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000 + index,
            )
        )
    return candles


def test_ema_sma_seed_and_recursion() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    series = ema_series(values, 3)
    assert series[0] is None and series[1] is None
    assert series[2] == pytest.approx(2.0, abs=MACD_VALUE_TOLERANCE)
    # multiplier = 2/(3+1)=0.5 → next = (4-2)*0.5 + 2 = 3
    assert series[3] == pytest.approx(3.0, abs=MACD_VALUE_TOLERANCE)
    assert series[4] == pytest.approx(4.0, abs=MACD_VALUE_TOLERANCE)
    assert series[5] == pytest.approx(5.0, abs=MACD_VALUE_TOLERANCE)


def test_macd_reference_hand_computed_tail() -> None:
    # Strictly rising series so DIF/Signal/OSC are positive once warm.
    closes = [float(100 + index) for index in range(60)]
    macd = compute_macd_series(closes)
    assert macd[24] is None  # Signal not ready until index 25+9-1 = 33
    assert macd[33] is not None
    dif, signal, osc = macd[-1]  # type: ignore[misc]
    assert dif > 0
    assert signal > 0
    assert abs(osc - (dif - signal)) <= MACD_VALUE_TOLERANCE


def test_kd_zero_range_uses_rsv_50_and_seeds() -> None:
    highs = [10.0] * 12
    lows = [10.0] * 12
    closes = [10.0] * 12
    kd = compute_kd_series(highs, lows, closes)
    assert kd[8] is not None
    k_value, d_value = kd[8]  # type: ignore[misc]
    # RSV=50 with seed 50 → K stays 50, D stays 50
    assert abs(k_value - 50.0) <= KD_VALUE_TOLERANCE
    assert abs(d_value - 50.0) <= KD_VALUE_TOLERANCE


def test_kd_known_smoothing_step() -> None:
    # Flat then one up-close so RSV becomes 100 on the last bar of the window.
    highs = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 12.0]
    lows = [8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0]
    closes = [9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0, 12.0]
    kd = compute_kd_series(highs, lows, closes)
    assert kd[8] is not None
    k_value, d_value = kd[8]  # type: ignore[misc]
    # RSV=100; K = 50*2/3 + 100/3 = 66.666...; D = 50*2/3 + K/3
    expected_k = (2.0 / 3.0) * 50.0 + (1.0 / 3.0) * 100.0
    expected_d = (2.0 / 3.0) * 50.0 + (1.0 / 3.0) * expected_k
    assert abs(k_value - expected_k) <= KD_VALUE_TOLERANCE
    assert abs(d_value - expected_d) <= KD_VALUE_TOLERANCE


def test_daily_insufficient_history_is_unknown() -> None:
    candles = _daily_candles([100.0 + i for i in range(MIN_DAILY_BARS_FOR_MACD_KD - 1)])
    snap = daily_macd_kd_snapshot(candles, now=datetime.fromisoformat(candles[-1].time))
    assert snap.quality == "insufficient_history"
    assert snap.pass_status == "unknown"


def test_monthly_excludes_current_calendar_month_and_needs_warm_up() -> None:
    # Spanning many months but still fewer than MIN monthly bars after assembly.
    closes = [100.0 + i for i in range(80)]
    candles = _daily_candles(closes, end=datetime(2024, 6, 28, 13, 30, tzinfo=TZ))
    monthly = assemble_monthly_candles(
        candles,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert all(datetime.fromisoformat(c.time).month != 6 or datetime.fromisoformat(c.time).year != 2024 for c in monthly)
    snap = monthly_macd_kd_snapshot(
        candles,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert snap.bar_count < MIN_MONTHLY_BARS_FOR_MACD_KD or snap.quality in {
        "insufficient_history",
        "ok",
    }
    if snap.bar_count < MIN_MONTHLY_BARS_FOR_MACD_KD:
        assert snap.pass_status == "unknown"


def test_evaluate_pass_requires_all_four_conditions() -> None:
    assert evaluate_pass(dif=1.0, osc=0.5, k=60.0, d=55.0, quality="ok") == "pass"
    assert evaluate_pass(dif=-0.1, osc=0.5, k=60.0, d=55.0, quality="ok") == "fail"
    assert evaluate_pass(dif=1.0, osc=0.5, k=60.0, d=55.0, quality="insufficient_history") == "unknown"


def test_holiday_gap_still_aligns_on_available_sessions() -> None:
    # Build weekdays then drop a mid-week session (holiday) without shifting labels.
    base = _daily_candles([100.0 + i for i in range(50)])
    dropped = base[:10] + base[11:]
    snap = daily_macd_kd_snapshot(dropped, now=datetime.fromisoformat(dropped[-1].time))
    assert snap.as_of == datetime.fromisoformat(dropped[-1].time).date().isoformat()
    assert snap.quality in {"ok", "insufficient_history"}


def test_duplicate_same_day_candles_do_not_crash() -> None:
    candles = _daily_candles([100.0 + i for i in range(40)])
    dup = candles + [
        CandleRecord(
            time=candles[-1].time,
            open=candles[-1].open,
            high=candles[-1].high,
            low=candles[-1].low,
            close=candles[-1].close,
            volume=candles[-1].volume,
        )
    ]
    snap = daily_macd_kd_snapshot(dup, now=datetime.fromisoformat(candles[-1].time) + timedelta(days=1))
    assert snap.pass_status in {"pass", "fail", "unknown"}


def test_newly_listed_short_history_stock_is_unknown_not_rejected() -> None:
    # Outside the starter pilot list; only a few sessions exist (IPO / thin history).
    candles = _daily_candles([120.0, 121.0, 119.5, 122.0, 123.0])
    snap = daily_macd_kd_snapshot(
        candles,
        now=datetime.fromisoformat(candles[-1].time) + timedelta(days=1),
    )
    assert snap.quality == "insufficient_history"
    assert snap.pass_status == "unknown"
    assert snap.bar_count < MIN_DAILY_BARS_FOR_MACD_KD
