"""Unit tests for lazy rankings archive session reconnect (#166)."""

from __future__ import annotations

from sqlalchemy import inspect, text

import app.db.archive_session as archive_session


def _sqlite_url(tmp_path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'archive.db'}"


def test_engine_retries_after_init_failure_cooldown(monkeypatch, tmp_path) -> None:
    archive_session.reset_rankings_archive_session_state()
    monkeypatch.setattr(archive_session, "_INIT_RETRY_COOLDOWN_SECONDS", 0.0)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_ATTEMPTS", 1)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_BACKOFF_SECONDS", ())
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        "sqlite+pysqlite:////nonexistent/path/should_fail.db",
    )

    assert archive_session.get_rankings_archive_session_factory() is None
    assert archive_session._last_init_failure_at is not None

    # After cooldown, a valid URL should reconnect instead of sticking unavailable.
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        _sqlite_url(tmp_path),
    )
    factory = archive_session.get_rankings_archive_session_factory()
    assert factory is not None
    with factory() as session:
        session.execute(text("SELECT 1"))
    assert archive_session._last_init_failure_at is None
    archive_session.reset_rankings_archive_session_state()


def test_engine_respects_retry_cooldown(monkeypatch, tmp_path) -> None:
    archive_session.reset_rankings_archive_session_state()
    monkeypatch.setattr(archive_session, "_INIT_RETRY_COOLDOWN_SECONDS", 60.0)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_ATTEMPTS", 1)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_BACKOFF_SECONDS", ())
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        "sqlite+pysqlite:////nonexistent/path/should_fail.db",
    )
    assert archive_session.get_rankings_archive_session_factory() is None

    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        _sqlite_url(tmp_path),
    )
    # Still within cooldown — must not reconnect yet.
    assert archive_session.get_rankings_archive_session_factory() is None
    archive_session.reset_rankings_archive_session_state()


def test_engine_cold_start_retries_within_request(monkeypatch, tmp_path) -> None:
    """First connect failures wake AutoPause via bounded in-request retries (#177)."""
    archive_session.reset_rankings_archive_session_state()
    monkeypatch.setattr(archive_session, "_INIT_RETRY_COOLDOWN_SECONDS", 60.0)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_ATTEMPTS", 3)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_BACKOFF_SECONDS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        _sqlite_url(tmp_path),
    )

    attempts = {"count": 0}
    real_try = archive_session._try_create_engine

    def flaky_try(url: str):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("Azure SQL cold start")
        return real_try(url)

    monkeypatch.setattr(archive_session, "_try_create_engine", flaky_try)
    monkeypatch.setattr(archive_session, "sleep", lambda _seconds: None)

    factory = archive_session.get_rankings_archive_session_factory()
    assert factory is not None
    assert attempts["count"] == 3
    assert archive_session._last_init_failure_at is None
    archive_session.reset_rankings_archive_session_state()


def test_engine_cold_start_exhaustion_sets_cooldown(monkeypatch) -> None:
    archive_session.reset_rankings_archive_session_state()
    monkeypatch.setattr(archive_session, "_INIT_RETRY_COOLDOWN_SECONDS", 60.0)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_ATTEMPTS", 2)
    monkeypatch.setattr(archive_session, "_INIT_IN_REQUEST_BACKOFF_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        "sqlite+pysqlite:////nonexistent/path/should_fail.db",
    )
    monkeypatch.setattr(archive_session, "sleep", lambda _seconds: None)

    assert archive_session.get_rankings_archive_session_factory() is None
    assert archive_session._last_init_failure_at is not None
    # Second call during cooldown must not re-attempt connect.
    assert archive_session.get_rankings_archive_session_factory() is None
    archive_session.reset_rankings_archive_session_state()


def test_successful_engine_is_cached(monkeypatch, tmp_path) -> None:
    archive_session.reset_rankings_archive_session_state()
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        _sqlite_url(tmp_path),
    )
    first = archive_session.get_rankings_archive_engine()
    second = archive_session.get_rankings_archive_engine()
    assert first is not None
    assert first is second
    archive_session.reset_rankings_archive_session_state()


def test_ensure_schema_adds_tw_rankings_scope_for_legacy_tables(monkeypatch, tmp_path) -> None:
    archive_session.reset_rankings_archive_session_state()
    monkeypatch.setattr(
        "app.core.config.settings.rankings_archive_database_url",
        _sqlite_url(tmp_path),
    )
    factory = archive_session.get_rankings_archive_session_factory()
    assert factory is not None

    with factory() as session:
        session.execute(text("DROP TABLE IF EXISTS ranking_snapshot_items_archive"))
        session.execute(text("DROP TABLE IF EXISTS ranking_snapshots_archive"))
        session.execute(
            text(
                """
                CREATE TABLE ranking_snapshots_archive (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  market VARCHAR(8) NOT NULL,
                  universe VARCHAR(16) NOT NULL,
                  ranking_type VARCHAR(32) NOT NULL,
                  trading_date DATE NOT NULL,
                  captured_at DATETIME NOT NULL,
                  source TEXT NOT NULL,
                  source_type VARCHAR(32) NOT NULL,
                  source_freshness TEXT NOT NULL,
                  is_market_open BOOLEAN NOT NULL,
                  warning_message TEXT NULL,
                  unsupported_reason TEXT NULL,
                  capture_scope VARCHAR(32) NOT NULL DEFAULT 'end_of_day',
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        session.commit()

    assert archive_session.ensure_rankings_archive_schema() is True
    engine = archive_session.get_rankings_archive_engine()
    assert engine is not None
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("ranking_snapshots_archive")
    }
    assert "tw_rankings_scope" in columns
    archive_session.reset_rankings_archive_session_state()
