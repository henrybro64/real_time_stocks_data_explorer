from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def clean_numeric_series(values: pd.Series | None) -> pd.Series:
    if values is None:
        return pd.Series(dtype="float64")
    cleaned = pd.to_numeric(values, errors="coerce").dropna()
    if cleaned.empty:
        return pd.Series(dtype="float64")
    return cleaned.astype("float64")


def select_price_series(history: pd.DataFrame) -> pd.Series:
    for column_name in ("Adj Close", "Close"):
        if column_name in history:
            series = clean_numeric_series(history[column_name])
            if not series.empty:
                return series
    return pd.Series(dtype="float64")


def calculate_return_percent(prices: pd.Series, periods: int) -> float | None:
    cleaned = clean_numeric_series(prices)
    if cleaned.size < 2:
        return None
    offset = min(periods, cleaned.size - 1)
    if offset <= 0:
        return None
    base_price = float(cleaned.iloc[-(offset + 1)])
    current_price = float(cleaned.iloc[-1])
    if base_price == 0:
        return None
    return round(((current_price / base_price) - 1.0) * 100.0, 2)


def calculate_simple_moving_average(prices: pd.Series, window: int) -> float | None:
    cleaned = clean_numeric_series(prices)
    if cleaned.size < window:
        return None
    return round(float(cleaned.tail(window).mean()), 2)


def calculate_price_vs_average_percent(
    current_price: float | None,
    moving_average: float | None,
) -> float | None:
    if current_price is None or moving_average in {None, 0}:
        return None
    return round(((current_price / moving_average) - 1.0) * 100.0, 2)


def calculate_rsi(prices: pd.Series, period: int = 14) -> float | None:
    cleaned = clean_numeric_series(prices)
    if cleaned.size <= period:
        return None
    delta = cleaned.diff().dropna()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    latest_gain = average_gain.iloc[-1]
    latest_loss = average_loss.iloc[-1]
    if pd.isna(latest_gain) or pd.isna(latest_loss):
        return None
    if latest_loss == 0:
        return 100.0 if latest_gain > 0 else 50.0
    relative_strength = latest_gain / latest_loss
    return round(100.0 - (100.0 / (1.0 + relative_strength)), 2)


def calculate_annualized_volatility(
    prices: pd.Series,
    window: int = 30,
) -> float | None:
    cleaned = clean_numeric_series(prices)
    daily_returns = cleaned.pct_change().dropna()
    if daily_returns.size < window:
        return None
    trailing_window = daily_returns.tail(window)
    return round(float(trailing_window.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100.0), 2)


def calculate_max_drawdown(prices: pd.Series) -> float | None:
    cleaned = clean_numeric_series(prices)
    if cleaned.size < 2:
        return None
    running_peak = cleaned.cummax()
    drawdown = (cleaned / running_peak) - 1.0
    return round(float(drawdown.min() * 100.0), 2)


def calculate_average_volume(volumes: pd.Series, window: int = 30) -> int | None:
    cleaned = clean_numeric_series(volumes)
    if cleaned.size < window:
        return None
    return int(round(float(cleaned.tail(window).mean())))
