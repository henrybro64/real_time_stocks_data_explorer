from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.integration.local_auth_helpers import enable_local_auth, register_local_user
from tests.integration.realized_trade_payloads import (
    MINIMAL_USD_TRADE,
    SAMPLE_TWD_TRADE,
    SAMPLE_USD_TRADE,
)


pytestmark = pytest.mark.integration


def test_realized_trade_crud_and_currency_filter(client) -> None:
    create = client.post("/api/realized-trades", json=SAMPLE_USD_TRADE)
    assert create.status_code == 200
    created = create.json()
    assert created["symbol"] == "AAPL"
    assert created["currency"] == "USD"
    assert created["cumulativeDividends"] == 0.0
    assert created["priceGain"] == 160.13
    assert created["realizedGain"] == 160.13
    assert created["realizedReturnPercent"] == 8.4605

    # Issue #204 example: 800→900 × 1000 + 18000 dividends => 14.75% total return.
    twd_create = client.post(
        "/api/realized-trades",
        json={
            **SAMPLE_TWD_TRADE,
            "buyPrice": 800,
            "sellPrice": 900,
            "cumulativeDividends": 18000,
        },
    )
    assert twd_create.status_code == 200
    twd_created = twd_create.json()
    assert twd_created["cumulativeDividends"] == 18000.0
    assert twd_created["priceGain"] == 100000.0
    assert twd_created["realizedGain"] == 118000.0
    assert twd_created["realizedReturnPercent"] == 14.75

    all_trades = client.get("/api/realized-trades")
    assert all_trades.status_code == 200
    assert len(all_trades.json()) == 2

    usd_only = client.get("/api/realized-trades?currency=USD")
    assert usd_only.status_code == 200
    assert len(usd_only.json()) == 1
    assert usd_only.json()[0]["currency"] == "USD"
    assert usd_only.json()[0]["cumulativeDividends"] == 0.0

    updated = client.patch(
        f"/api/realized-trades/{created['id']}",
        json={"sellPrice": 200},
    )
    assert updated.status_code == 200
    assert updated.json()["sellPrice"] == 200.0
    assert updated.json()["priceGain"] == 207.38
    assert updated.json()["realizedGain"] == 207.38

    dividends_only = client.patch(
        f"/api/realized-trades/{created['id']}",
        json={"cumulativeDividends": 12.5},
    )
    assert dividends_only.status_code == 200
    assert dividends_only.json()["cumulativeDividends"] == 12.5
    assert dividends_only.json()["priceGain"] == 207.38
    assert dividends_only.json()["realizedGain"] == 219.88

    cleared_buy_date = client.patch(
        f"/api/realized-trades/{created['id']}",
        json={"buyDate": None},
    )
    assert cleared_buy_date.status_code == 200
    assert cleared_buy_date.json()["buyDate"] is None

    delete = client.delete(f"/api/realized-trades/{created['id']}")
    assert delete.status_code == 204
    missing = client.delete(f"/api/realized-trades/{created['id']}")
    assert missing.status_code == 404


def test_realized_trade_validation_errors(client) -> None:
    cases = [
        ({"currency": "EUR"}, 422),
        ({"buyDate": "2026-09-02", "sellDate": "2026-09-01"}, 400),
        ({"symbol": "A" * 33}, 422),
        ({"buyPrice": 0.0000001}, 400),
        ({"cumulativeDividends": -5}, 422),
    ]
    for payload_override, expected_status in cases:
        response = client.post(
            "/api/realized-trades",
            json={**MINIMAL_USD_TRADE, **payload_override},
        )
        assert response.status_code == expected_status


def test_realized_trades_are_isolated_per_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_auth(monkeypatch)

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register_local_user(alice_client, "alice")
        register_local_user(bob_client, "bob")

        alice_create = alice_client.post("/api/realized-trades", json=MINIMAL_USD_TRADE)
        trade_id = alice_create.json()["id"]

        bob_list = bob_client.get("/api/realized-trades")
        bob_patch = bob_client.patch(
            f"/api/realized-trades/{trade_id}",
            json={"sellPrice": 90},
        )
        bob_delete = bob_client.delete(f"/api/realized-trades/{trade_id}")
        alice_list = alice_client.get("/api/realized-trades")

    assert alice_create.status_code == 200
    assert bob_list.status_code == 200
    assert bob_list.json() == []
    assert bob_patch.status_code == 404
    assert bob_delete.status_code == 404
    assert alice_list.status_code == 200
    assert len(alice_list.json()) == 1
