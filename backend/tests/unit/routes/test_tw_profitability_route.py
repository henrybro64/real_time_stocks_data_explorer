from fastapi import HTTPException
import pytest

from app.api.routes import tw_profitability as profitability_route
from app.services.tw_profitability_service import (
    TaiwanProfitabilityPoint,
    TaiwanProfitabilityResult,
)


def _sample_result() -> TaiwanProfitabilityResult:
    point = TaiwanProfitabilityPoint(
        fiscal_quarter="2025Q3",
        period_end="2025-09-30",
        gross_margin_percent=54.12,
        operating_margin_percent=42.3,
        pretax_margin_percent=43.1,
        net_margin_percent=38.2,
        quarter_end_close=None,
    )
    return TaiwanProfitabilityResult(
        stock_id="TW:2330",
        quarters=12,
        as_of="2025-09-30",
        source="yahoo_finance_quarterly_income",
        stale=False,
        points=(point,),
    )


def test_tw_profitability_schema_camel_case() -> None:
    payload = profitability_route._tw_profitability_schema(_sample_result()).model_dump()
    assert payload["stockId"] == "TW:2330"
    assert payload["quarters"] == 12
    assert payload["asOf"] == "2025-09-30"
    assert payload["source"] == "yahoo_finance_quarterly_income"
    assert payload["points"][0]["fiscalQuarter"] == "2025Q3"
    assert payload["points"][0]["grossMarginPercent"] == pytest.approx(54.12)
    assert payload["points"][0]["quarterEndClose"] is None


def test_tw_profitability_route_success(monkeypatch) -> None:
    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        lambda stock_id, quarters=12: _sample_result(),
    )
    response = profitability_route.get_tw_profitability("TW:2330", quarters=12)
    assert response.stockId == "TW:2330"
    assert response.points[0].netMarginPercent == pytest.approx(38.2)


def test_tw_profitability_route_maps_non_tw_to_404(monkeypatch) -> None:
    def raise_non_tw(stock_id, quarters=12):
        raise ValueError("Taiwan profitability margins are only available for TW stocks")

    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        raise_non_tw,
    )
    with pytest.raises(HTTPException) as exc_info:
        profitability_route.get_tw_profitability("US:AAPL", quarters=12)
    assert exc_info.value.status_code == 404


def test_tw_profitability_route_maps_upstream_to_502(monkeypatch) -> None:
    def raise_upstream(stock_id, quarters=12):
        raise RuntimeError("Taiwan profitability margins upstream temporarily unavailable")

    monkeypatch.setattr(
        profitability_route.tw_profitability_service,
        "get_profitability",
        raise_upstream,
    )
    with pytest.raises(HTTPException) as exc_info:
        profitability_route.get_tw_profitability("TW:2330", quarters=12)
    assert exc_info.value.status_code == 502
