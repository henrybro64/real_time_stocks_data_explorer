from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.alert_service import alert_service
from app.services.quote_service import quote_service
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


def _install_stock_quote_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stock_id: str = "US:AAPL",
    price_state: dict[str, float],
) -> None:
    stock = stock_record(
        stock_id,
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )

    def get_stock(request_stock_id: str):
        return stock if request_stock_id == stock_id else None

    def get_quote(request_stock_id: str):
        if request_stock_id != stock_id:
            return None
        return quote_record(
            stock_id,
            last_price=price_state["last_price"],
            change=1.0,
            change_percent=0.5,
            previous_close=price_state["last_price"] - 1,
            timestamp="2026-05-26T09:30:00-04:00",
            is_market_open=True,
            open_price=price_state["last_price"] - 0.5,
            high=price_state["last_price"] + 1,
            low=price_state["last_price"] - 2,
            volume=1_000_000,
        )

    def search(query: str):
        needle = query.strip().upper()
        symbol = stock.symbol.upper()
        if needle in {symbol, stock.id.upper()} or needle == symbol:
            return [stock]
        return []

    def resolve_stock_id(raw: str) -> str:
        return quote_service.resolve_stock_id(raw)

    monkeypatch.setattr(quote_service, "get_stock", get_stock)
    monkeypatch.setattr(quote_service, "get_quote", get_quote)
    monkeypatch.setattr(quote_service, "search", search)
    monkeypatch.setattr(alert_service, "_stock_loader", get_stock)
    monkeypatch.setattr(alert_service, "_quote_loader", get_quote)
    monkeypatch.setattr(alert_service, "_stock_resolver", resolve_stock_id)


def _enable_local_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "local_auth_enabled", True)
    monkeypatch.setattr(settings, "local_auth_username", "")
    monkeypatch.setattr(settings, "local_auth_password", "")


def _register_user(client: TestClient, username: str, password: str = "password-123") -> None:
    response = client.post(
        "/api/auth/register/local",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


def test_alert_rule_crud_event_history_and_duplicate_suppression(
    monkeypatch: pytest.MonkeyPatch,
    client,
) -> None:
    price_state = {"last_price": 205.0}
    _install_stock_quote_mocks(monkeypatch, price_state=price_state)

    create = client.post(
        "/api/alerts/rules",
        json={
            "stockId": "US:AAPL",
            "ruleType": "price_above",
            "thresholdValue": 200,
            "enabled": True,
        },
    )

    assert create.status_code == 200
    created = create.json()
    assert created["stockId"] == "US:AAPL"
    assert created["ruleType"] == "price_above"
    assert created["thresholdValue"] == 200.0
    assert created["enabled"] is True
    assert created["lastConditionMet"] is True
    assert created["lastObservedPrice"] == 205.0
    assert created["lastTriggeredAt"] is not None

    first_events = client.get("/api/alerts/events")
    second_events = client.get("/api/alerts/events")

    assert first_events.status_code == 200
    assert len(first_events.json()) == 1
    assert second_events.status_code == 200
    assert len(second_events.json()) == 1
    assert second_events.json()[0]["message"] == "AAPL rose above 200.00 (observed 205.00)."

    price_state["last_price"] = 199.0
    list_rules_after_fall = client.get("/api/alerts/rules")
    assert list_rules_after_fall.status_code == 200
    assert list_rules_after_fall.json()[0]["lastConditionMet"] is False
    assert list_rules_after_fall.json()[0]["lastObservedPrice"] == 199.0

    price_state["last_price"] = 206.0
    retriggered_events = client.get("/api/alerts/events")
    assert retriggered_events.status_code == 200
    assert len(retriggered_events.json()) == 2

    disable = client.patch(
        f"/api/alerts/rules/{created['id']}",
        json={"enabled": False},
    )
    assert disable.status_code == 200
    assert disable.json()["enabled"] is False
    assert disable.json()["lastConditionMet"] is False

    price_state["last_price"] = 220.0
    disabled_events = client.get("/api/alerts/events")
    assert disabled_events.status_code == 200
    assert len(disabled_events.json()) == 2

    enable = client.patch(
        f"/api/alerts/rules/{created['id']}",
        json={"enabled": True},
    )
    assert enable.status_code == 200
    assert enable.json()["enabled"] is True
    assert enable.json()["lastConditionMet"] is True
    assert enable.json()["lastObservedPrice"] == 220.0

    updated_rule = client.patch(
        f"/api/alerts/rules/{created['id']}",
        json={"thresholdValue": 230},
    )
    assert updated_rule.status_code == 200
    assert updated_rule.json()["thresholdValue"] == 230.0
    assert updated_rule.json()["lastConditionMet"] is False

    delete = client.delete(f"/api/alerts/rules/{created['id']}")
    assert delete.status_code == 204

    rules_after_delete = client.get("/api/alerts/rules")
    history_after_delete = client.get("/api/alerts/events")
    assert rules_after_delete.status_code == 200
    assert rules_after_delete.json() == []
    assert history_after_delete.status_code == 200
    assert len(history_after_delete.json()) == 3
    assert {event["alertRuleId"] for event in history_after_delete.json()} == {None}
    assert {event["stockId"] for event in history_after_delete.json()} == {"US:AAPL"}


def test_alert_rule_rejects_unknown_stock(monkeypatch: pytest.MonkeyPatch, client) -> None:
    price_state = {"last_price": 205.0}
    _install_stock_quote_mocks(monkeypatch, price_state=price_state)

    response = client.post(
        "/api/alerts/rules",
        json={
            "stockId": "US:NOPE",
            "ruleType": "price_above",
            "thresholdValue": 200,
            "enabled": True,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unknown stock id: US:NOPE"}


def test_alert_rule_resolves_bare_and_lowercase_tickers(
    monkeypatch: pytest.MonkeyPatch,
    client,
) -> None:
    price_state = {"last_price": 205.0}
    _install_stock_quote_mocks(monkeypatch, price_state=price_state)

    for stock_id in ("AAPL", "aapl", "  AAPL  "):
        response = client.post(
            "/api/alerts/rules",
            json={
                "stockId": stock_id,
                "ruleType": "price_above",
                "thresholdValue": 200,
                "enabled": True,
            },
        )
        assert response.status_code == 200, stock_id
        assert response.json()["stockId"] == "US:AAPL"


def test_alert_rules_and_events_are_isolated_per_authenticated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_local_auth(monkeypatch)
    price_state = {"last_price": 205.0}
    _install_stock_quote_mocks(monkeypatch, price_state=price_state)

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        _register_user(alice_client, "alice")
        _register_user(bob_client, "bob")

        alice_create = alice_client.post(
            "/api/alerts/rules",
            json={
                "stockId": "US:AAPL",
                "ruleType": "price_above",
                "thresholdValue": 200,
                "enabled": True,
            },
        )
        alice_rule_id = alice_create.json()["id"]

        bob_rules = bob_client.get("/api/alerts/rules")
        bob_events = bob_client.get("/api/alerts/events")
        bob_update = bob_client.patch(
            f"/api/alerts/rules/{alice_rule_id}",
            json={"enabled": False},
        )
        bob_delete = bob_client.delete(f"/api/alerts/rules/{alice_rule_id}")

        alice_rules = alice_client.get("/api/alerts/rules")
        alice_events = alice_client.get("/api/alerts/events")

    assert alice_create.status_code == 200
    assert bob_rules.status_code == 200
    assert bob_rules.json() == []
    assert bob_events.status_code == 200
    assert bob_events.json() == []
    assert bob_update.status_code == 404
    assert bob_update.json() == {"detail": f"Alert rule not found: {alice_rule_id}"}
    assert bob_delete.status_code == 404
    assert bob_delete.json() == {"detail": f"Alert rule not found: {alice_rule_id}"}
    assert alice_rules.status_code == 200
    assert [row["id"] for row in alice_rules.json()] == [alice_rule_id]
    assert alice_events.status_code == 200
    assert len(alice_events.json()) == 1
    assert alice_events.json()[0]["stockId"] == "US:AAPL"
