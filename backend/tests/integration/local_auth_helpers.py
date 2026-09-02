from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


def enable_local_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "local_auth_enabled", True)
    monkeypatch.setattr(settings, "local_auth_username", "")
    monkeypatch.setattr(settings, "local_auth_password", "")


def register_local_user(
    client: TestClient,
    username: str,
    password: str = "password-123",
) -> None:
    response = client.post(
        "/api/auth/register/local",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
