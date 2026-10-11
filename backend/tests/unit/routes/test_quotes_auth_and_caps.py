"""Market-data auth and quote ids caps (#272)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, WebSocketDisconnect

from app.api.routes import quotes as quotes_route
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient
from tests.integration.local_auth_helpers import enable_local_auth


def test_quotes_reject_anonymous_when_auth_enabled(monkeypatch) -> None:
    enable_local_auth(monkeypatch)

    # Avoid startup DB seed when exercising auth on a fresh client.
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr("app.main.seed_default_user", lambda: None)
    monkeypatch.setattr("app.main.ensure_bootstrap_local_user", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr("app.main.stop_background_jobs", lambda: None)

    with TestClient(app) as client:
        response = client.get("/api/quotes", params={"ids": "US:AAPL"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/search", {"q": "aa"}),
        ("/api/markets", None),
        ("/api/themes", None),
        ("/api/quotes/US:AAPL", None),
        ("/api/stocks/US:AAPL/history", None),
        ("/api/stocks/US:AAPL/news", None),
        ("/api/stocks/TW:2330/institutional-investors", None),
        ("/api/rankings", {"market": "US", "type": "change_up"}),
    ],
)
def test_market_data_routes_reject_anonymous_when_auth_enabled(
    monkeypatch,
    path: str,
    params: dict[str, str] | None,
) -> None:
    enable_local_auth(monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr("app.main.seed_default_user", lambda: None)
    monkeypatch.setattr("app.main.ensure_bootstrap_local_user", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr("app.main.stop_background_jobs", lambda: None)

    with TestClient(app) as client:
        response = client.get(path, params=params)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_stream_rejects_anonymous_when_auth_enabled(monkeypatch) -> None:
    enable_local_auth(monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr("app.main.seed_default_user", lambda: None)
    monkeypatch.setattr("app.main.ensure_bootstrap_local_user", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr("app.main.stop_background_jobs", lambda: None)

    with TestClient(app) as client:
        with client.websocket_connect("/api/stream?ids=US:AAPL") as websocket:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()
    assert exc_info.value.code == 4401


def test_quotes_reject_malformed_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        quotes_route.quote_service,
        "get_quotes_cache_first",
        lambda _ids: (_ for _ in ()).throw(AssertionError("should reject before lookup")),
    )
    with pytest.raises(HTTPException) as exc_info:
        quotes_route.get_quotes(ids="US:AAPL,not-an-id")
    assert exc_info.value.status_code == 422


def test_quotes_normalize_ids_before_lookup(monkeypatch) -> None:
    seen: dict[str, list[str]] = {}

    def capture(ids: list[str]):
        seen["ids"] = ids
        return []

    monkeypatch.setattr(quotes_route.quote_service, "get_quotes_cache_first", capture)
    assert quotes_route.get_quotes(ids="us:brk-b,tw:2330") == []
    assert seen["ids"] == ["US:BRK-B", "TW:2330"]


def test_quotes_reject_oversized_ids_list(monkeypatch) -> None:
    monkeypatch.setattr(settings, "quote_batch_ids_max", 3)
    monkeypatch.setattr(
        quotes_route.quote_service,
        "get_quotes_cache_first",
        lambda _ids: (_ for _ in ()).throw(AssertionError("should reject before lookup")),
    )
    with pytest.raises(HTTPException) as exc_info:
        quotes_route.get_quotes(ids=",".join(f"US:S{i}" for i in range(5)))
    assert exc_info.value.status_code == 422
    assert "maximum is 3" in str(exc_info.value.detail)
