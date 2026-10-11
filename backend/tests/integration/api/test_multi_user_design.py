from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.init_ids import DEFAULT_USER_ID
from app.db.models import User
from app.db.session import SessionLocal
from app.main import app
from app.services.quote_service import quote_service
from tests.integration.local_auth_helpers import enable_local_auth, register_local_user
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


def test_single_user_mode_bootstraps_default_principal() -> None:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")

    with SessionLocal() as session:
        session.execute(delete(User).where(User.id == DEFAULT_USER_ID))
        session.commit()

    with TestClient(app):
        pass

    with SessionLocal() as session:
        default_user = session.execute(
            select(User).where(User.id == DEFAULT_USER_ID),
        ).scalar_one_or_none()

    assert default_user is not None
    assert default_user.username == DEFAULT_USER_ID


def test_auth_enabled_rejects_anonymous_access(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_local_auth(monkeypatch)

    with TestClient(app) as client:
        status = client.get("/api/auth/status")
        response = client.get("/api/watchlist")

    assert status.status_code == 200
    assert status.json() == {
        "authEnabled": True,
        "loggedIn": False,
        "providers": {"local": True, "entra": False},
        "user": None,
    }
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_auth_enabled_rejects_stale_session_user(monkeypatch: pytest.MonkeyPatch) -> None:
    if SessionLocal is None:
        raise RuntimeError("SessionLocal is not configured")

    enable_local_auth(monkeypatch)

    with TestClient(app) as client:
        register_local_user(client, "alice")

        with SessionLocal() as session:
            session.execute(delete(User).where(User.username == "alice"))
            session.commit()

        protected_response = client.get("/api/watchlist")
        status_response = client.get("/api/auth/status")

    assert protected_response.status_code == 401
    assert protected_response.json() == {"detail": "Not authenticated"}
    assert status_response.status_code == 200
    assert status_response.json() == {
        "authEnabled": True,
        "loggedIn": False,
        "providers": {"local": True, "entra": False},
        "user": None,
    }


def test_user_owned_resources_are_isolated_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_local_auth(monkeypatch)

    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=200.0,
        change=1.0,
        change_percent=0.5,
        previous_close=199.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: quote if stock_id == "US:AAPL" else None,
    )

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register_local_user(alice_client, "alice")
        register_local_user(bob_client, "bob")

        alice_watchlist = alice_client.post("/api/watchlist/items", json={"id": "US:AAPL"})
        alice_position = alice_client.post(
            "/api/portfolio/positions",
            json={
                "stockId": "US:AAPL",
                "quantity": 2,
                "averageCost": 180,
                "currency": "USD",
                "notes": "Alice position",
            },
        )
        alice_asset = alice_client.post(
            "/api/portfolio/assets",
            json={
                "assetType": "cash",
                "name": "Broker cash",
                "currency": "USD",
                "value": 5000,
                "notes": "Alice asset",
            },
        )
        alice_key = alice_client.put(
            "/api/ai/keys/openai",
            json={"apiKey": "sk-alice-1234"},
        )

        bob_watchlist = bob_client.get("/api/watchlist")
        bob_positions = bob_client.get("/api/portfolio/positions")
        bob_assets = bob_client.get("/api/portfolio/assets")
        bob_key_before = bob_client.get("/api/ai/keys/openai")
        bob_delete_position = bob_client.delete(
            f"/api/portfolio/positions/{alice_position.json()['id']}",
        )
        bob_update_asset = bob_client.patch(
            f"/api/portfolio/assets/{alice_asset.json()['id']}",
            json={"name": "Bob takeover"},
        )
        bob_key = bob_client.put(
            "/api/ai/keys/openai",
            json={"apiKey": "sk-bob-9999"},
        )

        alice_watchlist_after = alice_client.get("/api/watchlist")
        alice_positions_after = alice_client.get("/api/portfolio/positions")
        alice_assets_after = alice_client.get("/api/portfolio/assets")
        alice_key_after = alice_client.get("/api/ai/keys/openai")
        bob_key_after = bob_client.get("/api/ai/keys/openai")

    assert alice_watchlist.status_code == 200
    assert alice_watchlist.json() == {"ids": ["US:AAPL"]}
    assert alice_position.status_code == 200
    assert alice_asset.status_code == 200
    assert alice_key.status_code == 200

    assert bob_watchlist.status_code == 200
    assert bob_watchlist.json() == {"ids": []}
    assert bob_positions.status_code == 200
    assert bob_positions.json() == []
    assert bob_assets.status_code == 200
    assert bob_assets.json() == []
    assert bob_key_before.status_code == 200
    assert bob_key_before.json() == {
        "provider": "openai",
        "configured": False,
        "keyLast4": None,
    }

    assert bob_delete_position.status_code == 404
    assert bob_delete_position.json() == {
        "detail": f"Position not found: {alice_position.json()['id']}",
    }
    assert bob_update_asset.status_code == 404
    assert bob_update_asset.json() == {
        "detail": f"Asset not found: {alice_asset.json()['id']}",
    }
    assert bob_key.status_code == 200

    assert alice_watchlist_after.status_code == 200
    assert alice_watchlist_after.json() == {"ids": ["US:AAPL"]}
    assert alice_positions_after.status_code == 200
    assert [row["stockId"] for row in alice_positions_after.json()] == ["US:AAPL"]
    assert alice_assets_after.status_code == 200
    assert [row["name"] for row in alice_assets_after.json()] == ["Broker cash"]
    assert alice_key_after.status_code == 200
    assert alice_key_after.json() == {
        "provider": "openai",
        "configured": True,
        "keyLast4": "1234",
    }
    assert bob_key_after.status_code == 200
    assert bob_key_after.json() == {
        "provider": "openai",
        "configured": True,
        "keyLast4": "9999",
    }


def test_cross_user_watchlist_delete_does_not_remove_other_users_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_auth(monkeypatch)

    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "US:AAPL" else None,
    )

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register_local_user(alice_client, "alice")
        register_local_user(bob_client, "bob")

        alice_add = alice_client.post("/api/watchlist/items", json={"id": "US:AAPL"})
        bob_delete = bob_client.delete("/api/watchlist/items/US%3AAAPL")
        alice_watchlist_after = alice_client.get("/api/watchlist")
        bob_watchlist_after = bob_client.get("/api/watchlist")

    assert alice_add.status_code == 200
    assert alice_add.json() == {"ids": ["US:AAPL"]}
    assert bob_delete.status_code == 200
    assert bob_delete.json() == {"ids": []}
    assert alice_watchlist_after.status_code == 200
    assert alice_watchlist_after.json() == {"ids": ["US:AAPL"]}
    assert bob_watchlist_after.status_code == 200
    assert bob_watchlist_after.json() == {"ids": []}


def test_forged_user_id_in_request_body_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_auth(monkeypatch)

    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=200.0,
        change=1.0,
        change_percent=0.5,
        previous_close=199.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: quote if stock_id == "US:AAPL" else None,
    )

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register_local_user(alice_client, "alice")
        register_local_user(bob_client, "bob")

        alice_watchlist = alice_client.post(
            "/api/watchlist/items",
            json={"id": "US:AAPL", "userId": "bob"},
        )
        alice_position = alice_client.post(
            "/api/portfolio/positions",
            json={
                "stockId": "US:AAPL",
                "quantity": 1,
                "averageCost": 180,
                "currency": "USD",
                "userId": "bob",
            },
        )

        bob_watchlist = bob_client.get("/api/watchlist")
        bob_positions = bob_client.get("/api/portfolio/positions")

    assert alice_watchlist.status_code == 200
    assert alice_watchlist.json() == {"ids": ["US:AAPL"]}
    assert alice_position.status_code == 200
    assert bob_watchlist.status_code == 200
    assert bob_watchlist.json() == {"ids": []}
    assert bob_positions.status_code == 200
    assert bob_positions.json() == []


def test_ai_preferences_are_isolated_per_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_local_auth(monkeypatch)

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register_local_user(alice_client, "alice")
        register_local_user(bob_client, "bob")

        alice_set = alice_client.put(
            "/api/ai/preferences",
            json={"preferredProvider": "openai"},
        )
        bob_before = bob_client.get("/api/ai/preferences")
        bob_set = bob_client.put(
            "/api/ai/preferences",
            json={"preferredProvider": "perplexity"},
        )
        alice_after = alice_client.get("/api/ai/preferences")
        bob_after = bob_client.get("/api/ai/preferences")

    assert alice_set.status_code == 200
    assert alice_set.json() == {"preferredProvider": "openai"}
    assert bob_before.status_code == 200
    assert bob_before.json() == {"preferredProvider": None}
    assert bob_set.status_code == 200
    assert bob_set.json() == {"preferredProvider": "perplexity"}
    assert alice_after.status_code == 200
    assert alice_after.json() == {"preferredProvider": "openai"}
    assert bob_after.status_code == 200
    assert bob_after.json() == {"preferredProvider": "perplexity"}
