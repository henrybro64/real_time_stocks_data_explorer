"""Entra and local sessions stay separate and survive a later request (#299)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.api.deps import clear_identity_cache_for_tests
from app.api.routes.auth import router
from app.services.entra_auth_service import EntraAuthError, EntraLoginStart


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="unit-test-session-secret-32b",
        same_site="lax",
        https_only=False,
    )
    app.include_router(router, prefix="/api")
    return app


def _enable_auth(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "auth_enabled", True)
    monkeypatch.setattr(config.settings, "local_auth_enabled", True)
    monkeypatch.setattr(config.settings, "entra_external_id_enabled", True)
    monkeypatch.setattr(config.settings, "entra_tenant_id", "tenant-1")
    monkeypatch.setattr(config.settings, "entra_client_id", "client-1")
    monkeypatch.setattr(config.settings, "entra_client_secret", "present-but-not-logged")
    monkeypatch.setattr(config.settings, "entra_redirect_uri", "https://stock.cloudforge64.com/api/auth/callback/entra")
    monkeypatch.setattr(config.settings, "entra_authority", "https://login.example/tenant-1/v2.0")
    clear_identity_cache_for_tests()


def test_entra_session_survives_refresh_and_logout_clears_it(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    users = {
        "u-entra": {"id": "u-entra", "username": "entra-user"},
        "u-local": {"id": "u-local", "username": "local-user"},
    }
    monkeypatch.setattr("app.api.deps.get_user_by_id", lambda user_id: users.get(user_id))
    monkeypatch.setattr("app.api.routes.auth.get_user_by_id", lambda user_id: users.get(user_id))
    monkeypatch.setattr(
        "app.api.routes.auth.begin_entra_login",
        lambda: EntraLoginStart(
            authorization_url="https://login.example/authorize",
            state="state-1",
            nonce="nonce-1",
            code_verifier="verifier-1",
        ),
    )
    seen: dict = {}

    def fake_complete(**kwargs):
        seen.update(kwargs)
        if kwargs["state"] != kwargs["expected_state"]:
            raise EntraAuthError("Invalid OAuth state")
        if kwargs["code_verifier"] != "verifier-1":
            raise EntraAuthError("Missing PKCE verifier")
        if kwargs["expected_nonce"] != "nonce-1":
            raise EntraAuthError("Invalid ID token")
        return "u-entra"

    monkeypatch.setattr("app.api.routes.auth.complete_entra_login", fake_complete)
    monkeypatch.setattr("app.api.routes.auth.verify_local_login", lambda username, password: "u-local" if username == "local-user" and password == "local-pass" else None)
    monkeypatch.setattr("app.api.routes.auth._enforce_auth_rate_limit", lambda *args, **kwargs: None)

    with TestClient(_app()) as client:
        start = client.get("/api/auth/login/entra", follow_redirects=False)
        assert start.status_code == 302
        assert start.headers["location"] == "https://login.example/authorize"

        rejected = client.get(
            "/api/auth/callback/entra",
            params={"code": "bad-code", "state": "wrong-state"},
            follow_redirects=False,
        )
        assert rejected.status_code == 401
        assert rejected.json()["detail"] == "Invalid OAuth state"
        signed_out = client.get("/api/auth/status")
        assert signed_out.json()["loggedIn"] is False

        start_again = client.get("/api/auth/login/entra", follow_redirects=False)
        assert start_again.status_code == 302
        callback = client.get(
            "/api/auth/callback/entra",
            params={"code": "real-code", "state": "state-1"},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/"
        assert seen["code_verifier"] == "verifier-1"
        assert seen["expected_nonce"] == "nonce-1"
        assert "present-but-not-logged" not in str(seen)

        status = client.get("/api/auth/status")
        body = status.json()
        assert body["loggedIn"] is True
        assert body["user"]["id"] == "u-entra"
        assert body["providers"]["local"] is True
        refreshed = client.get("/api/auth/status")
        assert refreshed.json()["user"]["id"] == "u-entra"

        logged_out = client.post("/api/auth/logout")
        assert logged_out.status_code == 200
        after = client.get("/api/auth/status")
        assert after.json()["loggedIn"] is False
        assert after.json()["user"] is None

        local = client.post(
            "/api/auth/login/local",
            json={"username": "local-user", "password": "local-pass"},
        )
        assert local.status_code == 200
        local_status = client.get("/api/auth/status")
        assert local_status.json()["user"]["id"] == "u-local"
        assert local_status.json()["user"]["id"] != "u-entra"
