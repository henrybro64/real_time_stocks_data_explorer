"""OAuth login/logout and auth status."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.schemas.auth import AuthStatusResponse, AuthUserPublic
from app.services import oauth_exchange
from app.services.oauth_accounts import find_or_create_oauth_user, get_user_public

router = APIRouter(prefix="/auth", tags=["auth"])


def _public_origin() -> str:
    return settings.public_base_url.rstrip("/")


def _callback_uri(provider: str) -> str:
    return f"{_public_origin()}{settings.api_prefix}/auth/callback/{provider}"


def _login_error_redirect() -> RedirectResponse:
    return RedirectResponse(
        url=f"{_public_origin()}/login?error=oauth",
        status_code=302,
    )


def _provider_flags() -> dict[str, bool]:
    return {
        "google": bool(settings.google_client_id and settings.google_client_secret),
        "microsoft": bool(
            settings.microsoft_client_id and settings.microsoft_client_secret,
        ),
        "apple": bool(
            settings.apple_client_id
            and settings.apple_team_id
            and settings.apple_key_id
            and settings.apple_private_key,
        ),
    }


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(request: Request) -> AuthStatusResponse:
    flags = _provider_flags()
    if not settings.auth_enabled:
        return AuthStatusResponse(
            authEnabled=False,
            loggedIn=False,
            providers=flags,
            user=None,
        )
    uid = request.session.get("user_id")
    if not uid:
        return AuthStatusResponse(
            authEnabled=True,
            loggedIn=False,
            providers=flags,
            user=None,
        )
    row = get_user_public(str(uid))
    if row is None:
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
        user=AuthUserPublic(id=str(uid), username=row["username"]),
    )


@router.get("/login/google")
def login_google(request: Request) -> RedirectResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not (settings.google_client_id and settings.google_client_secret):
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _callback_uri("google"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback/google")
def callback_google(request: Request, code: str | None = None, state: str | None = None):
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    saved = request.session.get("oauth_state")
    request.session.pop("oauth_state", None)
    if (
        not code
        or not state
        or not saved
        or not secrets.compare_digest(str(saved), str(state))
    ):
        return _login_error_redirect()
    try:
        sub, email = oauth_exchange.exchange_google(
            settings,
            code=code,
            redirect_uri=_callback_uri("google"),
        )
        uid = find_or_create_oauth_user("google", sub, email)
        request.session["user_id"] = uid
    except Exception:
        return _login_error_redirect()
    return RedirectResponse(url=f"{_public_origin()}/", status_code=302)


@router.get("/login/microsoft")
def login_microsoft(request: Request) -> RedirectResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not (settings.microsoft_client_id and settings.microsoft_client_secret):
        raise HTTPException(status_code=503, detail="Microsoft OAuth is not configured")
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    tenant = settings.microsoft_tenant.strip() or "consumers"
    params = {
        "client_id": settings.microsoft_client_id,
        "response_type": "code",
        "redirect_uri": _callback_uri("microsoft"),
        "response_mode": "query",
        "scope": "openid profile email offline_access",
        "state": state,
    }
    url = (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?"
        + urlencode(params)
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback/microsoft")
def callback_microsoft(
    request: Request,
    code: str | None = None,
    state: str | None = None,
):
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    saved = request.session.get("oauth_state")
    request.session.pop("oauth_state", None)
    if (
        not code
        or not state
        or not saved
        or not secrets.compare_digest(str(saved), str(state))
    ):
        return _login_error_redirect()
    try:
        sub, email = oauth_exchange.exchange_microsoft(
            settings,
            code=code,
            redirect_uri=_callback_uri("microsoft"),
        )
        uid = find_or_create_oauth_user("microsoft", sub, email)
        request.session["user_id"] = uid
    except Exception:
        return _login_error_redirect()
    return RedirectResponse(url=f"{_public_origin()}/", status_code=302)


@router.get("/login/apple")
def login_apple(request: Request) -> RedirectResponse:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not (
        settings.apple_client_id
        and settings.apple_team_id
        and settings.apple_key_id
        and settings.apple_private_key
    ):
        raise HTTPException(status_code=503, detail="Apple Sign In is not configured")
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.apple_client_id,
        "redirect_uri": _callback_uri("apple"),
        "response_type": "code",
        "scope": "name email",
        "response_mode": "query",
        "state": state,
    }
    url = "https://appleid.apple.com/auth/authorize?" + urlencode(params)
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback/apple")
def callback_apple(
    request: Request,
    code: str | None = None,
    state: str | None = None,
):
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    saved = request.session.get("oauth_state")
    request.session.pop("oauth_state", None)
    if (
        not code
        or not state
        or not saved
        or not secrets.compare_digest(str(saved), str(state))
    ):
        return _login_error_redirect()
    try:
        sub, email = oauth_exchange.exchange_apple(
            settings,
            code=code,
            redirect_uri=_callback_uri("apple"),
        )
        uid = find_or_create_oauth_user("apple", sub, email)
        request.session["user_id"] = uid
    except Exception:
        return _login_error_redirect()
    return RedirectResponse(url=f"{_public_origin()}/", status_code=302)


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"status": "ok"}
