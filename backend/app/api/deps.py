from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.core.config import settings
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


def get_request_identity(request: Request) -> RequestIdentity:
    """Resolve the current request identity in single-user or session mode."""
    if not settings.auth_enabled:
        return RequestIdentity(
            user_id=DEFAULT_USER_ID,
            auth_enabled=False,
        )

    uid = request.session.get("user_id")
    if not uid:
        return RequestIdentity(user_id=None, auth_enabled=True)

    user_id = str(uid)
    if get_user_by_id(user_id) is None:
        request.session.clear()
        return RequestIdentity(user_id=None, auth_enabled=True)

    return RequestIdentity(user_id=user_id, auth_enabled=True)


def get_effective_user_id(request: Request) -> str:
    """Resolve the current user id for protected routes."""
    identity = get_request_identity(request)
    if identity.user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return identity.user_id
