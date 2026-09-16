"""Compact technical-analysis facts for AI stock analysis (1Y daily window).

Values reuse the chart's moving-average helpers so the narrative and the chart
cannot disagree: same completed-candle filter, same SMA math, same direction
rule, and the same unadjusted close basis the chart plots.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import logging

import pandas as pd

from app.models.quote import CandleRecord
from app.services.moving_average import (
    SmaDirection,
    completed_daily_candles,
    compute_sma_series,
    is_daily_candle_complete,
)
from app.services.stock_analysis_key_price_levels import (
    build_key_price_levels,
    unavailable_key_price_levels,
)
from app.services.stock_analysis_metrics import (
    calculate_average_volume,
    calculate_price_vs_average_percent,
    calculate_return_percent,
    calculate_rsi,
    clean_numeric_series,
)

Availability = Literal["ok", "insufficient", "unavailable"]
MaAlignment = Literal["bullish", "bearish", "mixed", "insufficient"]

_ANALYSIS_PERIOD = "1Y"
_RECENT_EXTREME_WINDOW = 20
_VOLUME_AVERAGE_WINDOW = 30
# Chart candles are unadjusted; keep the AI on the same basis so quoted closes
# and moving averages match what the user sees.
_PRICE_COLUMN_PREFERENCE = ("Close", "Adj Close")

logger = logging.getLogger(__name__)


def build_technical_analysis(
    history: pd.DataFrame,
    *,
    market: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Build a compact ``technicalAnalysis`` payload from daily OHLC history.

    Uses completed daily candles only (same incomplete-session exclusion as the
    chart). Caller supplies roughly one year of daily bars.
    """
    base: dict[str, object] = {"period": _ANALYSIS_PERIOD}
    currency = "TWD" if market.upper() == "TW" else "USD"
    if history is None or not isinstance(history, pd.DataFrame) or history.empty:
        return _null_payload(base, availability="unavailable", currency=currency)

    candles, price_basis = _history_to_candles(history)
    if not candles:
        return _null_payload(base, availability="unavailable", currency=currency)

    completed = completed_daily_candles(candles, market, now=now)
    if not completed:
        return _null_payload(base, availability="unavailable", currency=currency)

    closes = pd.Series([float(candle.close) for candle in completed], dtype="float64")
    volumes = pd.Series([float(candle.volume) for candle in completed], dtype="float64")

    latest_close = round(float(closes.iloc[-1]), 2)
    data_as_of = _candle_iso_date(completed[-1])

    sma10, sma10_direction = _latest_sma(completed, 10)
    sma20, sma20_direction = _latest_sma(completed, 20)
    sma60, sma60_direction = _latest_sma(completed, 60)

    recent_high_20d = _recent_extreme(completed, kind="high")
    recent_low_20d = _recent_extreme(completed, kind="low")

    try:
        key_price_levels = build_key_price_levels(
            completed,
            currency=currency,
            data_as_of=data_as_of,
            reference_price=float(closes.iloc[-1]),
        )
    except Exception:  # noqa: BLE001 — soft-fail; do not break technicalAnalysis
        logger.exception(
            "key_price_levels soft-fail market=%s data_as_of=%s",
            market,
            data_as_of,
        )
        key_price_levels = unavailable_key_price_levels(
            currency=currency,
            data_as_of=data_as_of,
            reference_price=latest_close,
        )

    return {
        **base,
        "dataAsOf": data_as_of,
        "source": "application_computed_market_data",
        "priceBasis": price_basis,
        "completedSessions": len(completed),
        "latestClose": latest_close,
        "sma10": sma10,
        "sma20": sma20,
        "sma60": sma60,
        "sma10Direction": sma10_direction,
        "sma20Direction": sma20_direction,
        "sma60Direction": sma60_direction,
        "priceVsSma10Percent": calculate_price_vs_average_percent(latest_close, sma10),
        "priceVsSma20Percent": calculate_price_vs_average_percent(latest_close, sma20),
        "priceVsSma60Percent": calculate_price_vs_average_percent(latest_close, sma60),
        "movingAverageAlignment": _moving_average_alignment(sma10, sma20, sma60),
        "return5dPercent": calculate_return_percent(closes, 5),
        "return20dPercent": calculate_return_percent(closes, 20),
        "recentHigh20d": recent_high_20d,
        "recentLow20d": recent_low_20d,
        "distanceFromRecentHighPercent": calculate_price_vs_average_percent(
            latest_close,
            recent_high_20d,
        ),
        "distanceFromRecentLowPercent": calculate_price_vs_average_percent(
            latest_close,
            recent_low_20d,
        ),
        "rsi14": calculate_rsi(closes, period=14),
        "volumeVsAverage30dPercent": _volume_vs_average_percent(volumes),
        "keyPriceLevels": key_price_levels,
        "availability": _availability(sma60, sma60_direction),
    }


def completed_session_history(
    history: pd.DataFrame,
    *,
    market: str,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Drop a still-forming same-day row from ``history``.

    Every computed metric in the analysis payload runs on the result so the
    nested technical block and the top-level legacy metrics can never describe
    different latest sessions.
    """
    if history is None or not isinstance(history, pd.DataFrame) or history.empty:
        return history

    # Positional mask, not label lookup: a duplicated index label would
    # otherwise duplicate rows in the result.
    keep = []
    for index_value in history.index:
        timestamp = _index_to_iso(index_value)
        if timestamp is None:
            keep.append(False)
            continue
        probe = CandleRecord(time=timestamp, open=0.0, high=0.0, low=0.0, close=0.0, volume=0)
        keep.append(is_daily_candle_complete(probe, market, now=now))
    if all(keep):
        return history
    return history.iloc[[position for position, kept in enumerate(keep) if kept]]


def _availability(sma60: float | None, sma60_direction: SmaDirection | None) -> Availability:
    """``ok`` only when the longest average has both a value and a direction."""
    if sma60 is not None and sma60_direction is not None:
        return "ok"
    return "insufficient"


def _null_payload(
    base: dict[str, object],
    *,
    availability: Availability,
    currency: str = "USD",
) -> dict[str, object]:
    return {
        **base,
        "dataAsOf": None,
        "source": "application_computed_market_data",
        "priceBasis": None,
        "completedSessions": 0,
        "latestClose": None,
        "sma10": None,
        "sma20": None,
        "sma60": None,
        "sma10Direction": None,
        "sma20Direction": None,
        "sma60Direction": None,
        "priceVsSma10Percent": None,
        "priceVsSma20Percent": None,
        "priceVsSma60Percent": None,
        "movingAverageAlignment": "insufficient",
        "return5dPercent": None,
        "return20dPercent": None,
        "recentHigh20d": None,
        "recentLow20d": None,
        "distanceFromRecentHighPercent": None,
        "distanceFromRecentLowPercent": None,
        "rsi14": None,
        "volumeVsAverage30dPercent": None,
        "keyPriceLevels": unavailable_key_price_levels(currency=currency),
        "availability": availability,
    }


def _latest_sma(
    candles: list[CandleRecord],
    window: int,
) -> tuple[float | None, SmaDirection | None]:
    """Latest SMA value and direction from the chart's own series helper."""
    if len(candles) < window:
        return None, None
    points = compute_sma_series(candles, window)
    if not points:
        return None, None
    latest = points[-1]
    return latest.value, latest.direction


def _select_price_column(history: pd.DataFrame) -> tuple[pd.Series, str] | None:
    for column_name in _PRICE_COLUMN_PREFERENCE:
        if column_name in history:
            series = clean_numeric_series(history[column_name])
            if not series.empty:
                return series, column_name
    return None


def _history_to_candles(history: pd.DataFrame) -> tuple[list[CandleRecord], str | None]:
    selected = _select_price_column(history)
    if selected is None:
        return [], None
    price_series, price_basis = selected

    highs, lows, opens = _ohlc_on_price_basis(history, price_series, price_basis)
    volumes = clean_numeric_series(history["Volume"]) if "Volume" in history else None

    candles: list[CandleRecord] = []
    for index_value, close in price_series.items():
        timestamp = _index_to_iso(index_value)
        if timestamp is None:
            continue
        close_value = float(close)
        candles.append(
            CandleRecord(
                time=timestamp,
                open=_value_at(opens, index_value, close_value),
                high=_value_at(highs, index_value, close_value),
                low=_value_at(lows, index_value, close_value),
                close=close_value,
                volume=int(round(_value_at(volumes, index_value, 0.0))),
            )
        )
    return candles, price_basis


def _ohlc_on_price_basis(
    history: pd.DataFrame,
    price_series: pd.Series,
    price_basis: str,
) -> tuple[pd.Series | None, pd.Series | None, pd.Series | None]:
    """Keep High/Low/Open on the same scale as the selected close series.

    Unadjusted High/Low around a split cannot be mixed with Adj Close.
    """
    highs = clean_numeric_series(history["High"]) if "High" in history else None
    lows = clean_numeric_series(history["Low"]) if "Low" in history else None
    opens = clean_numeric_series(history["Open"]) if "Open" in history else None
    if price_basis != "Adj Close":
        return highs, lows, opens

    raw_close = clean_numeric_series(history["Close"]) if "Close" in history else None
    if raw_close is None or raw_close.empty:
        # No unadjusted close to form a ratio; do not mix raw High/Low with Adj Close.
        return None, None, None

    def _scale(series: pd.Series | None) -> pd.Series | None:
        if series is None or series.empty:
            return None
        aligned = pd.concat(
            [series.rename("value"), raw_close.rename("raw"), price_series.rename("adj")],
            axis=1,
        ).dropna()
        if aligned.empty:
            return None
        ratio = aligned["adj"] / aligned["raw"]
        ratio = ratio.replace([float("inf"), float("-inf")], pd.NA).dropna()
        ratio = ratio[ratio != 0]
        if ratio.empty:
            return None
        scaled = aligned["value"] * ratio
        return clean_numeric_series(scaled)

    return _scale(highs), _scale(lows), _scale(opens)


def _value_at(series: pd.Series | None, index_value: object, fallback: float) -> float:
    if series is None or index_value not in series.index:
        return fallback
    value = series.loc[index_value]
    if isinstance(value, pd.Series):
        value = value.iloc[-1]
    if pd.isna(value):
        return fallback
    return float(value)


def _index_to_iso(index_value: object) -> str | None:
    """ISO timestamp for a history row, or ``None`` when the index isn't dated."""
    if isinstance(index_value, pd.Timestamp):
        return index_value.to_pydatetime().isoformat()
    if isinstance(index_value, datetime):
        return index_value.isoformat()
    text = str(index_value).strip()
    if not text:
        return None
    try:
        return pd.Timestamp(text).to_pydatetime().isoformat()
    except (TypeError, ValueError):
        return None


def _candle_iso_date(candle: CandleRecord) -> str:
    candle_dt = datetime.fromisoformat(candle.time)
    return candle_dt.date().isoformat()


def _moving_average_alignment(
    sma10: float | None,
    sma20: float | None,
    sma60: float | None,
) -> MaAlignment:
    if sma10 is None or sma20 is None or sma60 is None:
        return "insufficient"
    if sma10 > sma20 > sma60:
        return "bullish"
    if sma10 < sma20 < sma60:
        return "bearish"
    return "mixed"


def _recent_extreme(
    candles: list[CandleRecord],
    *,
    kind: Literal["high", "low"],
) -> float | None:
    """Recent 20-session extreme using intraday highs/lows, like the chart."""
    if len(candles) < _RECENT_EXTREME_WINDOW:
        return None
    trailing = candles[-_RECENT_EXTREME_WINDOW:]
    if kind == "high":
        value = max(float(candle.high) for candle in trailing)
    else:
        value = min(float(candle.low) for candle in trailing)
    return round(value, 2)


def _volume_vs_average_percent(volumes: pd.Series) -> float | None:
    cleaned = clean_numeric_series(volumes)
    if cleaned.empty:
        return None
    average = calculate_average_volume(cleaned, window=_VOLUME_AVERAGE_WINDOW)
    if average is None or average == 0:
        return None
    latest = float(cleaned.iloc[-1])
    return round(((latest / float(average)) - 1.0) * 100.0, 2)
