"""Route coverage for Profile MACD/KD (#331)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import history as history_routes
from app.services.stock_technical_indicators import StockTechnicalIndicatorsError
from tests.support import stock_record


def test_technical_indicators_route_returns_payload(monkeypatch) -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    def _fake_get(stock_id: str, **_kwargs):  # noqa: ANN001
        assert stock_id == "TW:2330"
        return {
            "stockId": stock.id,
            "market": "TW",
            "upstreamFetch": False,
            "daily": {"quality": "insufficient_history", "macdDif": None},
            "monthly": {"quality": "insufficient_history", "macdDif": None},
        }

    monkeypatch.setattr(
        "app.services.stock_technical_indicators.get_stock_technical_indicators",
        _fake_get,
    )
    payload = history_routes.get_technical_indicators("TW:2330")
    assert payload["upstreamFetch"] is False
    assert payload["daily"]["macdDif"] is None


def test_technical_indicators_route_maps_not_found(monkeypatch) -> None:
    def _raise(stock_id: str, **_kwargs):  # noqa: ANN001
        del stock_id
        raise StockTechnicalIndicatorsError("not_found", "stock_not_in_catalog")

    monkeypatch.setattr(
        "app.services.stock_technical_indicators.get_stock_technical_indicators",
        _raise,
    )
    with pytest.raises(HTTPException) as exc:
        history_routes.get_technical_indicators("TW:9999")
    assert exc.value.status_code == 404


def test_technical_indicators_route_maps_unsupported_market(monkeypatch) -> None:
    def _raise(stock_id: str, **_kwargs):  # noqa: ANN001
        del stock_id
        raise StockTechnicalIndicatorsError(
            "unsupported_market",
            "macd_kd_profile_values_are_taiwan_only",
        )

    monkeypatch.setattr(
        "app.services.stock_technical_indicators.get_stock_technical_indicators",
        _raise,
    )
    with pytest.raises(HTTPException) as exc:
        history_routes.get_technical_indicators("US:AAPL")
    assert exc.value.status_code == 400
