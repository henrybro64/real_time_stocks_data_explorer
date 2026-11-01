"""migrate-db waits a bounded time for Postgres before Alembic (#308)."""

from __future__ import annotations

import logging
import socket

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from scripts import wait_for_database as wait

_URL = "postgresql+psycopg://postgres:s3cret-pw@stock-ticker-postgres:5432/stocks?connect_timeout=15"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _refused(url, connect_timeout) -> None:
    raise OperationalError(
        "select 1",
        {},
        Exception(
            'connection to server at "stock-ticker-postgres" (10.43.0.5), port 5432 failed: '
            "Connection refused\n\tIs the server running on that host?"
        ),
    )


def _run(budget: float, probe, clock: _Clock, url: str = _URL) -> bool:
    return wait.wait_for_database(
        budget,
        database_url=url,
        probe=probe,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_returns_at_once_when_postgres_answers() -> None:
    clock = _Clock()
    assert _run(30, lambda url, connect_timeout: None, clock) is True
    assert clock.sleeps == []


def test_retries_with_backoff_until_postgres_answers() -> None:
    answers = iter([_refused, _refused, _refused, lambda url, connect_timeout: None])
    clock = _Clock()

    assert _run(300, lambda url, connect_timeout: next(answers)(url, connect_timeout), clock) is True
    assert clock.sleeps == [1.0, 2.0, 5.0]


def test_backoff_repeats_its_last_step() -> None:
    clock = _Clock()
    assert _run(40, _refused, clock) is False
    assert clock.sleeps == [1.0, 2.0, 5.0, 10.0, 10.0, 10.0, 2.0]


def test_gives_up_at_the_bound() -> None:
    clock = _Clock()
    assert _run(5, _refused, clock) is False
    assert clock.sleeps == [1.0, 2.0, 2.0]
    assert clock.now == 5.0


def test_zero_budget_makes_no_connection_attempt() -> None:
    calls: list[int] = []
    clock = _Clock()
    assert _run(0, lambda url, connect_timeout: calls.append(connect_timeout), clock) is False
    assert calls == []


def test_connect_timeout_is_capped_by_the_time_left() -> None:
    seen: list[int] = []
    clock = _Clock()

    def _slow(url, connect_timeout: int) -> None:
        seen.append(connect_timeout)
        clock.now += connect_timeout
        _refused(url, connect_timeout)

    assert _run(25, _slow, clock) is False
    assert seen[0] == 10  # capped at the per-attempt maximum
    assert all(2 <= value <= 10 for value in seen)  # libpq floor of 2
    # The libpq floor is the only overrun allowed.
    assert clock.now <= 25 + 2


def test_missing_url_fails_without_attempting() -> None:
    calls: list[int] = []
    clock = _Clock()
    assert _run(30, lambda url, connect_timeout: calls.append(1), clock, url="  ") is False
    assert calls == []


def test_unparseable_url_does_not_log_the_url(caplog) -> None:
    clock = _Clock()
    with caplog.at_level(logging.DEBUG):
        assert _run(30, _refused, clock, url="not a url :: s3cret-pw") is False
    assert "s3cret-pw" not in caplog.text
    assert any(getattr(r, "event", "") == "db.wait.invalid_url" for r in caplog.records)


def test_logs_are_actionable_and_never_carry_the_password(caplog) -> None:
    def _auth_failure(url, connect_timeout) -> None:
        # Defensive: even if a driver echoed the password, it must be masked.
        raise OperationalError("select 1", {}, Exception("password s3cret-pw rejected"))

    clock = _Clock()
    with caplog.at_level(logging.INFO):
        assert _run(3, _auth_failure, clock) is False

    events = [getattr(record, "event", "") for record in caplog.records]
    assert "db.wait.retry" in events
    assert events[-1] == "db.wait.timed_out"
    timed_out = caplog.records[-1]
    assert timed_out.target == "stock-ticker-postgres:5432/stocks"
    assert "docs/postgres-apparmor.md" in timed_out.hint
    assert "***" in timed_out.last_error
    for record in caplog.records:
        assert "s3cret-pw" not in record.getMessage()
        assert "s3cret-pw" not in str(record.__dict__)


def test_reason_keeps_only_the_first_driver_line() -> None:
    try:
        _refused(None, 2)
    except OperationalError as exc:
        reason = wait._reason(exc, None)
    assert reason.startswith("Exception: connection to server")
    assert "Is the server running" not in reason


def test_describe_target_has_no_credentials() -> None:
    assert wait.describe_target(make_url(_URL)) == "stock-ticker-postgres:5432/stocks"


def test_probe_runs_select_one_against_a_real_engine() -> None:
    # SQLite gets no connect_timeout argument; sqlite3.connect would reject it.
    wait.probe_database(make_url("sqlite://"), 2)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_probe_against_a_closed_postgres_port_raises_a_retried_error() -> None:
    pytest.importorskip("psycopg")
    url = make_url(f"postgresql+psycopg://postgres:pw@127.0.0.1:{_closed_port()}/stocks")
    with pytest.raises(OperationalError):
        wait.probe_database(url, 2)


def test_main_exit_status_follows_the_wait(monkeypatch) -> None:
    monkeypatch.setattr(wait, "configure_logging", lambda: None)
    monkeypatch.setattr(wait, "wait_for_database", lambda timeout: True)
    assert wait.main() == 0
    monkeypatch.setattr(wait, "wait_for_database", lambda timeout: False)
    assert wait.main() == 1


def test_main_uses_the_configured_budget(monkeypatch) -> None:
    seen: list[float] = []
    monkeypatch.setattr(wait, "configure_logging", lambda: None)
    monkeypatch.setattr(wait.settings, "database_wait_seconds", 42.0)
    monkeypatch.setattr(wait, "wait_for_database", lambda timeout: seen.append(timeout) or True)
    wait.main()
    assert seen == [42.0]


def test_default_credentials_keep_the_error_readable() -> None:
    # In-cluster default: user and password are both "postgres".
    message = 'connection to server at "stock-ticker-postgres" failed: password authentication failed for user "postgres"'
    exc = OperationalError("select 1", {}, Exception(message))
    assert wait._reason(exc, "postgres", "postgres") == f"Exception: {message}"
    # A real secret is still masked.
    exc = OperationalError("select 1", {}, Exception("bad password s3cret-pw"))
    assert wait._reason(exc, "s3cret-pw", "postgres") == "Exception: bad password ***"
