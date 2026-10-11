"""Microsoft Entra External ID OIDC helper (#240).

Uses authorization-code + ID token validation (PyJWT + JWKS). No client secret
is required for public SPA-style confidential clients when a secret is
configured; the secret is only used at the token endpoint when present.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import settings
from app.repositories.oauth_identity_repository import oauth_identity_repository
from app.services.rate_limit_service import require_rate_limit

logger = logging.getLogger(__name__)

ENTRA_PROVIDER = "entra_external_id"


class EntraAuthError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class EntraLoginStart:
    authorization_url: str
    state: str
    nonce: str


def entra_is_configured() -> bool:
    return bool(
        settings.entra_external_id_enabled
        and settings.entra_tenant_id.strip()
        and settings.entra_client_id.strip()
        and settings.entra_redirect_uri.strip()
        and _authority().strip()
    )


def _authority() -> str:
    configured = settings.entra_authority.strip().rstrip("/")
    if configured:
        return configured
    tenant = settings.entra_tenant_id.strip()
    if not tenant:
        return ""
    return f"https://login.microsoftonline.com/{tenant}/v2.0"


def _metadata_url() -> str:
    return f"{_authority()}/.well-known/openid-configuration"


def fetch_oidc_metadata(*, http_get=None) -> dict[str, Any]:
    getter = http_get or httpx.get
    response = getter(_metadata_url(), timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise EntraAuthError("Invalid Entra OIDC metadata")
    return payload


def begin_login(*, http_get=None) -> EntraLoginStart:
    if not entra_is_configured():
        raise EntraAuthError("Entra External ID is not configured")
    metadata = fetch_oidc_metadata(http_get=http_get)
    authorize = metadata.get("authorization_endpoint")
    if not isinstance(authorize, str) or not authorize:
        raise EntraAuthError("Entra metadata missing authorization_endpoint")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.entra_client_id.strip(),
        "response_type": "code",
        "redirect_uri": settings.entra_redirect_uri.strip(),
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
    }
    return EntraLoginStart(
        authorization_url=f"{authorize}?{urlencode(params)}",
        state=state,
        nonce=nonce,
    )


def complete_login(
    *,
    code: str,
    state: str,
    expected_state: str | None,
    expected_nonce: str | None,
    client_ip: str,
    http_post=None,
    http_get=None,
    jwks_client_factory=None,
) -> str:
    """Exchange the auth code, validate the ID token, return local users.id."""
    if not entra_is_configured():
        raise EntraAuthError("Entra External ID is not configured")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        raise EntraAuthError("Invalid OAuth state")
    if not expected_nonce:
        raise EntraAuthError("Missing OAuth nonce")
    if not code.strip():
        raise EntraAuthError("Missing authorization code")

    require_rate_limit(
        f"auth:login:{client_ip}",
        limit=int(settings.auth_login_rate_limit_per_minute),
        window_seconds=60,
        detail="Too many login attempts. Try again shortly.",
    )

    metadata = fetch_oidc_metadata(http_get=http_get)
    token_endpoint = metadata.get("token_endpoint")
    jwks_uri = metadata.get("jwks_uri")
    issuer = metadata.get("issuer")
    if not isinstance(token_endpoint, str) or not token_endpoint:
        raise EntraAuthError("Entra metadata missing token_endpoint")
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise EntraAuthError("Entra metadata missing jwks_uri")
    if not isinstance(issuer, str) or not issuer:
        raise EntraAuthError("Entra metadata missing issuer")

    form = {
        "grant_type": "authorization_code",
        "client_id": settings.entra_client_id.strip(),
        "code": code,
        "redirect_uri": settings.entra_redirect_uri.strip(),
    }
    secret = settings.entra_client_secret.strip()
    if secret:
        form["client_secret"] = secret

    poster = http_post or httpx.post
    token_response = poster(token_endpoint, data=form, timeout=15.0)
    if token_response.status_code >= 400:
        raise EntraAuthError("Entra token exchange failed")
    token_payload = token_response.json()
    id_token = token_payload.get("id_token") if isinstance(token_payload, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise EntraAuthError("Entra response missing id_token")

    factory = jwks_client_factory or PyJWKClient
    jwks_client = factory(jwks_uri)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    try:
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.entra_client_id.strip(),
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise EntraAuthError("Invalid or expired Entra ID token") from exc

    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
        raise EntraAuthError("Invalid OAuth nonce")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise EntraAuthError("Entra ID token missing subject")

    if not oauth_identity_repository.is_available():
        raise EntraAuthError("DATABASE_URL is not configured")

    existing = oauth_identity_repository.get_user_id(
        provider=ENTRA_PROVIDER,
        subject=subject,
    )
    if existing is not None:
        return existing

    # First-time Entra sign-in creates a local users.id row (#240 / #273).
    require_rate_limit(
        f"auth:register:{client_ip}",
        limit=int(settings.auth_register_rate_limit_per_hour),
        window_seconds=3600,
        detail="Too many registration attempts. Try again later.",
    )

    preferred = claims.get("preferred_username") or claims.get("email") or subject
    username = _unique_username(str(preferred))
    user_id = f"u-{uuid.uuid4().hex[:16]}"
    return oauth_identity_repository.create_user_with_identity(
        user_id=user_id,
        username=username,
        provider=ENTRA_PROVIDER,
        subject=subject,
    )


def _unique_username(raw: str) -> str:
    base = "".join(ch for ch in raw.strip().lower() if ch.isalnum() or ch in {".", "_", "-"})
    base = (base or "entra-user")[:48]
    candidate = base
    from app.repositories.user_repository import user_repository

    suffix = 0
    while user_repository.get_by_username(candidate) is not None:
        suffix += 1
        candidate = f"{base}-{suffix}"[:64]
    return candidate
