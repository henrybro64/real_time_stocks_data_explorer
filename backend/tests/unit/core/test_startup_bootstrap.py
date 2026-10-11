"""Startup bootstrap survives an unreachable database (#290)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.main as main_module

_PASSWORD = "bootstrap-pass-123"


def _db_down(*args, **kwargs):
    raise OperationalError("SELECT 1", {}, Exception("connection refused to db-host"))


def _schema_missing(*args, **kwargs):
    # Mirrors seed_default_user, which wraps driver errors in RuntimeError.
    try:
        raise ProgrammingError("SELECT 1", {}, Exception('relation "users" does not exist'))
    except ProgrammingError as exc:
        raise RuntimeError("Database schema is missing or incompatible.") from exc


def _seed_db_down(*args, **kwargs):
    try:
        _db_down()
    except OperationalError as exc:
        raise RuntimeError("Database schema is missing or incompatible.") from exc


@pytest.fixture
def local_auth(monkeypatch):
    monkeypatch.setattr(main_module.settings, "auth_enabled", True)
    monkeypatch.setattr(main_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(main_module.settings, "local_auth_username", "owner")
    monkeypatch.setattr(main_module.settings, "local_auth_password", _PASSWORD)


@pytest.fixture
def single_user(monkeypatch):
    monkeypatch.setattr(main_module.settings, "auth_enabled", False)
    monkeypatch.setattr(main_module, "SessionLocal", object())


def _records(caplog, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == event]


def test_local_auth_bootstrap_survives_unreachable_db(local_auth, monkeypatch, caplog) -> None:
    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", _db_down)

    with caplog.at_level(logging.ERROR):
        main_module.bootstrap_local_auth_user()

    [record] = _records(caplog, "app.bootstrap.local_auth_user_failed")
    assert record.error_type == "OperationalError"
    # Neither the password nor the driver message (host, user) reaches the log.
    assert _PASSWORD not in caplog.text
    assert "db-host" not in caplog.text


def test_default_user_seed_survives_unreachable_db(single_user, monkeypatch, caplog) -> None:
    monkeypatch.setattr(main_module, "seed_default_user", _seed_db_down)

    with caplog.at_level(logging.ERROR):
        main_module.bootstrap_single_user_principal()

    [record] = _records(caplog, "app.bootstrap.default_user_failed")
    assert record.error_type == "OperationalError"
    assert "db-host" not in caplog.text


def test_missing_schema_still_stops_startup(single_user, monkeypatch) -> None:
    # A forgotten `alembic upgrade head` must stay loud.
    monkeypatch.setattr(main_module, "seed_default_user", _schema_missing)
    with pytest.raises(RuntimeError, match="schema is missing"):
        main_module.bootstrap_single_user_principal()


@pytest.mark.parametrize(
    "error",
    [
        TypeError("bug"),
        ProgrammingError("SELECT 1", {}, Exception('relation "users" does not exist')),
    ],
)
def test_bugs_and_schema_errors_still_raise_from_local_auth(local_auth, monkeypatch, error) -> None:
    def _raise(*args, **kwargs):
        raise error

    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", _raise)
    with pytest.raises(type(error)):
        main_module.bootstrap_local_auth_user()


def test_api_starts_and_serves_health_with_db_down(local_auth, monkeypatch) -> None:
    monkeypatch.setattr(main_module.settings, "session_secret", "9f" * 16)
    monkeypatch.setattr(main_module.settings, "run_background_jobs", False)
    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", _db_down)

    with TestClient(main_module.app) as client:  # runs every startup hook
        assert client.get("/health").status_code == 200
