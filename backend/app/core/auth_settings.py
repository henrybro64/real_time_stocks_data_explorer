"""Startup validation for auth and capture secrets (#267, #240)."""

from __future__ import annotations

_DEFAULT_SESSION_SECRET = "dev-session-secret-change-me"
_PLACEHOLDER_SESSION_SECRETS = frozenset(
    {
        _DEFAULT_SESSION_SECRET,
        "k8s-dev-replace-with-openssl-rand-hex-32-chars-minimum-16",
        "change-me",
        "secret",
        "session-secret",
    }
)
_PLACEHOLDER_BOOTSTRAP_PASSWORDS = frozenset(
    {
        "change-me-local-password",
        "change-me",
        "password",
        "admin",
    }
)
_PLACEHOLDER_CAPTURE_TOKENS = frozenset(
    {
        "k8s-dev-replace-with-random-capture-token",
        "change-me",
        "capture-token",
        "replace-with-long-random-token",
    }
)


def validate_auth_runtime_settings(
    *,
    auth_enabled: bool,
    local_auth_enabled: bool,
    session_secret: str,
    local_auth_username: str,
    local_auth_password: str,
    portfolio_history_capture_token: str = "",
    rankings_capture_token: str = "",
    rankings_archive_capture_token: str = "",
    taiwan_market_data_capture_token: str = "",
    entra_external_id_enabled: bool = False,
    entra_tenant_id: str = "",
    entra_client_id: str = "",
    entra_redirect_uri: str = "",
    entra_authority: str = "",
) -> None:
    entra_ready = bool(
        entra_external_id_enabled
        and entra_tenant_id.strip()
        and entra_client_id.strip()
        and entra_redirect_uri.strip()
        and (entra_authority.strip() or entra_tenant_id.strip())
    )
    if auth_enabled and not local_auth_enabled and not entra_ready:
        raise RuntimeError(
            "AUTH_ENABLED requires LOCAL_AUTH_ENABLED=true and/or a fully "
            "configured Entra External ID provider "
            "(ENTRA_EXTERNAL_ID_ENABLED, ENTRA_TENANT_ID, ENTRA_CLIENT_ID, "
            "ENTRA_REDIRECT_URI).",
        )
    if bool(local_auth_username) ^ bool(local_auth_password):
        raise RuntimeError(
            "Set both LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD, or leave both empty.",
        )
    if local_auth_password and len(local_auth_password) < 8:
        raise RuntimeError("LOCAL_AUTH_PASSWORD must be at least 8 characters.")

    # Capture endpoints stay reachable when session auth is off, so a guessable
    # token is refused either way.
    _reject_placeholder_capture_tokens(
        portfolio_history_capture_token=portfolio_history_capture_token,
        rankings_capture_token=rankings_capture_token,
        rankings_archive_capture_token=rankings_archive_capture_token,
        taiwan_market_data_capture_token=taiwan_market_data_capture_token,
    )

    if not auth_enabled:
        return

    secret = session_secret.strip()
    if len(secret) < 16:
        raise RuntimeError(
            "AUTH_ENABLED requires SESSION_SECRET (or session_secret) "
            "with at least 16 characters.",
        )
    if secret in _PLACEHOLDER_SESSION_SECRETS or _looks_like_placeholder(secret):
        raise RuntimeError(
            "AUTH_ENABLED refuses a default or placeholder SESSION_SECRET. "
            "Generate a random value (for example openssl rand -hex 32).",
        )
    if local_auth_enabled and local_auth_password.strip().casefold() in _PLACEHOLDER_BOOTSTRAP_PASSWORDS:
        raise RuntimeError(
            "AUTH_ENABLED refuses a placeholder LOCAL_AUTH_PASSWORD. "
            "Choose a unique bootstrap password or leave both username and password empty.",
        )
    if entra_external_id_enabled and not entra_ready:
        raise RuntimeError(
            "ENTRA_EXTERNAL_ID_ENABLED requires ENTRA_TENANT_ID, ENTRA_CLIENT_ID, "
            "and ENTRA_REDIRECT_URI (and optionally ENTRA_AUTHORITY).",
        )


def _reject_placeholder_capture_tokens(
    *,
    portfolio_history_capture_token: str,
    rankings_capture_token: str,
    rankings_archive_capture_token: str,
    taiwan_market_data_capture_token: str,
) -> None:
    for label, token in (
        ("PORTFOLIO_HISTORY_CAPTURE_TOKEN", portfolio_history_capture_token),
        ("RANKINGS_CAPTURE_TOKEN", rankings_capture_token),
        ("RANKINGS_ARCHIVE_CAPTURE_TOKEN", rankings_archive_capture_token),
        ("TAIWAN_MARKET_DATA_CAPTURE_TOKEN", taiwan_market_data_capture_token),
    ):
        cleaned = token.strip()
        if not cleaned:
            continue
        if cleaned in _PLACEHOLDER_CAPTURE_TOKENS or _looks_like_placeholder(cleaned):
            raise RuntimeError(
                f"{label} is a placeholder. Generate a random token or leave the value empty.",
            )


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return "replace" in lowered or "change-me" in lowered or "changeme" in lowered
