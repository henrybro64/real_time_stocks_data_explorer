"""Auth startup validation (#267, #284)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.auth_settings import (
    apply_auth_runtime_settings,
    validate_auth_runtime_settings,
)


def _ok(**overrides):
    base = dict(
        auth_enabled=True,
        local_auth_enabled=True,
        session_secret="a" * 32,
        local_auth_username="",
        local_auth_password="",
    )
    base.update(overrides)
    return validate_auth_runtime_settings(**base)


def test_auth_disabled_allows_default_session_secret() -> None:
    soft = validate_auth_runtime_settings(
        auth_enabled=False,
        local_auth_enabled=False,
        session_secret="dev-session-secret-change-me",
        local_auth_username="",
        local_auth_password="",
    )
    assert soft == []


def test_auth_enabled_rejects_default_session_secret() -> None:
    with pytest.raises(RuntimeError, match="placeholder SESSION_SECRET"):
        _ok(session_secret="dev-session-secret-change-me")


def test_auth_enabled_rejects_placeholder_bootstrap_password() -> None:
    with pytest.raises(RuntimeError, match="placeholder LOCAL_AUTH_PASSWORD"):
        _ok(
            local_auth_username="admin",
            local_auth_password="change-me-local-password",
        )


def test_placeholder_capture_token_is_soft_and_cleared() -> None:
    """Placeholder capture tokens must not take the process down (#284)."""
    settings = SimpleNamespace(
        auth_enabled=True,
        local_auth_enabled=True,
        session_secret="9f" * 16,
        local_auth_username="",
        local_auth_password="",
        portfolio_history_capture_token="k8s-dev-replace-with-random-capture-token",
        rankings_capture_token="",
        rankings_archive_capture_token="replace-with-long-random-token",
        taiwan_market_data_capture_token="",
        entra_external_id_enabled=False,
        entra_tenant_id="",
        entra_client_id="",
        entra_redirect_uri="",
        entra_authority="",
    )
    soft = apply_auth_runtime_settings(settings)
    assert "PORTFOLIO_HISTORY_CAPTURE_TOKEN" in soft
    assert "RANKINGS_ARCHIVE_CAPTURE_TOKEN" in soft
    assert settings.portfolio_history_capture_token == ""
    assert settings.rankings_archive_capture_token == ""


def test_hard_failure_lists_capture_placeholders_too() -> None:
    with pytest.raises(RuntimeError) as exc:
        _ok(
            session_secret="dev-session-secret-change-me",
            portfolio_history_capture_token="change-me",
            rankings_archive_capture_token="replace-with-long-random-token",
        )
    message = str(exc.value)
    assert "SESSION_SECRET" in message
    assert "PORTFOLIO_HISTORY_CAPTURE_TOKEN" in message
    assert "RANKINGS_ARCHIVE_CAPTURE_TOKEN" in message


def test_auth_enabled_accepts_random_secret() -> None:
    soft = _ok(session_secret="9f" * 16, portfolio_history_capture_token="tok-" + "ab" * 12)
    assert soft == []


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
        entra_authority="https://contoso.ciamlogin.com/tenant/v2.0",
    )


def test_auth_enabled_rejects_entra_without_authority() -> None:
    with pytest.raises(RuntimeError, match="ENTRA_AUTHORITY"):
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
