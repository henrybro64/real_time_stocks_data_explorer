"""Microsoft Entra External ID OIDC helper (#240, #299).

Uses authorization-code + PKCE + ID token validation (PyJWT + JWKS). The
client secret is sent at the token endpoint only when configured. Token-exchange
failures are logged with allowlisted OAuth fields and AADSTS codes; secrets,
codes, tokens, and Microsoft error descriptions never go to logs or the browser.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import settings
from app.repositories.oauth_identity_repository import (
    OAuthIdentityPersistError,
    oauth_identity_repository,
)
from app.services.rate_limit_service import require_rate_limit

logger = logging.getLogger(__name__)

ENTRA_PROVIDER = "entra_external_id"
_OAUTH_ERROR_ALLOWLIST = frozenset(
    {
        "access_denied",
        "consent_required",
        "interaction_required",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "login_required",
        "server_error",
        "temporarily_unavailable",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)
_AADSTS_RE = re.compile(r"AADSTS(\d{4,10})", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")
PUBLIC_TOKEN_EXCHANGE_FAILED = "Entra token exchange failed"
PUBLIC_SIGNIN_FAILED = "Entra sign-in failed"


class EntraAuthError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class EntraLoginStart:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str


def entra_is_configured() -> bool:
    return bool(
        settings.entra_external_id_enabled
        and settings.entra_tenant_id.strip()
        and settings.entra_client_id.strip()
        and settings.entra_redirect_uri.strip()
        and _authority().strip()
    )


def _authority() -> str:
    """External ID authority. Workforce login.microsoftonline.com is not valid here."""
    return settings.entra_authority.strip().rstrip("/")


def _metadata_url() -> str:
    return f"{_authority()}/.well-known/openid-configuration"


def allowlisted_oauth_error(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    if cleaned in _OAUTH_ERROR_ALLOWLIST:
        return cleaned
    return None


def extract_aadsts_code(*candidates: object) -> str | None:
    """Return ``AADSTS<digits>`` from allowlisted numeric codes, never descriptions."""
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            return f"AADSTS{candidate}"
        if isinstance(candidate, list):
            for item in candidate:
                found = extract_aadsts_code(item)
                if found:
                    return found
            continue
        if isinstance(candidate, str):
            match = _AADSTS_RE.search(candidate)
            if match:
                return f"AADSTS{match.group(1)}"
    return None


def _safe_correlation_id(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()[:64]
    if not cleaned or not _SAFE_ID_RE.match(cleaned):
        return None
    return cleaned


_TRACE_ID_RE = re.compile(r"Trace ID:\s*([A-Za-z0-9-]{1,64})", re.IGNORECASE)
_CORRELATION_ID_RE = re.compile(r"Correlation ID:\s*([A-Za-z0-9-]{1,64})", re.IGNORECASE)


def diagnose_authorize_error(*, error: object, error_description: object) -> dict[str, str | None]:
    """Allowlisted fields from an authorize-redirect error. The description is not returned."""
    description = error_description if isinstance(error_description, str) else ""
    trace = _TRACE_ID_RE.search(description)
    correlation = _CORRELATION_ID_RE.search(description)
    return {
        "oauth_error": allowlisted_oauth_error(error),
        "aadsts_code": extract_aadsts_code(description),
        "entra_trace_id": _safe_correlation_id(trace.group(1)) if trace else None,
        "entra_correlation_id": _safe_correlation_id(correlation.group(1)) if correlation else None,
    }


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _redirect_uri_host() -> str:
    parsed = urlparse(settings.entra_redirect_uri.strip())
    return parsed.netloc[:128]


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
    code_verifier, code_challenge = _pkce_pair()
    params = {
        "client_id": settings.entra_client_id.strip(),
        "response_type": "code",
        "redirect_uri": settings.entra_redirect_uri.strip(),
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return EntraLoginStart(
        authorization_url=f"{authorize}?{urlencode(params)}",
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
    )


def complete_login(
    *,
    code: str,
    state: str,
    expected_state: str | None,
    expected_nonce: str | None,
    client_ip: str,
    code_verifier: str | None = None,
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
    if not isinstance(code_verifier, str) or not code_verifier.strip():
        raise EntraAuthError("Missing PKCE verifier")
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

    secret = settings.entra_client_secret.strip()
    form = {
        "grant_type": "authorization_code",
        "client_id": settings.entra_client_id.strip(),
        "code": code,
        "redirect_uri": settings.entra_redirect_uri.strip(),
        "code_verifier": code_verifier.strip(),
        "scope": "openid profile email",
    }
    if secret:
        form["client_secret"] = secret

    poster = http_post or httpx.post
    try:
        token_response = poster(
            token_endpoint,
            data=form,
            timeout=15.0,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.TimeoutException:
        _log_token_exchange_failure(
            http_status=None,
            oauth_error="timeout",
            aadsts_code=None,
            entra_correlation_id=None,
            entra_trace_id=None,
            entra_request_id=None,
            client_secret_configured=bool(secret),
        )
        raise EntraAuthError(PUBLIC_TOKEN_EXCHANGE_FAILED) from None
    except httpx.RequestError:
        _log_token_exchange_failure(
            http_status=None,
            oauth_error="network_error",
            aadsts_code=None,
            entra_correlation_id=None,
            entra_trace_id=None,
            entra_request_id=None,
            client_secret_configured=bool(secret),
        )
        raise EntraAuthError(PUBLIC_TOKEN_EXCHANGE_FAILED) from None

    if token_response.status_code >= 400:
        _raise_from_token_response(token_response, client_secret_configured=bool(secret))

    try:
        token_payload = token_response.json()
    except ValueError:
        _log_token_exchange_failure(
            http_status=int(token_response.status_code),
            oauth_error="malformed_response",
            aadsts_code=None,
            entra_correlation_id=_header_id(token_response, "x-ms-request-id"),
            entra_trace_id=None,
            entra_request_id=_header_id(token_response, "client-request-id"),
            client_secret_configured=bool(secret),
        )
        raise EntraAuthError(PUBLIC_TOKEN_EXCHANGE_FAILED) from None
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

    # First-time Entra sign-in creates a new users.id; never merge onto local accounts (#299).
    require_rate_limit(
        f"auth:register:{client_ip}",
        limit=int(settings.auth_register_rate_limit_per_hour),
        window_seconds=3600,
        detail="Too many registration attempts. Try again later.",
    )

    preferred = claims.get("preferred_username") or claims.get("email") or subject
    username = _unique_username(str(preferred))
    user_id = f"u-{uuid.uuid4().hex[:16]}"
    try:
        return oauth_identity_repository.create_user_with_identity(
            user_id=user_id,
            username=username,
            provider=ENTRA_PROVIDER,
            subject=subject,
        )
    except OAuthIdentityPersistError:
        raise EntraAuthError(PUBLIC_SIGNIN_FAILED) from None


def diagnose_token_error_response(response: object) -> dict[str, str | int | bool | None]:
    """Allowlisted token-error fields for logs. Never includes descriptions or bodies."""
    status = getattr(response, "status_code", None)
    http_status = int(status) if isinstance(status, int) else None
    payload: dict[str, Any] | None = None
    try:
        parsed = response.json()  # type: ignore[union-attr]
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:  # noqa: BLE001 - malformed body is itself a diagnostic
        payload = None

    oauth_error = allowlisted_oauth_error((payload or {}).get("error"))
    if oauth_error is None and payload is None:
        oauth_error = "malformed_response"

    description = ""
    if payload is not None and isinstance(payload.get("error_description"), str):
        description = payload["error_description"]
    aadsts = extract_aadsts_code(
        (payload or {}).get("error_codes"),
        (payload or {}).get("error"),
    )
    # Parse AADSTS and ids out of error_description in memory; never log it.
    if aadsts is None:
        aadsts = extract_aadsts_code(description)
    correlation = _safe_correlation_id((payload or {}).get("correlation_id"))
    trace = _safe_correlation_id((payload or {}).get("trace_id"))
    if correlation is None:
        match = _CORRELATION_ID_RE.search(description)
        correlation = _safe_correlation_id(match.group(1)) if match else None
    if trace is None:
        match = _TRACE_ID_RE.search(description)
        trace = _safe_correlation_id(match.group(1)) if match else None

    return {
        "http_status": http_status,
        "oauth_error": oauth_error,
        "aadsts_code": aadsts,
        "entra_correlation_id": correlation,
        "entra_trace_id": trace,
        "entra_request_id": _header_id(response, "x-ms-request-id")
        or _safe_correlation_id((payload or {}).get("request_id")),
        "redirect_uri_host": _redirect_uri_host(),
        "entra_client_id": settings.entra_client_id.strip(),
        "client_secret_configured": bool(settings.entra_client_secret.strip()),
    }


def _header_id(response: object, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get(name)
    except Exception:  # noqa: BLE001
        return None
    return _safe_correlation_id(raw)


def _log_token_exchange_failure(
    *,
    http_status: int | None,
    oauth_error: str | None,
    aadsts_code: str | None,
    entra_correlation_id: str | None,
    entra_trace_id: str | None,
    entra_request_id: str | None,
    client_secret_configured: bool,
) -> None:
    logger.error(
        PUBLIC_TOKEN_EXCHANGE_FAILED,
        extra={
            "event": "auth.entra.token_exchange_failed",
            "http_status": http_status,
            "oauth_error": oauth_error,
            "aadsts_code": aadsts_code,
            "entra_correlation_id": entra_correlation_id,
            "entra_trace_id": entra_trace_id,
            "entra_request_id": entra_request_id,
            "redirect_uri_host": _redirect_uri_host(),
            "entra_client_id": settings.entra_client_id.strip(),
            "client_secret_configured": client_secret_configured,
        },
    )


def _raise_from_token_response(response: object, *, client_secret_configured: bool) -> None:
    diagnostic = diagnose_token_error_response(response)
    _log_token_exchange_failure(
        http_status=diagnostic["http_status"] if isinstance(diagnostic["http_status"], int) else None,
        oauth_error=diagnostic["oauth_error"] if isinstance(diagnostic["oauth_error"], str) else None,
        aadsts_code=diagnostic["aadsts_code"] if isinstance(diagnostic["aadsts_code"], str) else None,
        entra_correlation_id=diagnostic["entra_correlation_id"]
        if isinstance(diagnostic["entra_correlation_id"], str)
        else None,
        entra_trace_id=diagnostic["entra_trace_id"]
        if isinstance(diagnostic["entra_trace_id"], str)
        else None,
        entra_request_id=diagnostic["entra_request_id"]
        if isinstance(diagnostic["entra_request_id"], str)
        else None,
        client_secret_configured=client_secret_configured,
    )
    raise EntraAuthError(PUBLIC_TOKEN_EXCHANGE_FAILED)


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
