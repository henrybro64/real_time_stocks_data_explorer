from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.models.quote import CandleRecord
from app.services.moving_average import completed_daily_candles, compute_sma_series
from app.services.stock_analysis_technical import build_technical_analysis


def _candles_from_history(history: pd.DataFrame) -> list[CandleRecord]:
    return [
        CandleRecord(
            time=timestamp.to_pydatetime().isoformat(),
            open=float(row["Close"]),
            high=float(row["Close"]),
            low=float(row["Close"]),
            close=float(row["Close"]),
            volume=int(row["Volume"]),
        )
        for timestamp, row in history.iterrows()
    ]


def _daily_history(
    closes: list[float],
    *,
    volumes: list[int] | None = None,
    end: datetime | None = None,
    tz: ZoneInfo | None = None,
) -> pd.DataFrame:
    timezone = tz or ZoneInfo("America/New_York")
    anchor = end or datetime(2026, 8, 3, 16, 0, tzinfo=timezone)
    dates: list[datetime] = []
    day_offset = 0
    while len(dates) < len(closes):
        day = anchor - timedelta(days=day_offset)
        day_offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day.replace(hour=16, minute=0, second=0, microsecond=0))
    dates.reverse()

    volume_values = volumes if volumes is not None else [1_000] * len(closes)
    frame = pd.DataFrame(
        {
            "Close": closes,
            "Volume": volume_values,
        },
        index=pd.DatetimeIndex(dates),
    )
    return frame


def test_unavailable_when_history_empty() -> None:
    payload = build_technical_analysis(pd.DataFrame(), market="US")

    assert payload["period"] == "1Y"
    assert payload["availability"] == "unavailable"
    assert payload["latestClose"] is None
    assert payload["movingAverageAlignment"] == "insufficient"


def test_insufficient_when_history_shorter_than_sma60() -> None:
    history = _daily_history([100.0 + index for index in range(25)])
    payload = build_technical_analysis(history, market="US", now=history.index[-1].to_pydatetime())

    assert payload["availability"] == "insufficient"
    assert payload["latestClose"] == 124.0
    assert payload["sma10"] is not None
    assert payload["sma20"] is not None
    assert payload["sma60"] is None
    assert payload["movingAverageAlignment"] == "insufficient"


def test_bullish_alignment_and_up_directions() -> None:
    # Strong uptrend so SMA10 > SMA20 > SMA60 and directions are up.
    closes = [100.0 + index * 0.8 for index in range(90)]
    history = _daily_history(closes)
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    assert payload["availability"] == "ok"
    assert payload["movingAverageAlignment"] == "bullish"
    assert payload["sma10"] > payload["sma20"] > payload["sma60"]
    assert payload["sma10Direction"] == "up"
    assert payload["sma20Direction"] == "up"
    assert payload["sma60Direction"] == "up"
    assert payload["return5dPercent"] is not None
    assert payload["return5dPercent"] > 0
    assert payload["rsi14"] is not None


def test_bearish_alignment_and_down_directions() -> None:
    closes = [200.0 - index * 0.8 for index in range(90)]
    history = _daily_history(closes)
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    assert payload["availability"] == "ok"
    assert payload["movingAverageAlignment"] == "bearish"
    assert payload["sma10"] < payload["sma20"] < payload["sma60"]
    assert payload["sma10Direction"] == "down"
    assert payload["sma20Direction"] == "down"
    assert payload["sma60Direction"] == "down"


def test_mixed_alignment_when_averages_disagree() -> None:
    # Construct closes so SMA10 > SMA20 but SMA20 < SMA60 (not strictly ordered).
    closes = [100.0] * 50 + [80.0] * 20 + [95.0] * 20
    history = _daily_history(closes)
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    assert payload["availability"] == "ok"
    assert payload["sma10"] is not None
    assert payload["sma20"] is not None
    assert payload["sma60"] is not None
    assert payload["movingAverageAlignment"] == "mixed"
    assert not (payload["sma10"] > payload["sma20"] > payload["sma60"])
    assert not (payload["sma10"] < payload["sma20"] < payload["sma60"])


def test_flat_sma_direction_when_change_below_threshold() -> None:
    # Nearly constant closes → SMA change over 5 days is well under 0.1%.
    closes = [100.0] * 40 + [100.01] * 30
    history = _daily_history(closes)
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    assert payload["sma10Direction"] == "flat"
    assert payload["sma20Direction"] == "flat"


def test_incomplete_current_session_candle_is_excluded() -> None:
    tz = ZoneInfo("America/New_York")
    completed_closes = [100.0 + index for index in range(70)]
    history = _daily_history(completed_closes, end=datetime(2026, 8, 3, 16, 0, tzinfo=tz), tz=tz)
    # Append an in-progress same-day bar that should be dropped during the session.
    forming_time = datetime(2026, 8, 4, 16, 0, tzinfo=tz)
    forming = pd.DataFrame(
        {"Close": [999.0], "Volume": [9_999]},
        index=pd.DatetimeIndex([forming_time]),
    )
    history = pd.concat([history, forming])
    during_session = datetime(2026, 8, 4, 11, 0, tzinfo=tz)

    payload = build_technical_analysis(history, market="US", now=during_session)

    assert payload["latestClose"] == 169.0
    assert payload["dataAsOf"] == "2026-08-03"
    assert payload["latestClose"] != 999.0


def test_recent_high_low_and_volume_vs_average() -> None:
    # Mild ramp then a 20d window that includes an obvious high/low.
    closes = [90.0 + index * 0.1 for index in range(60)] + [110.0, 120.0, 115.0, 105.0, 108.0]
    volumes = [1_000] * 64 + [2_000]
    history = _daily_history(closes, volumes=volumes)
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    assert payload["recentHigh20d"] == 120.0
    assert payload["recentLow20d"] == pytest.approx(min(closes[-20:]))
    assert payload["distanceFromRecentHighPercent"] == pytest.approx(
        ((108.0 / 120.0) - 1.0) * 100.0,
        abs=0.01,
    )
    assert payload["volumeVsAverage30dPercent"] is not None
    assert payload["volumeVsAverage30dPercent"] > 0


def test_prefers_unadjusted_close_so_values_match_the_chart() -> None:
    tz = ZoneInfo("America/New_York")
    closes = [100.0 + index * 0.5 for index in range(70)]
    history = _daily_history(closes, end=datetime(2026, 8, 3, 16, 0, tzinfo=tz), tz=tz)
    history["Adj Close"] = [50.0] * 70
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    # The chart plots unadjusted closes, so the narrative must quote the same basis.
    assert payload["priceBasis"] == "Close"
    assert payload["latestClose"] == round(100.0 + 69 * 0.5, 2)


def test_falls_back_to_adj_close_when_close_is_absent() -> None:
    tz = ZoneInfo("America/New_York")
    closes = [100.0 + index * 0.5 for index in range(70)]
    history = _daily_history(closes, end=datetime(2026, 8, 3, 16, 0, tzinfo=tz), tz=tz)
    history["Adj Close"] = history["Close"]
    history = history.drop(columns=["Close"])
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    assert payload["priceBasis"] == "Adj Close"
    assert payload["latestClose"] == round(100.0 + 69 * 0.5, 2)


def test_does_not_mix_unadjusted_highs_with_adj_close() -> None:
    tz = ZoneInfo("America/New_York")
    closes = [50.0] * 70
    history = _daily_history(closes, end=datetime(2026, 8, 3, 16, 0, tzinfo=tz), tz=tz)
    history["Adj Close"] = history["Close"]
    history["High"] = 400.0
    history["Low"] = 1.0
    history = history.drop(columns=["Close"])
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)

    # No unadjusted Close to form a split ratio, so extrema stay on Adj Close.
    assert payload["priceBasis"] == "Adj Close"
    assert payload["recentHigh20d"] == 50.0
    assert payload["recentLow20d"] == 50.0


def test_sma_values_and_directions_match_the_chart_helper() -> None:
    tz = ZoneInfo("America/New_York")
    closes = [100.0 + index * 0.8 for index in range(90)]
    history = _daily_history(closes, end=datetime(2026, 8, 3, 16, 0, tzinfo=tz), tz=tz)
    now = history.index[-1].to_pydatetime() + timedelta(hours=1)

    payload = build_technical_analysis(history, market="US", now=now)
    candles = completed_daily_candles(
        _candles_from_history(history),
        "US",
        now=now,
    )

    for window, value_key, direction_key in (
        (10, "sma10", "sma10Direction"),
        (20, "sma20", "sma20Direction"),
        (60, "sma60", "sma60Direction"),
    ):
        expected = compute_sma_series(candles, window)[-1]
        assert payload[value_key] == expected.value
        assert payload[direction_key] == expected.direction
