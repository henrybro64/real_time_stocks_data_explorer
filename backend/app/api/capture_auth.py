"""Shared auth for internal capture endpoints (#267, #284)."""

from __future__ import annotations

import hmac

from fastapi import HTTPException

from app.core.auth_settings import is_placeholder_capture_token


def require_capture_token(
    provided: str | None,
    expected: str,
    *,
    not_configured_detail: str,
) -> None:
    """Require a configured capture token using a constant-time compare."""
    expected_token = expected.strip()
    # Placeholder values are treated as unset so they never authenticate (#284).
    if not expected_token or is_placeholder_capture_token(expected_token):
        raise HTTPException(status_code=503, detail=not_configured_detail)
    candidate = provided if isinstance(provided, str) else ""
    if not hmac.compare_digest(candidate, expected_token):
        raise HTTPException(status_code=403, detail="Forbidden")
