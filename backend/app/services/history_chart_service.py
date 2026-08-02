"""Orchestrate stock history responses with optional SMA overlays."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.services.chart_layout import build_chart_layout
from app.services.moving_average import (
    MovingAverageSeriesRecord,
    align_moving_averages_to_candles,
    build_moving_averages,
    collect_sma_values,
    completed_daily_candles,
    history_fetch_limit,
    supports_daily_moving_averages,
    trim_moving_averages,
    visible_daily_limit,
)
from app.services.quote_service import quote_service


@dataclass(frozen=True)
class HistoryChartPayload:
    stock: StockRecord
    candles: list[CandleRecord]
    chart_layout: dict[str, object] | None
    moving_averages: MovingAverageSeriesRecord | None


def get_history_chart(stock_id: str, range_name: str) -> HistoryChartPayload | None:
    if not supports_daily_moving_averages(range_name):
        data = quote_service.get_history(stock_id, range_name)
        if data is None:
            return None
        stock, candles = data
        return HistoryChartPayload(
            stock=stock,
            candles=candles,
            chart_layout=build_chart_layout(candles),
            moving_averages=None,
        )

    fetch_limit = history_fetch_limit(range_name)
    visible_limit = visible_daily_limit(range_name)
    data = quote_service.get_history(stock_id, range_name, limit=fetch_limit)
    if data is None:
        return None
    stock, full_candles = data
    if not full_candles:
        return HistoryChartPayload(
            stock=stock,
            candles=[],
            chart_layout=None,
            moving_averages=None,
        )

    completed = completed_daily_candles(full_candles, stock.market)
    completed_averages = build_moving_averages(completed)
    full_averages = align_moving_averages_to_candles(completed_averages, full_candles)
    visible_candles = full_candles[-visible_limit:]
    visible_averages = trim_moving_averages(full_averages, visible_count=len(visible_candles))
    layout = build_chart_layout(
        visible_candles,
        extra_values=collect_sma_values(visible_averages),
    )
    return HistoryChartPayload(
        stock=stock,
        candles=visible_candles,
        chart_layout=layout,
        moving_averages=visible_averages,
    )
