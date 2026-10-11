"""Map quota / rate-limit exceptions to HTTP responses (#273)."""

from __future__ import annotations

from fastapi import HTTPException

from app.services.rate_limit_service import RateLimitExceededError
from app.services.user_quota_service import UserQuotaExceededError


def raise_quota_http(exc: Exception) -> None:
    if isinstance(exc, UserQuotaExceededError):
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    if isinstance(exc, RateLimitExceededError):
        raise HTTPException(
            status_code=429,
            detail=exc.detail,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
