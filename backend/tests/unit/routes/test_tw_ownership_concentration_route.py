from fastapi import HTTPException
import pytest

from app.api.routes import tw_ownership_concentration as ownership_route
from app.services.tw_ownership_concentration_service import (
    OwnershipComponentState,
    OwnershipDataStatus,
    TaiwanOwnershipConcentrationPoint,
    TaiwanOwnershipConcentrationResult,
)


def _sample_result(*, stale: bool = False) -> TaiwanOwnershipConcentrationResult:
    point = TaiwanOwnershipConcentrationPoint(
        date="2026-09-11",
        holders_400_lots_or_more_percent=63.46,
        foreign_ownership_percent=18.21,
        investment_trust_ownership_percent=None,
        dealer_ownership_percent=None,
        director_ownership_percent=None,
        weekly_close=88.1,
    )
    return TaiwanOwnershipConcentrationResult(
        stock_id="TW:2330",
        range="1Y",
        as_of="2026-09-11",
        source="tdcc_portal+twse_mi_qfiis+quote_history",
        stale=stale,
        currency="TWD",
        data_status=OwnershipDataStatus(
            tdcc=OwnershipComponentState(status="available"),
            foreign_ownership=OwnershipComponentState(status="available"),
            weekly_close=OwnershipComponentState(status="available"),
        ),
        points=(point,),
    )


def test_tw_ownership_schema_camel_case() -> None:
    payload = ownership_route._tw_ownership_concentration_schema(_sample_result()).model_dump()
    assert payload["stockId"] == "TW:2330"
    assert payload["range"] == "1Y"
    assert payload["asOf"] == "2026-09-11"
    assert payload["source"] == "tdcc_portal+twse_mi_qfiis+quote_history"
    assert payload["stale"] is False
    assert payload["currency"] == "TWD"
    assert payload["dataStatus"]["tdcc"]["status"] == "available"
    assert payload["dataStatus"]["foreignOwnership"]["status"] == "available"
    assert payload["dataStatus"]["weeklyClose"]["status"] == "available"
    assert payload["points"][0]["holders400LotsOrMorePercent"] == pytest.approx(63.46)
    assert payload["points"][0]["foreignOwnershipPercent"] == pytest.approx(18.21)
    assert payload["points"][0]["investmentTrustOwnershipPercent"] is None
    assert payload["points"][0]["weeklyClose"] == pytest.approx(88.1)


def test_tw_ownership_schema_preserves_component_reasons() -> None:
    result = _sample_result()
    result = TaiwanOwnershipConcentrationResult(
        stock_id=result.stock_id,
        range=result.range,
        as_of=result.as_of,
        source="tdcc_portal",
        stale=False,
        currency="TWD",
        data_status=OwnershipDataStatus(
            tdcc=OwnershipComponentState(status="available"),
            foreign_ownership=OwnershipComponentState(
                status="temporarily_unavailable",
                reason="upstream_timeout",
            ),
            weekly_close=OwnershipComponentState(
                status="temporarily_unavailable",
                reason="history_unavailable",
            ),
        ),
        points=result.points,
    )
    payload = ownership_route._tw_ownership_concentration_schema(result).model_dump()
    assert payload["source"] == "tdcc_portal"
    assert payload["dataStatus"]["foreignOwnership"] == {
        "status": "temporarily_unavailable",
        "reason": "upstream_timeout",
    }
    assert payload["dataStatus"]["weeklyClose"]["reason"] == "history_unavailable"


def test_tw_ownership_route_success(monkeypatch) -> None:
    monkeypatch.setattr(
        ownership_route.tw_ownership_concentration_service,
        "get_ownership_concentration",
        lambda stock_id, range_name="1Y": _sample_result(),
    )
    response = ownership_route.get_tw_ownership_concentration("TW:2330", range="1Y")
    assert response.stockId == "TW:2330"
    assert response.points[0].holders400LotsOrMorePercent == pytest.approx(63.46)
    assert response.dataStatus.tdcc.status == "available"
    assert response.currency == "TWD"


def test_tw_ownership_route_maps_non_tw_to_404(monkeypatch) -> None:
    def raise_non_tw(stock_id, range_name="1Y"):
        raise ValueError("Taiwan ownership concentration is only available for TW stocks")

    monkeypatch.setattr(
        ownership_route.tw_ownership_concentration_service,
        "get_ownership_concentration",
        raise_non_tw,
    )
    with pytest.raises(HTTPException) as exc_info:
        ownership_route.get_tw_ownership_concentration("US:AAPL", range="1Y")
    assert exc_info.value.status_code == 404


def test_tw_ownership_route_maps_upstream_to_502(monkeypatch) -> None:
    def raise_upstream(stock_id, range_name="1Y"):
        raise RuntimeError("Taiwan ownership concentration upstream temporarily unavailable")

    monkeypatch.setattr(
        ownership_route.tw_ownership_concentration_service,
        "get_ownership_concentration",
        raise_upstream,
    )
    with pytest.raises(HTTPException) as exc_info:
        ownership_route.get_tw_ownership_concentration("TW:2330", range="1Y")
    assert exc_info.value.status_code == 502
