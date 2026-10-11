"""Startup bootstrap survives an unreachable database and retries (#290)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError, ProgrammingError

import app.main as main_module

_PASSWORD = "bootstrap-pass-123"


def _connection_refused() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("connection refused to db-host"))


def _db_down(*args, **kwargs):
    raise _connection_refused()


def _seed_db_down(*args, **kwargs):
    # seed_default_user wraps driver errors in RuntimeError.
    try:
        _db_down()
    except OperationalError as exc:
        raise RuntimeError("Database schema is missing or incompatible.") from exc


def _schema_missing(*args, **kwargs):
    try:
        raise ProgrammingError("SELECT 1", {}, Exception('relation "users" does not exist'))
    except ProgrammingError as exc:
        raise RuntimeError("Database schema is missing or incompatible.") from exc


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Short retry delays; stop and join every retry thread before patches are undone."""
    monkeypatch.setattr(main_module, "_BOOTSTRAP_RETRY_DELAYS_SECONDS", (0.01,))
    main_module._bootstrap_stop.clear()
    main_module._bootstrap_retry_threads.clear()
    yield
    main_module._bootstrap_stop.set()
    for thread in main_module._bootstrap_retry_threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    main_module._bootstrap_retry_threads.clear()


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


def _wait_for_retries() -> None:
    for thread in list(main_module._bootstrap_retry_threads):
        thread.join(timeout=5)
        assert not thread.is_alive()


def _fail_then_succeed(failures: int, error_factory):
    calls = {"count": 0}

    def _step(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] <= failures:
            error_factory()
        return "created"

    return _step, calls


def test_local_auth_bootstrap_survives_unreachable_db(local_auth, monkeypatch, caplog) -> None:
    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", _db_down)

    with caplog.at_level(logging.ERROR):
        main_module.bootstrap_local_auth_user()

    record = _records(caplog, "app.bootstrap.local_auth_user_failed")[0]
    assert record.error_type == "OperationalError"
    # Neither the password nor the driver message (host, user) reaches the log.
    assert _PASSWORD not in caplog.text
    assert "db-host" not in caplog.text


def test_local_auth_bootstrap_retries_until_the_db_returns(
    local_auth, monkeypatch, caplog
) -> None:
    step, calls = _fail_then_succeed(2, _db_down)
    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", step)

    with caplog.at_level(logging.INFO):
        main_module.bootstrap_local_auth_user()
        _wait_for_retries()

    assert calls["count"] == 3
    assert len(_records(caplog, "app.bootstrap.local_auth_user_failed")) == 2
    assert len(_records(caplog, "app.bootstrap.local_auth_user_ready")) == 1


def test_default_user_seed_retries_so_writes_work_again(single_user, monkeypatch, caplog) -> None:
    # With auth off every write is FK-bound to default-user, so the seed must land.
    step, calls = _fail_then_succeed(1, _seed_db_down)
    monkeypatch.setattr(main_module, "seed_default_user", step)

    with caplog.at_level(logging.INFO):
        main_module.bootstrap_single_user_principal()
        _wait_for_retries()

    assert calls["count"] == 2
    [failed] = _records(caplog, "app.bootstrap.default_user_failed")
    assert failed.error_type == "OperationalError"
    assert "db-host" not in caplog.text
    assert len(_records(caplog, "app.bootstrap.default_user_seeded")) == 1


def test_retry_stops_on_a_non_connection_error(local_auth, monkeypatch, caplog) -> None:
    calls = {"count": 0}

    def _step(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            _db_down()
        raise TypeError("bug")

    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", _step)

    with caplog.at_level(logging.ERROR):
        main_module.bootstrap_local_auth_user()
        _wait_for_retries()

    assert calls["count"] == 2
    [stopped] = _records(caplog, "app.bootstrap.retry_stopped")
    assert stopped.error_type == "TypeError"


def test_shutdown_stops_a_pending_retry(local_auth, monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_BOOTSTRAP_RETRY_DELAYS_SECONDS", (60.0,))
    calls = {"count": 0}

    def _step(*args, **kwargs):
        calls["count"] += 1
        _db_down()

    monkeypatch.setattr(main_module, "ensure_bootstrap_local_user", _step)
    main_module.bootstrap_local_auth_user()
    main_module.stop_bootstrap_retries()
    _wait_for_retries()

    assert calls["count"] == 1


def test_missing_schema_still_stops_startup(single_user, monkeypatch) -> None:
    # A forgotten `alembic upgrade head` must stay loud.
    monkeypatch.setattr(main_module, "seed_default_user", _schema_missing)
    with pytest.raises(RuntimeError, match="schema is missing"):
        main_module.bootstrap_single_user_principal()
    assert main_module._bootstrap_retry_threads == []


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

    with TestClient(main_module.app) as client:  # runs every startup and shutdown hook
        assert client.get("/health").status_code == 200
    _wait_for_retries()  # shutdown stopped the retry
