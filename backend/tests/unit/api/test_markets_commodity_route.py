from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.api.routes import markets as markets_route
from app.services.commodity_service import CommodityQuoteRecord, CommodityUnavailableError


def test_wti_route_returns_typed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        markets_route,
        "get_wti_crude_oil_quote",
        lambda: CommodityQuoteRecord(
            symbol="CL=F",
            name="WTI Crude Oil",
            price=68.45,
            currency="USD",
            unit="barrel",
            change=-0.74,
            change_percent=-1.07,
            timestamp="2026-08-01T02:05:00Z",
            source="Yahoo Finance",
            is_market_open=False,
            status="ok",
        ),
    )

    payload = markets_route.get_wti_crude_oil()

    assert payload["symbol"] == "CL=F"
    assert payload["currency"] == "USD"
    assert payload["unit"] == "barrel"
    assert payload["changePercent"] == pytest.approx(-1.07)
    assert payload["isMarketOpen"] is False


def test_wti_route_maps_unavailable_to_502(monkeypatch) -> None:
    def boom():
        raise CommodityUnavailableError("down")

    monkeypatch.setattr(markets_route, "get_wti_crude_oil_quote", boom)

    with pytest.raises(HTTPException) as exc_info:
        markets_route.get_wti_crude_oil()

    assert exc_info.value.status_code == 502
