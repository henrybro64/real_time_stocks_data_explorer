"""Shared auth for internal capture endpoints (#267)."""

from __future__ import annotations

import hmac

from fastapi import HTTPException


def require_capture_token(
    provided: str | None,
    expected: str,
    *,
    not_configured_detail: str,
) -> None:
    """Require a configured capture token using a constant-time compare."""
    expected_token = expected.strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail=not_configured_detail)
    candidate = provided if isinstance(provided, str) else ""
    if not hmac.compare_digest(candidate, expected_token):
        raise HTTPException(status_code=403, detail="Forbidden")
