"""Startup validation for auth and capture secrets (#267, #240, #284)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

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

_CAPTURE_TOKEN_SETTINGS = (
    ("PORTFOLIO_HISTORY_CAPTURE_TOKEN", "portfolio_history_capture_token"),
    ("RANKINGS_CAPTURE_TOKEN", "rankings_capture_token"),
    ("RANKINGS_ARCHIVE_CAPTURE_TOKEN", "rankings_archive_capture_token"),
    ("TAIWAN_MARKET_DATA_CAPTURE_TOKEN", "taiwan_market_data_capture_token"),
)


def looks_like_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return "replace" in lowered or "change-me" in lowered or "changeme" in lowered


def is_placeholder_capture_token(token: str) -> bool:
    cleaned = token.strip()
    if not cleaned:
        return False
    return cleaned in _PLACEHOLDER_CAPTURE_TOKENS or looks_like_placeholder(cleaned)


def is_placeholder_session_secret(secret: str) -> bool:
    cleaned = secret.strip()
    return (
        not cleaned
        or len(cleaned) < 16
        or cleaned in _PLACEHOLDER_SESSION_SECRETS
        or looks_like_placeholder(cleaned)
    )


def is_placeholder_bootstrap_password(password: str) -> bool:
    return password.strip().casefold() in _PLACEHOLDER_BOOTSTRAP_PASSWORDS


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
) -> list[str]:
    """Validate auth settings.

    Returns labels of placeholder capture tokens that must be treated as unset.
    Raises ``RuntimeError`` listing every hard failure (and any soft capture
    findings) so a repair can fix everything in one pass (#284).
    """
    hard: list[str] = []
    soft_capture = _placeholder_capture_labels(
        portfolio_history_capture_token=portfolio_history_capture_token,
        rankings_capture_token=rankings_capture_token,
        rankings_archive_capture_token=rankings_archive_capture_token,
        taiwan_market_data_capture_token=taiwan_market_data_capture_token,
    )

    entra_ready = bool(
        entra_external_id_enabled
        and entra_tenant_id.strip()
        and entra_client_id.strip()
        and entra_redirect_uri.strip()
        and entra_authority.strip()
    )
    if auth_enabled and not local_auth_enabled and not entra_ready:
        hard.append(
            "AUTH_ENABLED requires LOCAL_AUTH_ENABLED=true and/or a fully "
            "configured Entra External ID provider "
            "(ENTRA_EXTERNAL_ID_ENABLED, ENTRA_TENANT_ID, ENTRA_CLIENT_ID, "
            "ENTRA_REDIRECT_URI, and ENTRA_AUTHORITY)."
        )
    if bool(local_auth_username) ^ bool(local_auth_password):
        hard.append(
            "Set both LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD, or leave both empty."
        )
    if local_auth_password and len(local_auth_password) < 8:
        hard.append("LOCAL_AUTH_PASSWORD must be at least 8 characters.")

    if auth_enabled:
        secret = session_secret.strip()
        if len(secret) < 16:
            hard.append(
                "AUTH_ENABLED requires SESSION_SECRET (or session_secret) "
                "with at least 16 characters."
            )
        elif secret in _PLACEHOLDER_SESSION_SECRETS or looks_like_placeholder(secret):
            hard.append(
                "AUTH_ENABLED refuses a default or placeholder SESSION_SECRET. "
                "Generate a random value (for example openssl rand -hex 32)."
            )
        if (
            local_auth_enabled
            and local_auth_password
            and is_placeholder_bootstrap_password(local_auth_password)
        ):
            hard.append(
                "AUTH_ENABLED refuses a placeholder LOCAL_AUTH_PASSWORD. "
                "Choose a unique bootstrap password or leave both username and "
                "password empty (existing accounts keep their stored password)."
            )
        if entra_external_id_enabled and not entra_ready and not local_auth_enabled:
            hard.append(
                "ENTRA_EXTERNAL_ID_ENABLED requires ENTRA_TENANT_ID, ENTRA_CLIENT_ID, "
                "ENTRA_REDIRECT_URI, and ENTRA_AUTHORITY "
                "(the tenant's https://<tenant>.ciamlogin.com/<tenant-id>/v2.0 URL)."
            )

    if hard:
        details = hard + [
            f"{label} is a placeholder and will be ignored (capture disabled). "
            "Generate a random token or leave the value empty."
            for label in soft_capture
        ]
        raise RuntimeError(" ".join(details))

    return soft_capture


def apply_auth_runtime_settings(settings: Any) -> list[str]:
    """Validate settings and clear placeholder capture tokens in place (#284).

    Same entrypoint for the API process and the worker.
    """
    soft = validate_auth_runtime_settings(
        auth_enabled=settings.auth_enabled,
        local_auth_enabled=settings.local_auth_enabled,
        session_secret=settings.session_secret,
        local_auth_username=settings.local_auth_username,
        local_auth_password=settings.local_auth_password,
        portfolio_history_capture_token=settings.portfolio_history_capture_token,
        rankings_capture_token=settings.rankings_capture_token,
        rankings_archive_capture_token=settings.rankings_archive_capture_token,
        taiwan_market_data_capture_token=settings.taiwan_market_data_capture_token,
        entra_external_id_enabled=settings.entra_external_id_enabled,
        entra_tenant_id=settings.entra_tenant_id,
        entra_client_id=settings.entra_client_id,
        entra_redirect_uri=settings.entra_redirect_uri,
        entra_authority=settings.entra_authority,
    )
    for label, attr in _CAPTURE_TOKEN_SETTINGS:
        if label in soft:
            setattr(settings, attr, "")
            logger.error(
                "Placeholder capture token disabled; endpoint treated as not configured",
                extra={
                    "event": "auth.capture_token.placeholder_disabled",
                    "token_name": label,
                },
            )
    entra_ready = bool(
        settings.entra_external_id_enabled
        and str(settings.entra_tenant_id).strip()
        and str(settings.entra_client_id).strip()
        and str(settings.entra_redirect_uri).strip()
        and str(settings.entra_authority).strip()
    )
    if settings.auth_enabled and settings.entra_external_id_enabled and not entra_ready:
        logger.error(
            "ENTRA_EXTERNAL_ID_ENABLED is set but Entra is incomplete; "
            "Microsoft login stays disabled",
            extra={
                "event": "auth.entra.incomplete_config",
                "tenant_configured": bool(str(settings.entra_tenant_id).strip()),
                "client_id_configured": bool(str(settings.entra_client_id).strip()),
                "redirect_configured": bool(str(settings.entra_redirect_uri).strip()),
                "authority_configured": bool(str(settings.entra_authority).strip()),
                "local_auth_enabled": bool(settings.local_auth_enabled),
            },
        )
    elif settings.auth_enabled and entra_ready and not str(settings.entra_client_secret).strip():
        # Visible at startup. A missing secret makes the token endpoint return
        # 401; this log does not claim a present secret is wrong (#299).
        logger.error(
            "ENTRA_CLIENT_SECRET is empty; Microsoft token exchange has no client secret",
            extra={
                "event": "auth.entra.secret_missing",
                "client_secret_configured": False,
                "entra_client_id": str(settings.entra_client_id).strip(),
            },
        )
    return soft


def _placeholder_capture_labels(
    *,
    portfolio_history_capture_token: str,
    rankings_capture_token: str,
    rankings_archive_capture_token: str,
    taiwan_market_data_capture_token: str,
) -> list[str]:
    values = {
        "PORTFOLIO_HISTORY_CAPTURE_TOKEN": portfolio_history_capture_token,
        "RANKINGS_CAPTURE_TOKEN": rankings_capture_token,
        "RANKINGS_ARCHIVE_CAPTURE_TOKEN": rankings_archive_capture_token,
        "TAIWAN_MARKET_DATA_CAPTURE_TOKEN": taiwan_market_data_capture_token,
    }
    return [label for label, token in values.items() if is_placeholder_capture_token(token)]
