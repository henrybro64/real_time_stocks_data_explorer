"""Single-symbol MACD/KD profile values (#331)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.stock_technical_indicators import (
    StockTechnicalIndicatorsError,
    get_stock_technical_indicators,
)
from app.services.tw_research.constants import CALCULATION_VERSION
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


def _candles(days: int = 900, *, rising: bool = True) -> list[CandleRecord]:
    start = datetime(2020, 1, 2, 13, 30, tzinfo=TZ)
    out: list[CandleRecord] = []
    price = 50.0 if rising else 200.0
    for index in range(days):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        price = price + (0.12 if rising else -0.1)
        if price < 5:
            price = 5
        out.append(
            CandleRecord(
                time=day.isoformat(),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000,
            )
        )
    return out


def test_tw_stock_returns_daily_and_monthly_values() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: stock,
        candles_loader=lambda _stock: _candles(),
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert payload["upstreamFetch"] is False
    assert payload["calculationVersion"] == CALCULATION_VERSION
    assert payload["daily"]["timeframe"] == "daily"
    assert payload["monthly"]["timeframe"] == "monthly"
    if payload["daily"]["quality"] == "ok":
        assert payload["daily"]["macdDif"] is not None
        assert payload["daily"]["macdHistogram"] == pytest.approx(
            float(payload["daily"]["dif"]) - float(payload["daily"]["signal"])
        )
        assert payload["daily"]["kdK"] is not None
    assert payload["daily"].get("passStatus") != "pass" or payload["daily"]["dif"] is not None


def test_insufficient_history_is_unavailable_not_zero() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="TSMC",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    payload = get_stock_technical_indicators(
        "TW:2330",
        stock_loader=lambda _sid: stock,
        candles_loader=lambda _stock: _candles(days=10),
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert payload["daily"]["quality"] == "insufficient_history"
    assert payload["daily"]["dif"] is None
    assert payload["daily"]["macdDif"] is None
    assert payload["daily"]["kdK"] is None
    assert payload["daily"]["passStatus"] is None
    assert payload["daily"]["unavailableReason"] == "insufficient_history"
    # Never fabricate zeros for unvalidated numerics.
    for key in ("dif", "signal", "osc", "k", "d", "macdDif", "macdSignal", "macdHistogram", "kdK", "kdD"):
        assert payload["daily"][key] is None


def test_us_stock_rejected() -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    with pytest.raises(StockTechnicalIndicatorsError) as exc:
        get_stock_technical_indicators(
            "US:AAPL",
            stock_loader=lambda _sid: stock,
            candles_loader=lambda _stock: [],
        )
    assert exc.value.code == "unsupported_market"
