from fastapi import HTTPException
import pytest

from app.api.routes import tw_margin_short as margin_short_route
from tests.support import sample_margin_short_result


def test_tw_margin_short_schema_preserves_lot_and_amount_fields() -> None:
    payload = margin_short_route._tw_margin_short_schema(sample_margin_short_result()).model_dump()

    assert payload["stockId"] == "TW:1303"
    assert payload["symbol"] == "1303"
    assert payload["market"] == "TWSE"
    assert payload["latestDate"] == "2026-07-09"
    assert payload["summary"]["marginBalanceLots"] == pytest.approx(31_339.0)
    assert payload["summary"]["shortMarginBalanceLots"] == pytest.approx(0.0)
    assert payload["summary"]["borrowedShortBalanceLots"] == pytest.approx(12_345.0)
    assert payload["summary"]["borrowedShortBalanceChangeLots"] == pytest.approx(141.0)
    assert payload["summary"]["borrowedShortBalanceChangeAmount10k"] == 2609
    assert payload["history"][0]["closePrice"] == pytest.approx(185.0)
    assert payload["sharesPerLot"] == 1000


def test_tw_margin_short_schema_allows_partial_nullable_balances() -> None:
    from app.services.tw_margin_short_service import (
        TaiwanMarginShortRecord,
        TaiwanMarginShortResult,
    )

    record = TaiwanMarginShortRecord(
        date="2026-07-09",
        symbol="6488",
        name="環球晶",
        close_price=None,
        margin_balance_lots=10_200.0,
        short_margin_balance_lots=4.0,
        borrowed_short_balance_lots=None,
        borrowed_short_balance_change_lots=None,
        borrowed_short_balance_change_amount_10k=None,
    )
    result = TaiwanMarginShortResult(
        stock_id="TW:6488",
        symbol="6488",
        market="TPEx",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TPEx official margin and borrowed-short daily data",
    )

    payload = margin_short_route._tw_margin_short_schema(result).model_dump()

    assert payload["summary"]["marginBalanceLots"] == pytest.approx(10_200.0)
    assert payload["summary"]["borrowedShortBalanceLots"] is None
    assert payload["history"][0]["borrowedShortBalanceLots"] is None


def test_tw_margin_short_schema_exposes_100_unit_shares_per_lot_for_fx_etf() -> None:
    from app.services.tw_margin_short_service import (
        TaiwanMarginShortRecord,
        TaiwanMarginShortResult,
    )

    record = TaiwanMarginShortRecord(
        date="2026-07-09",
        symbol="00677K",
        name="富邦VIX",
        close_price=200.0,
        margin_balance_lots=10.0,
        short_margin_balance_lots=2.0,
        borrowed_short_balance_lots=15.0,
        borrowed_short_balance_change_lots=5.0,
        borrowed_short_balance_change_amount_10k=10,
    )
    result = TaiwanMarginShortResult(
        stock_id="TW:00677K",
        symbol="00677K",
        market="TWSE",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TWSE official margin and borrowed-short daily data",
    )

    payload = margin_short_route._tw_margin_short_schema(result).model_dump()

    assert payload["sharesPerLot"] == 100
    assert payload["summary"]["borrowedShortBalanceLots"] == pytest.approx(15.0)


def test_margin_short_route_delegates_to_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(stock_id: str, *, days: int):
        captured["stock_id"] = stock_id
        captured["days"] = days
        return sample_margin_short_result()

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        fake_get,
    )

    response = margin_short_route.get_tw_margin_short("TW:1303", days=30)

    assert captured == {"stock_id": "TW:1303", "days": 30}
    assert response.latestDate == "2026-07-09"
    assert response.summary is not None
    assert response.summary.borrowedShortBalanceChangeAmount10k == 2609


def test_margin_short_route_maps_not_found_errors(monkeypatch) -> None:
    def raise_not_found(*args, **kwargs):
        raise ValueError("Stock not found")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_not_found,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:9999", days=30)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Stock not found"
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_upstream_errors(monkeypatch) -> None:
    def raise_upstream(*args, **kwargs):
        raise RuntimeError("Taiwan margin-short data is temporarily unavailable from official sources.")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_upstream,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:1303", days=30)
    except HTTPException as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_rate_limited_to_503(monkeypatch) -> None:
    def raise_rate_limited(*args, **kwargs):
        raise RuntimeError("Taiwan margin-short official source is rate-limited.")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_rate_limited,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:1303", days=30)
    except HTTPException as exc:
        assert exc.status_code == 503
        assert "rate-limited" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_schema_and_auth_failures_to_502(monkeypatch) -> None:
    cases = (
        "Taiwan margin-short official response could not be interpreted.",
        "Taiwan margin-short official source denied access.",
    )
    for detail in cases:
        monkeypatch.setattr(
            margin_short_route.tw_margin_short_service,
            "get_margin_short",
            lambda *args, detail=detail, **kwargs: (_ for _ in ()).throw(RuntimeError(detail)),
        )
        try:
            margin_short_route.get_tw_margin_short("TW:1303", days=30)
        except HTTPException as exc:
            assert exc.status_code == 502
            assert str(exc.detail) == detail
        else:
            raise AssertionError(f"Expected HTTPException for {detail}")


def test_margin_short_route_maps_unknown_market_as_bad_request(monkeypatch) -> None:
    def raise_unknown(*args, **kwargs):
        raise ValueError("Taiwan margin-short market is unavailable for this stock")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_unknown,
    )

    try:
        margin_short_route.get_tw_margin_short("TW:1303", days=30)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Expected HTTPException")


def test_margin_short_route_maps_non_tw_stock_as_not_found(monkeypatch) -> None:
    def raise_non_tw(*args, **kwargs):
        raise ValueError("Taiwan margin-short data is only available for TW stocks")

    monkeypatch.setattr(
        margin_short_route.tw_margin_short_service,
        "get_margin_short",
        raise_non_tw,
    )

    try:
        margin_short_route.get_tw_margin_short("US:AAPL", days=30)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert "only available for TW stocks" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException")
