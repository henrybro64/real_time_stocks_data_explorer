from dataclasses import dataclass
from threading import Lock
import time

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.diagnostics import set_user_id
from app.db.init_ids import DEFAULT_USER_ID
from app.services.local_auth_service import get_user_by_id


@dataclass(frozen=True)
class RequestIdentity:
    """Resolved request identity after applying the auth feature flag."""

    user_id: str | None
    auth_enabled: bool

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


_USER_EXISTS_TTL_SECONDS = 60.0
_user_exists_until: dict[str, float] = {}
_user_exists_lock = Lock()


def clear_identity_cache_for_tests() -> None:
    with _user_exists_lock:
        _user_exists_until.clear()


def _user_still_exists(user_id: str) -> bool:
    """Cache a positive user lookup for a minute so every request is not a query."""
    now = time.monotonic()
    with _user_exists_lock:
        until = _user_exists_until.get(user_id)
        if until is not None and now < until:
            return True
    if get_user_by_id(user_id) is None:
        with _user_exists_lock:
            _user_exists_until.pop(user_id, None)
        return False
    with _user_exists_lock:
        _user_exists_until[user_id] = time.monotonic() + _USER_EXISTS_TTL_SECONDS
    return True


def identity_from_session(session: object | None) -> RequestIdentity:
    """Resolve identity from a session mapping. Safe for HTTP and WebSocket scopes."""
    if not settings.auth_enabled:
        set_user_id(DEFAULT_USER_ID)
        return RequestIdentity(
            user_id=DEFAULT_USER_ID,
            auth_enabled=False,
        )

    uid = session.get("user_id") if session is not None else None  # type: ignore[union-attr]
    if not uid:
        set_user_id(None)
        return RequestIdentity(user_id=None, auth_enabled=True)

    user_id = str(uid)
    if not _user_still_exists(user_id):
        clear = getattr(session, "clear", None)
        if callable(clear):
            clear()
        set_user_id(None)
        return RequestIdentity(user_id=None, auth_enabled=True)

    set_user_id(user_id)
    return RequestIdentity(user_id=user_id, auth_enabled=True)


def get_request_identity(request: Request) -> RequestIdentity:
    """Resolve the current request identity in single-user or session mode."""
    if not settings.auth_enabled:
        set_user_id(DEFAULT_USER_ID)
        request.state.user_id = DEFAULT_USER_ID
        return RequestIdentity(
            user_id=DEFAULT_USER_ID,
            auth_enabled=False,
        )
    identity = identity_from_session(request.session)
    request.state.user_id = identity.user_id
    return identity


def get_effective_user_id(request: Request) -> str:
    """Resolve the current user id for protected routes."""
    identity = get_request_identity(request)
    if identity.user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    _enforce_api_user_rate_limit(identity.user_id)
    return identity.user_id


def _enforce_api_user_rate_limit(user_id: str) -> None:
    """Soft per-user API throttle when auth is on (#273)."""
    limit = int(settings.api_user_rate_limit_per_minute)
    if limit <= 0 or not settings.auth_enabled:
        return
    from app.api.http_quota import raise_quota_http
    from app.services.rate_limit_service import RateLimitExceededError, require_rate_limit

    try:
        require_rate_limit(
            f"api:user:{user_id}",
            limit=limit,
            window_seconds=60,
            detail="Too many requests. Slow down and try again shortly.",
        )
    except RateLimitExceededError as exc:
        raise_quota_http(exc)
