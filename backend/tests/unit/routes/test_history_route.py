from fastapi import HTTPException

from app.api.routes import history as history_routes
from tests.support import candle_record, stock_record


def test_history_route_returns_envelope_for_available_candles(monkeypatch) -> None:
    stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="GlobalWafers",
        name_zh_tw="環球晶",
    )
    candles = [
        candle_record(
            time="2026-07-01T13:30:00+08:00",
            open_price=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1_000_000,
        )
    ]
    monkeypatch.setattr(
        history_routes.quote_service,
        "get_history",
        lambda stock_id, range_name, **_kwargs: (stock, candles),
    )

    response = history_routes.get_history("TW:6488", range_name="1W")

    assert response.stock.id == "TW:6488"
    assert response.stock.exchange == "TPEx"
    assert response.range == "1W"
    assert len(response.candles) == 1
    assert response.chartLayout is not None
    assert response.movingAverages is None


def test_history_route_returns_aligned_moving_averages_for_daily_ranges(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple",
        name_zh_tw=None,
    )
    candles = [
        candle_record(
            time=f"2026-{(index // 28) + 1:02d}-{(index % 28) + 1:02d}T15:00:00-05:00",
            open_price=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=1_000,
        )
        for index in range(90)
    ]
    captured: dict[str, object] = {}

    def fake_history(stock_id: str, range_name: str, **kwargs):
        captured["limit"] = kwargs.get("limit")
        return stock, candles

    monkeypatch.setattr(history_routes.quote_service, "get_history", fake_history)

    response = history_routes.get_history("US:AAPL", range_name="1M")

    assert captured["limit"] == 90
    assert response.movingAverages is not None
    assert len(response.candles) == 30
    assert len(response.movingAverages.sma10) == 30
    assert len(response.movingAverages.sma20) == 30
    assert len(response.movingAverages.sma60) == 30
    assert response.movingAverages.sma10[0].time == response.candles[0].time
    assert response.movingAverages.sma60[0].value is not None
    assert response.chartLayout is not None
    assert response.chartLayout.minY <= min(candle.low for candle in response.candles)


def test_history_route_maps_provider_runtime_errors_to_502(monkeypatch) -> None:
    def failing_history(stock_id: str, range_name: str, **_kwargs):
        raise RuntimeError(
            f"Taiwan daily history temporarily unavailable for {stock_id} (TPEx)"
        )

    monkeypatch.setattr(history_routes.quote_service, "get_history", failing_history)

    try:
        history_routes.get_history("TW:6488", range_name="1W")
    except HTTPException as exc:
        assert exc.status_code == 502
        assert "temporarily unavailable" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")


def test_history_route_maps_missing_stock_to_404(monkeypatch) -> None:
    monkeypatch.setattr(
        history_routes.quote_service,
        "get_history",
        lambda stock_id, range_name, **_kwargs: None,
    )

    try:
        history_routes.get_history("TW:9999", range_name="1W")
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Stock not found"
    else:
        raise AssertionError("Expected HTTPException")
