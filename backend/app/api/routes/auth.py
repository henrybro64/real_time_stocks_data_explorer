"""Session auth endpoints (local username/password)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import get_request_identity
from app.core.config import settings
from app.schemas.auth import (
    AuthStatusResponse,
    AuthUserPublic,
    LocalLoginRequest,
    LocalRegisterRequest,
)
from app.services.local_auth_service import (
    get_user_by_id,
    register_local_user,
    verify_local_login,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _provider_flags() -> dict[str, bool]:
    return {
        "local": settings.local_auth_enabled,
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


@router.post("/login/local")
def login_local(payload: LocalLoginRequest, request: Request) -> dict[str, str]:
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Auth is disabled")
    if not settings.local_auth_enabled:
        raise HTTPException(status_code=404, detail="Local auth is disabled")
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
    try:
        uid = register_local_user(payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.session["user_id"] = uid
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"status": "ok"}
