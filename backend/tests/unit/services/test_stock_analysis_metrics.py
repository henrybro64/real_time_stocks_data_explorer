import pandas as pd

from app.services.stock_analysis_metrics import (
    calculate_annualized_volatility,
    calculate_max_drawdown,
    calculate_return_percent,
    calculate_rsi,
    calculate_simple_moving_average,
)


def test_calculate_return_percent_uses_trailing_window() -> None:
    prices = pd.Series([100.0, 103.0, 106.09])

    result = calculate_return_percent(prices, periods=2)

    assert result == 6.09


def test_calculate_rsi_handles_persistent_uptrend() -> None:
    prices = pd.Series([float(value) for value in range(1, 21)])

    result = calculate_rsi(prices, period=14)

    assert result == 100.0


def test_calculate_simple_moving_average_returns_latest_window_average() -> None:
    prices = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0])

    result = calculate_simple_moving_average(prices, window=3)

    assert result == 16.0


def test_calculate_annualized_volatility_is_zero_for_constant_daily_returns() -> None:
    prices = pd.Series([100.0 * (1.01**index) for index in range(31)])

    result = calculate_annualized_volatility(prices, window=30)

    assert result == 0.0


def test_calculate_max_drawdown_returns_peak_to_trough_percentage() -> None:
    prices = pd.Series([100.0, 120.0, 90.0, 95.0])

    result = calculate_max_drawdown(prices)

    assert result == -25.0
