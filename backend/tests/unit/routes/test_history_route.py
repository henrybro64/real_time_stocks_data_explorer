from fastapi import HTTPException

from app.api.routes import tw_institutional as history_route
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
    payload = history_route._tw_institutional_schema(_sample_institutional_result()).model_dump()

    assert payload == {
        "stockId": "TW:2330",
        "symbol": "2330",
        "market": "TWSE",
        "latestDate": "2026-07-09",
        "summary": {
            "foreignNetLots": 12.345,
            "investmentTrustNetLots": 1.2,
            "dealerNetLots": -0.6,
            "totalNetLots": 12.945,
            "dealerSelfNetLots": -0.8,
            "dealerHedgeNetLots": 0.2,
        },
        "history": [
            {
                "date": "2026-07-09",
                "foreignNetLots": 12.345,
                "investmentTrustNetLots": 1.2,
                "dealerNetLots": -0.6,
                "totalNetLots": 12.945,
                "dealerSelfNetLots": -0.8,
                "dealerHedgeNetLots": 0.2,
            }
        ],
        "source": "TWSE T86 daily institutional data",
    }


def test_institutional_route_delegates_to_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(stock_id: str, *, days: int):
        captured["stock_id"] = stock_id
        captured["days"] = days
        return _sample_institutional_result()

    monkeypatch.setattr(
        history_route.tw_institutional_service,
        "get_institutional_investors",
        fake_get,
    )

    response = history_route.get_tw_institutional_investors("TW:2330", days=30)

    assert captured == {"stock_id": "TW:2330", "days": 30}
    assert response.latestDate == "2026-07-09"
    assert response.summary is not None
    assert response.summary.totalNetLots == 12.945


def test_institutional_route_maps_service_errors(monkeypatch) -> None:
    def raise_not_found(*args, **kwargs):
        raise ValueError("Stock not found")

    monkeypatch.setattr(
        history_route.tw_institutional_service,
        "get_institutional_investors",
        raise_not_found,
    )
    try:
        history_route.get_tw_institutional_investors("TW:9999", days=30)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Stock not found"
    else:
        raise AssertionError("Expected HTTPException")
