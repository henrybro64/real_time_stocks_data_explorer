"""Session auth endpoints (local username/password)."""

from __future__ import annotations

import secrets
from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.db.init_ids import DEFAULT_USER_ID
from app.schemas.auth import AuthStatusResponse, AuthUserPublic, LocalLoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _provider_flags() -> dict[str, bool]:
    return {
        "local": settings.local_auth_enabled,
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
    if settings.local_auth_enabled and str(uid) == DEFAULT_USER_ID:
        return AuthStatusResponse(
            authEnabled=True,
            loggedIn=True,
            providers=flags,
            user=AuthUserPublic(id=DEFAULT_USER_ID, username=settings.local_auth_username),
        )
    request.session.clear()
    return AuthStatusResponse(
        authEnabled=True,
        loggedIn=False,
        providers=flags,
        user=None,
    )


@router.post("/login/local")
def login_local(payload: LocalLoginRequest, request: Request) -> dict[str, str]:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not settings.local_auth_enabled:
        raise HTTPException(status_code=404, detail="Local auth is disabled")
    if not (
        secrets.compare_digest(payload.username, settings.local_auth_username)
        and secrets.compare_digest(payload.password, settings.local_auth_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user_id"] = DEFAULT_USER_ID
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"status": "ok"}
