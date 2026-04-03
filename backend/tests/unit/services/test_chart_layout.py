from app.models.quote import CandleRecord
from app.services.chart_layout import build_chart_layout


def test_build_chart_layout_returns_none_for_empty_input() -> None:
    assert build_chart_layout([]) is None


def test_build_chart_layout_handles_flat_series() -> None:
    candles = [
        CandleRecord(
            time="2026-04-01T09:00:00+08:00",
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1000,
        ),
        CandleRecord(
            time="2026-04-01T10:00:00+08:00",
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1200,
        ),
    ]

    layout = build_chart_layout(candles)

    assert layout is not None
    assert layout["minY"] == 100.0
    assert layout["maxY"] == 100.0
    assert layout["pointsPolyline"] == "0.0,50.0 100.0,50.0"
    assert layout["points"] == [{"x": 0.0, "y": 50.0}, {"x": 100.0, "y": 50.0}]
    assert layout["yAxisTicks"] == [100.0, 100.0, 100.0, 100.0, 100.0]
    assert layout["xAxisTickIndices"] == [0, 1]
    assert layout["trendUp"] is True


def test_build_chart_layout_tracks_range_and_trend_direction() -> None:
    candles = [
        CandleRecord(
            time="2026-04-01T09:00:00+08:00",
            open=120.0,
            high=121.0,
            low=119.0,
            close=120.0,
            volume=1000,
        ),
        CandleRecord(
            time="2026-04-01T10:00:00+08:00",
            open=115.0,
            high=116.0,
            low=114.0,
            close=115.0,
            volume=1000,
        ),
        CandleRecord(
            time="2026-04-01T11:00:00+08:00",
            open=110.0,
            high=111.0,
            low=109.0,
            close=110.0,
            volume=1000,
        ),
    ]

    layout = build_chart_layout(candles)

    assert layout is not None
    assert layout["minY"] == 110.0
    assert layout["maxY"] == 120.0
    assert layout["firstClose"] == 120.0
    assert layout["lastClose"] == 110.0
    assert layout["trendUp"] is False
    assert layout["xAxisTickIndices"] == [0, 1, 2]
    assert layout["points"] == [
        {"x": 0.0, "y": 0.0},
        {"x": 50.0, "y": 50.0},
        {"x": 100.0, "y": 100.0},
    ]
