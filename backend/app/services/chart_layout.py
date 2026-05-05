"""Server-side chart geometry (0–100 SVG space) for price/value series."""

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


def build_value_chart_layout(values: list[float]) -> dict[str, object] | None:
    if not values:
        return None
    lo = min(values)
    hi = max(values)
    denom = max(len(values) - 1, 1)

    points: list[dict[str, float]] = []
    poly_parts: list[str] = []
    for index, value in enumerate(values):
        x = (index / denom) * 100.0
        y = 50.0 if hi == lo else 100.0 - ((value - lo) / (hi - lo)) * 100.0
        points.append({"x": x, "y": y})
        poly_parts.append(f"{x},{y}")

    first_value = float(values[0])
    last_value = float(values[-1])

    return {
        "minY": float(lo),
        "maxY": float(hi),
        "pointsPolyline": " ".join(poly_parts),
        "points": points,
        "yAxisTicks": [float(x) for x in _y_axis_ticks(lo, hi, _Y_TICK_COUNT)],
        "xAxisTickIndices": _x_axis_tick_indices(len(values), _X_TICK_MAX),
        "firstClose": first_value,
        "lastClose": last_value,
        "trendUp": last_value >= first_value,
    }


def build_chart_layout(candles: list[CandleRecord]) -> dict[str, object] | None:
    return build_value_chart_layout([float(candle.close) for candle in candles])
