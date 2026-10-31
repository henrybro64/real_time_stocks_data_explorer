"""Session auth endpoints (local username/password + optional Entra OIDC)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.api.deps import get_request_identity
from app.core.config import settings
from app.schemas.auth import (
    AuthStatusResponse,
    AuthUserPublic,
    LocalLoginRequest,
    LocalRegisterRequest,
)
from app.services.entra_auth_service import (
    PUBLIC_SIGNIN_FAILED,
    EntraAuthError,
    begin_login as begin_entra_login,
    complete_login as complete_entra_login,
    diagnose_authorize_error,
    entra_is_configured,
)
from app.services.local_auth_service import (
    get_user_by_id,
    register_local_user,
    verify_local_login,
)
from app.services.rate_limit_service import RateLimitExceededError

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_ENTRA_STATE_KEY = "entra_oauth_state"
_ENTRA_NONCE_KEY = "entra_oauth_nonce"
_ENTRA_VERIFIER_KEY = "entra_oauth_code_verifier"


def _provider_flags() -> dict[str, bool]:
    return {
        "local": settings.local_auth_enabled,
        "entra": entra_is_configured(),
    }


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(request: Request) -> AuthStatusResponse:
    flags = _provider_flags()
    identity = get_request_identity(request)

    if not identity.auth_enabled:
        return AuthStatusResponse(
            authEnabled=False,
            loggedIn=False,
            providers=flags,
            user=None,
        )

    if not identity.is_authenticated:
        return AuthStatusResponse(
            authEnabled=True,
            loggedIn=False,
            providers=flags,
            user=None,
        )

    user = get_user_by_id(identity.user_id)
    if user is None:
        request.session.clear()
        return AuthStatusResponse(
            authEnabled=True,
            loggedIn=False,
            providers=flags,
            user=None,
        )
    return AuthStatusResponse(
        authEnabled=True,
        loggedIn=True,
        providers=flags,
        user=AuthUserPublic(id=user["id"], username=user["username"]),
    )


def _client_ip(request: Request) -> str:
    """Client address for auth rate limits.

    ``TRUSTED_PROXY_HOPS`` is how many proxies append to ``X-Forwarded-For``.
    The client is that many entries from the right. A caller-supplied leftmost
    value cannot open a new bucket. With no usable header, the TCP peer is used.
    """
    hops = int(settings.trusted_proxy_hops)
    forwarded = request.headers.get("x-forwarded-for")
    if hops > 0 and forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _enforce_auth_rate_limit(request: Request, *, kind: str) -> None:
    from app.api.http_quota import raise_quota_http
    from app.services.rate_limit_service import require_rate_limit

    ip = _client_ip(request)
    try:
        if kind == "login":
            require_rate_limit(
                f"auth:login:{ip}",
                limit=int(settings.auth_login_rate_limit_per_minute),
                window_seconds=60,
                detail="Too many login attempts. Try again shortly.",
            )
        else:
            require_rate_limit(
                f"auth:register:{ip}",
                limit=int(settings.auth_register_rate_limit_per_hour),
                window_seconds=3600,
                detail="Too many registration attempts. Try again later.",
            )
    except RateLimitExceededError as exc:
        raise_quota_http(exc)


@router.post("/login/local")
def login_local(payload: LocalLoginRequest, request: Request) -> dict[str, str]:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not settings.local_auth_enabled:
        raise HTTPException(status_code=404, detail="Local auth is disabled")
    _enforce_auth_rate_limit(request, kind="login")
    uid = verify_local_login(payload.username.strip(), payload.password)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user_id"] = uid
    return {"status": "ok"}


@router.post("/register/local")
def register_local(payload: LocalRegisterRequest, request: Request) -> dict[str, str]:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not settings.local_auth_enabled:
        raise HTTPException(status_code=404, detail="Local auth is disabled")
    _enforce_auth_rate_limit(request, kind="register")
    try:
        uid = register_local_user(payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.session["user_id"] = uid
    return {"status": "ok"}


@router.get("/login/entra")
def login_entra(request: Request) -> RedirectResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not entra_is_configured():
        raise HTTPException(status_code=404, detail="Entra External ID is not configured")
    try:
        start = begin_entra_login()
    except EntraAuthError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    request.session[_ENTRA_STATE_KEY] = start.state
    request.session[_ENTRA_NONCE_KEY] = start.nonce
    request.session[_ENTRA_VERIFIER_KEY] = start.code_verifier
    return RedirectResponse(url=start.authorization_url, status_code=302)


@router.get("/callback/entra")
def callback_entra(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not entra_is_configured():
        raise HTTPException(status_code=404, detail="Entra External ID is not configured")
    if error:
        # Read AADSTS and correlation ids out of error_description, then drop it (#299).
        diagnostic = diagnose_authorize_error(error=error, error_description=error_description)
        logger.info(
            PUBLIC_SIGNIN_FAILED,
            extra={
                "event": "auth.entra.authorize_callback_error",
                **diagnostic,
            },
        )
        raise HTTPException(status_code=401, detail=PUBLIC_SIGNIN_FAILED)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing Entra authorization code or state")

    expected_state = request.session.pop(_ENTRA_STATE_KEY, None)
    expected_nonce = request.session.pop(_ENTRA_NONCE_KEY, None)
    code_verifier = request.session.pop(_ENTRA_VERIFIER_KEY, None)
    try:
        uid = complete_entra_login(
            code=code,
            state=state,
            expected_state=expected_state if isinstance(expected_state, str) else None,
            expected_nonce=expected_nonce if isinstance(expected_nonce, str) else None,
            code_verifier=code_verifier if isinstance(code_verifier, str) else None,
            client_ip=_client_ip(request),
        )
    except RateLimitExceededError as exc:
        from app.api.http_quota import raise_quota_http

        raise_quota_http(exc)
    except EntraAuthError as exc:
        raise HTTPException(status_code=401, detail=exc.detail) from exc

    request.session["user_id"] = uid
    return RedirectResponse(url="/", status_code=302)


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"status": "ok"}
