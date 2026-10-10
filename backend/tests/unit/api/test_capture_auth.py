"""Constant-time capture token checks (#267)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.capture_auth import require_capture_token


def test_missing_configuration_is_unavailable() -> None:
    with pytest.raises(HTTPException) as exc:
        require_capture_token(None, "", not_configured_detail="not configured")
    assert exc.value.status_code == 503


def test_wrong_token_is_forbidden() -> None:
    with pytest.raises(HTTPException) as exc:
        require_capture_token("nope", "secret-token", not_configured_detail="x")
    assert exc.value.status_code == 403


def test_matching_token_passes() -> None:
    require_capture_token("secret-token", "secret-token", not_configured_detail="x")
