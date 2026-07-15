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
        lambda stock_id, range_name: (stock, candles),
    )

    response = history_routes.get_history("TW:6488", range_name="1W")

    assert response.stock.id == "TW:6488"
    assert response.stock.exchange == "TPEx"
    assert response.range == "1W"
    assert len(response.candles) == 1
    assert response.chartLayout is not None


def test_history_route_maps_provider_runtime_errors_to_502(monkeypatch) -> None:
    def failing_history(stock_id: str, range_name: str):
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
        lambda stock_id, range_name: None,
    )

    try:
        history_routes.get_history("TW:9999", range_name="1W")
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Stock not found"
    else:
        raise AssertionError("Expected HTTPException")
