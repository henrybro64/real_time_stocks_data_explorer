"""Auth startup validation (#267)."""

from __future__ import annotations

import pytest

from app.core.auth_settings import validate_auth_runtime_settings


def _ok(**overrides):
    base = dict(
        auth_enabled=True,
        local_auth_enabled=True,
        session_secret="a" * 32,
        local_auth_username="",
        local_auth_password="",
    )
    base.update(overrides)
    validate_auth_runtime_settings(**base)


def test_auth_disabled_allows_default_session_secret() -> None:
    validate_auth_runtime_settings(
        auth_enabled=False,
        local_auth_enabled=False,
        session_secret="dev-session-secret-change-me",
        local_auth_username="",
        local_auth_password="",
    )


def test_auth_enabled_rejects_default_session_secret() -> None:
    with pytest.raises(RuntimeError, match="placeholder SESSION_SECRET"):
        _ok(session_secret="dev-session-secret-change-me")


def test_auth_enabled_rejects_placeholder_bootstrap_password() -> None:
    with pytest.raises(RuntimeError, match="placeholder LOCAL_AUTH_PASSWORD"):
        _ok(
            local_auth_username="admin",
            local_auth_password="change-me-local-password",
        )


def test_auth_enabled_rejects_placeholder_capture_token() -> None:
    with pytest.raises(RuntimeError, match="PORTFOLIO_HISTORY_CAPTURE_TOKEN"):
        _ok(portfolio_history_capture_token="k8s-dev-replace-with-random-capture-token")


def test_auth_disabled_rejects_placeholder_capture_token() -> None:
    with pytest.raises(RuntimeError, match="PORTFOLIO_HISTORY_CAPTURE_TOKEN"):
        validate_auth_runtime_settings(
            auth_enabled=False,
            local_auth_enabled=False,
            session_secret="dev-session-secret-change-me",
            local_auth_username="",
            local_auth_password="",
            portfolio_history_capture_token="change-me",
        )


def test_auth_enabled_accepts_random_secret() -> None:
    _ok(session_secret="9f" * 16, portfolio_history_capture_token="tok-" + "ab" * 12)


def test_auth_enabled_allows_entra_without_local() -> None:
    validate_auth_runtime_settings(
        auth_enabled=True,
        local_auth_enabled=False,
        session_secret="9f" * 16,
        local_auth_username="",
        local_auth_password="",
        entra_external_id_enabled=True,
        entra_tenant_id="tenant",
        entra_client_id="client",
        entra_redirect_uri="http://localhost/api/auth/callback/entra",
    )


def test_auth_enabled_rejects_entra_half_configured() -> None:
    with pytest.raises(RuntimeError, match="ENTRA_EXTERNAL_ID_ENABLED"):
        validate_auth_runtime_settings(
            auth_enabled=True,
            local_auth_enabled=True,
            session_secret="9f" * 16,
            local_auth_username="",
            local_auth_password="",
            entra_external_id_enabled=True,
            entra_tenant_id="tenant",
            entra_client_id="",
            entra_redirect_uri="",
        )


def test_auth_enabled_rejects_no_providers() -> None:
    with pytest.raises(RuntimeError, match="LOCAL_AUTH_ENABLED"):
        validate_auth_runtime_settings(
            auth_enabled=True,
            local_auth_enabled=False,
            session_secret="9f" * 16,
            local_auth_username="",
            local_auth_password="",
        )
