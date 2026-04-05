from fastapi import HTTPException, Request

from app.core.config import settings
from app.db.init_ids import DEFAULT_USER_ID


def get_effective_user_id(request: Request) -> str:
    """Resolve the current user for protected routes."""
    if not settings.auth_enabled:
        return DEFAULT_USER_ID
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(uid)
