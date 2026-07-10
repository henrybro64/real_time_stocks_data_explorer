from fastapi import HTTPException
import pytest

from app.api.routes import tw_institutional as institutional_route
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorRecord,
    TaiwanInstitutionalInvestorsResult,
)


def _sample_institutional_result() -> TaiwanInstitutionalInvestorsResult:
    record = TaiwanInstitutionalInvestorRecord(
        date="2026-07-09",
        symbol="2330",
        name="台積電",
        foreign_net_shares=12_345,
        investment_trust_net_shares=1_200,
        dealer_self_net_shares=-800,
        dealer_hedge_net_shares=200,
        dealer_net_shares=-600,
        total_net_shares=12_945,
    )
    return TaiwanInstitutionalInvestorsResult(
        stock_id="TW:2330",
        symbol="2330",
        market="TWSE",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TWSE T86 daily institutional data",
    )


def test_tw_institutional_schema_converts_shares_to_lots() -> None:
    payload = institutional_route._tw_institutional_schema(_sample_institutional_result()).model_dump()

    assert payload["stockId"] == "TW:2330"
    assert payload["symbol"] == "2330"
    assert payload["market"] == "TWSE"
    assert payload["latestDate"] == "2026-07-09"
    assert payload["summary"]["foreignNetLots"] == pytest.approx(12.345)
    assert payload["summary"]["investmentTrustNetLots"] == pytest.approx(1.2)
    assert payload["summary"]["dealerNetLots"] == pytest.approx(-0.6)
    assert payload["summary"]["totalNetLots"] == pytest.approx(12.945)
    assert payload["summary"]["dealerSelfNetLots"] == pytest.approx(-0.8)
    assert payload["summary"]["dealerHedgeNetLots"] == pytest.approx(0.2)
    assert payload["history"][0]["date"] == "2026-07-09"
    assert payload["source"] == "TWSE T86 daily institutional data"


def test_tw_institutional_schema_uses_100_unit_lots_for_foreign_currency_etfs() -> None:
    record = TaiwanInstitutionalInvestorRecord(
        date="2026-07-09",
        symbol="00677K",
        name="富邦VIX",
        foreign_net_shares=1_000,
        investment_trust_net_shares=0,
        dealer_self_net_shares=0,
        dealer_hedge_net_shares=0,
        dealer_net_shares=0,
        total_net_shares=1_000,
    )
    result = TaiwanInstitutionalInvestorsResult(
        stock_id="TW:00677K",
        symbol="00677K",
        market="TWSE",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TWSE T86 daily institutional data",
    )

    payload = institutional_route._tw_institutional_schema(result).model_dump()

    assert payload["summary"]["foreignNetLots"] == pytest.approx(10.0)
    assert payload["summary"]["totalNetLots"] == pytest.approx(10.0)


def test_institutional_route_delegates_to_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(stock_id: str, *, days: int):
        captured["stock_id"] = stock_id
        captured["days"] = days
        return _sample_institutional_result()

    monkeypatch.setattr(
        institutional_route.tw_institutional_service,
        "get_institutional_investors",
        fake_get,
    )

    response = institutional_route.get_tw_institutional_investors("TW:2330", days=30)

    assert captured == {"stock_id": "TW:2330", "days": 30}
    assert response.latestDate == "2026-07-09"
    assert response.summary is not None
    assert response.summary.totalNetLots == pytest.approx(12.945)


def test_institutional_route_maps_not_found_errors(monkeypatch) -> None:
    def raise_not_found(*args, **kwargs):
        raise ValueError("Stock not found")

    monkeypatch.setattr(
        institutional_route.tw_institutional_service,
        "get_institutional_investors",
        raise_not_found,
    )
    try:
        institutional_route.get_tw_institutional_investors("TW:9999", days=30)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Stock not found"
    else:
        raise AssertionError("Expected HTTPException")


def test_institutional_route_maps_upstream_errors(monkeypatch) -> None:
    def raise_upstream(*args, **kwargs):
        raise RuntimeError("Taiwan institutional data is temporarily unavailable from official sources.")

    monkeypatch.setattr(
        institutional_route.tw_institutional_service,
        "get_institutional_investors",
        raise_upstream,
    )
    try:
        institutional_route.get_tw_institutional_investors("TW:2330", days=30)
    except HTTPException as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("Expected HTTPException")


def test_institutional_route_maps_unknown_market_as_bad_request(monkeypatch) -> None:
    def raise_unknown(*args, **kwargs):
        raise ValueError("Taiwan institutional market is unavailable for this stock")

    monkeypatch.setattr(
        institutional_route.tw_institutional_service,
        "get_institutional_investors",
        raise_unknown,
    )
    try:
        institutional_route.get_tw_institutional_investors("TW:2330", days=30)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Expected HTTPException")
