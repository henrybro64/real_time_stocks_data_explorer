from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.integration.local_auth_helpers import enable_local_auth, register_local_user


pytestmark = pytest.mark.integration


def test_realized_trade_crud_and_currency_filter(client) -> None:
    create = client.post(
        "/api/realized-trades",
        json={
            "symbol": " aapl ",
            "currency": "USD",
            "buyPrice": 180.25,
            "sellPrice": 195.5,
            "shares": 10.5,
            "buyDate": "2026-07-10",
            "sellDate": "2026-08-20",
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["symbol"] == "AAPL"
    assert created["currency"] == "USD"
    assert created["realizedGain"] == 160.13
    assert created["realizedReturnPercent"] == 8.4605

    twd_create = client.post(
        "/api/realized-trades",
        json={
            "symbol": "2330",
            "currency": "TWD",
            "buyPrice": 920,
            "sellPrice": 1020,
            "shares": 1000,
            "sellDate": "2026-08-01",
        },
    )
    assert twd_create.status_code == 200

    all_trades = client.get("/api/realized-trades")
    assert all_trades.status_code == 200
    assert len(all_trades.json()) == 2

    usd_only = client.get("/api/realized-trades?currency=USD")
    assert usd_only.status_code == 200
    assert len(usd_only.json()) == 1
    assert usd_only.json()[0]["currency"] == "USD"

    updated = client.patch(
        f"/api/realized-trades/{created['id']}",
        json={"sellPrice": 200},
    )
    assert updated.status_code == 200
    assert updated.json()["sellPrice"] == 200.0
    assert updated.json()["realizedGain"] == 207.38

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
    invalid_currency = client.post(
        "/api/realized-trades",
        json={
            "symbol": "AAPL",
            "currency": "EUR",
            "buyPrice": 100,
            "sellPrice": 110,
            "shares": 1,
            "sellDate": "2026-08-01",
        },
    )
    assert invalid_currency.status_code == 400

    invalid_dates = client.post(
        "/api/realized-trades",
        json={
            "symbol": "AAPL",
            "currency": "USD",
            "buyPrice": 100,
            "sellPrice": 110,
            "shares": 1,
            "buyDate": "2026-09-02",
            "sellDate": "2026-09-01",
        },
    )
    assert invalid_dates.status_code == 400


def test_realized_trades_are_isolated_per_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_auth(monkeypatch)

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register_local_user(alice_client, "alice")
        register_local_user(bob_client, "bob")

        alice_create = alice_client.post(
            "/api/realized-trades",
            json={
                "symbol": "AAPL",
                "currency": "USD",
                "buyPrice": 100,
                "sellPrice": 120,
                "shares": 1,
                "sellDate": "2026-08-01",
            },
        )
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
