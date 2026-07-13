import pytest

from tests.support import sample_margin_short_result


pytestmark = pytest.mark.integration


def test_margin_short_endpoint_returns_summary_and_history(monkeypatch, client) -> None:
    from app.api.routes import tw_margin_short as margin_short_routes

    result = sample_margin_short_result()
    monkeypatch.setattr(
        margin_short_routes.tw_margin_short_service,
        "get_margin_short",
        lambda stock_id, *, days: result,
    )

    response = client.get("/api/stocks/TW:1303/margin-short", params={"days": 90})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stockId"] == "TW:1303"
    assert payload["symbol"] == "1303"
    assert payload["market"] == "TWSE"
    assert payload["latestDate"] == "2026-07-09"
    assert payload["sharesPerLot"] == 1000
    assert payload["summary"] == {
        "marginBalanceLots": 31339.0,
        "shortMarginBalanceLots": 0.0,
        "borrowedShortBalanceLots": 12345.0,
        "borrowedShortBalanceChangeLots": 141.0,
        "borrowedShortBalanceChangeAmount10k": 2609,
    }
    assert payload["history"][0]["closePrice"] == pytest.approx(185.0)
    assert payload["source"] == "TWSE official margin and borrowed-short daily data"


def test_margin_short_endpoint_exposes_100_unit_shares_per_lot_for_fx_etf(
    monkeypatch,
    client,
) -> None:
    from app.api.routes import tw_margin_short as margin_short_routes
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
    monkeypatch.setattr(
        margin_short_routes.tw_margin_short_service,
        "get_margin_short",
        lambda stock_id, *, days: result,
    )

    response = client.get("/api/stocks/TW:00677K/margin-short", params={"days": 90})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sharesPerLot"] == 100
    assert payload["summary"]["borrowedShortBalanceLots"] == pytest.approx(15.0)


def test_margin_short_endpoint_rejects_non_taiwan_stocks(monkeypatch, client) -> None:
    from app.api.routes import tw_margin_short as margin_short_routes

    def raise_non_tw(stock_id: str, *, days: int):
        raise ValueError("Taiwan margin-short data is only available for TW stocks")

    monkeypatch.setattr(
        margin_short_routes.tw_margin_short_service,
        "get_margin_short",
        raise_non_tw,
    )

    response = client.get("/api/stocks/US:AAPL/margin-short")

    assert response.status_code == 404
    assert response.json()["detail"] == "Taiwan margin-short data is only available for TW stocks"


def test_margin_short_endpoint_returns_400_for_unknown_market(monkeypatch, client) -> None:
    from app.api.routes import tw_margin_short as margin_short_routes

    def raise_unknown(stock_id: str, *, days: int):
        raise ValueError("Taiwan margin-short market is unavailable for this stock")

    monkeypatch.setattr(
        margin_short_routes.tw_margin_short_service,
        "get_margin_short",
        raise_unknown,
    )

    response = client.get("/api/stocks/TW:1303/margin-short")

    assert response.status_code == 400
    assert "unavailable for this stock" in response.json()["detail"]


def test_margin_short_endpoint_returns_502_when_upstream_unavailable(monkeypatch, client) -> None:
    from app.api.routes import tw_margin_short as margin_short_routes

    def raise_upstream(stock_id: str, *, days: int):
        raise RuntimeError(
            "Taiwan margin-short data is temporarily unavailable from official sources."
        )

    monkeypatch.setattr(
        margin_short_routes.tw_margin_short_service,
        "get_margin_short",
        raise_upstream,
    )

    response = client.get("/api/stocks/TW:1303/margin-short")

    assert response.status_code == 502
    assert "temporarily unavailable" in response.json()["detail"]
