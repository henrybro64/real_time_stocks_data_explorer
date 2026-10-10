"""Login stays compatible with passwords created before the register cap."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LocalLoginRequest, LocalRegisterRequest


def test_login_accepts_password_longer_than_register_maximum() -> None:
    password = "a" * 300

    login = LocalLoginRequest(username="alice", password=password)

    assert login.password == password
    with pytest.raises(ValidationError):
        LocalRegisterRequest(username="alice", password=password)
