"""Server-side chart geometry (0–100 SVG space) from OHLC candles — no client-side price math."""

from __future__ import annotations

from app.models.quote import CandleRecord

_Y_TICK_COUNT = 5
_X_TICK_MAX = 5


def _x_axis_tick_indices(length: int, max_ticks: int) -> list[int]:
    if length <= 0:
        return []
    if length == 1:
        return [0]
    ticks = min(max_ticks, length)
    out: set[int] = set()
    for i in range(ticks):
        out.add(round((i / (ticks - 1)) * (length - 1)))
    return sorted(out)


def _y_axis_ticks(lo: float, hi: float, count: int) -> list[float]:
    if hi == lo:
        return [lo] * count
    return [lo + (hi - lo) * (1 - i / (count - 1)) for i in range(count)]


def build_chart_layout(candles: list[CandleRecord]) -> dict[str, object] | None:
    if not candles:
        return None
    lows = [c.low for c in candles]
    highs = [c.high for c in candles]
    lo = min(lows)
    hi = max(highs)
    denom = max(len(candles) - 1, 1)

    points: list[dict[str, float]] = []
    poly_parts: list[str] = []
    for index, candle in enumerate(candles):
        x = (index / denom) * 100.0
        y = 50.0 if hi == lo else 100.0 - ((candle.close - lo) / (hi - lo)) * 100.0
        points.append({"x": x, "y": y})
        poly_parts.append(f"{x},{y}")

    first_close = float(candles[0].close)
    last_close = float(candles[-1].close)

    return {
        "minY": float(lo),
        "maxY": float(hi),
        "pointsPolyline": " ".join(poly_parts),
        "points": points,
        "yAxisTicks": [float(x) for x in _y_axis_ticks(lo, hi, _Y_TICK_COUNT)],
        "xAxisTickIndices": _x_axis_tick_indices(len(candles), _X_TICK_MAX),
        "firstClose": first_close,
        "lastClose": last_close,
        "trendUp": last_close >= first_close,
    }
