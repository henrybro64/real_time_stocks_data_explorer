"""Lazy optional session factory for the rankings archive database.

Uses ``RANKINGS_ARCHIVE_DATABASE_URL`` only. Never reuses primary ``DATABASE_URL``.
Connection failures are contained so FastAPI can start without Azure SQL.

Failed connects are retried with short in-request backoff (Azure SQL Free AutoPause
cold starts), then a brief process cooldown before the next initialization wave.
"""

from __future__ import annotations

import logging
from threading import Lock
from time import monotonic, sleep

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.archive_models import ArchiveBase

logger = logging.getLogger(__name__)

# After exhausting in-request retries, wait before another init wave.
_INIT_RETRY_COOLDOWN_SECONDS = 10.0
# Bounded wake-up attempts for serverless AutoPause (#177).
_INIT_IN_REQUEST_ATTEMPTS = 3
_INIT_IN_REQUEST_BACKOFF_SECONDS = (2.0, 5.0, 8.0)

_lock = Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_UNSET = object()
_session_factory_override: sessionmaker[Session] | None | object = _UNSET
_schema_ready = False
_last_init_failure_at: float | None = None


def rankings_archive_database_url() -> str:
    return settings.rankings_archive_database_url.strip()


def set_rankings_archive_session_factory_override(
    session_factory: sessionmaker[Session] | None,
) -> None:
    """Test helper: force a session factory (or None) without reading env URL."""
    global _session_factory_override, _schema_ready
    with _lock:
        _session_factory_override = session_factory
        _schema_ready = False


def reset_rankings_archive_session_state() -> None:
    """Test helper: clear cached engine/session factory and overrides."""
    global _engine, _session_factory, _session_factory_override, _schema_ready
    global _last_init_failure_at
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _session_factory_override = _UNSET
        _schema_ready = False
        _last_init_failure_at = None


def rankings_archive_is_configured() -> bool:
    if _session_factory_override is not _UNSET:
        return _session_factory_override is not None
    return bool(rankings_archive_database_url())


def _try_create_engine(url: str) -> Engine:
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return engine


def _initialize_engine_locked() -> Engine | None:
    global _engine, _session_factory, _last_init_failure_at
    if _engine is not None:
        return _engine
    if _last_init_failure_at is not None:
        elapsed = monotonic() - _last_init_failure_at
        if elapsed < _INIT_RETRY_COOLDOWN_SECONDS:
            logger.info(
                "Rankings archive init skipped during cooldown",
                extra={
                    "event": "rankings_archive.engine.cooldown_skip",
                    "cooldown_remaining_seconds": round(
                        _INIT_RETRY_COOLDOWN_SECONDS - elapsed,
                        1,
                    ),
                },
            )
            return None
    url = rankings_archive_database_url()
    if not url:
        _engine = None
        _session_factory = None
        return None

    last_error: Exception | None = None
    for attempt in range(1, _INIT_IN_REQUEST_ATTEMPTS + 1):
        engine: Engine | None = None
        try:
            if attempt > 1:
                backoff = _INIT_IN_REQUEST_BACKOFF_SECONDS[
                    min(attempt - 2, len(_INIT_IN_REQUEST_BACKOFF_SECONDS) - 1)
                ]
                logger.info(
                    "Rankings archive cold-start retry",
                    extra={
                        "event": "rankings_archive.engine.cold_start_retry",
                        "attempt": attempt,
                        "backoff_seconds": backoff,
                    },
                )
                sleep(backoff)
            engine = _try_create_engine(url)
            _engine = engine
            _session_factory = sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
            )
            _last_init_failure_at = None
            logger.info(
                "Rankings archive engine ready",
                extra={
                    "event": "rankings_archive.engine.recovered"
                    if attempt > 1
                    else "rankings_archive.engine.ready",
                    "attempt": attempt,
                },
            )
            return _engine
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if engine is not None:
                engine.dispose()
            logger.warning(
                "Rankings archive connect attempt failed",
                extra={
                    "event": "rankings_archive.engine.connect_failed",
                    "attempt": attempt,
                    "max_attempts": _INIT_IN_REQUEST_ATTEMPTS,
                    "error_type": type(exc).__name__,
                },
            )

    logger.error(
        "Rankings archive database unavailable after cold-start retries",
        extra={
            "event": "rankings_archive.engine.unavailable_persistent",
            "attempts": _INIT_IN_REQUEST_ATTEMPTS,
            "error_type": type(last_error).__name__ if last_error else None,
        },
    )
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _last_init_failure_at = monotonic()
    return None


def get_rankings_archive_engine() -> Engine | None:
    if _session_factory_override is not _UNSET:
        return None
    with _lock:
        if _engine is not None:
            return _engine
        return _initialize_engine_locked()


def get_rankings_archive_session_factory() -> sessionmaker[Session] | None:
    if _session_factory_override is not _UNSET:
        return _session_factory_override  # type: ignore[return-value]
    with _lock:
        if _session_factory is not None:
            return _session_factory
        _initialize_engine_locked()
        return _session_factory


def ensure_rankings_archive_schema() -> bool:
    """Create archive tables if missing. Returns False when archive DB is unavailable."""
    global _schema_ready
    factory = get_rankings_archive_session_factory()
    if factory is None:
        return False
    with _lock:
        if _schema_ready:
            return True
        engine = _engine
        if engine is None and _session_factory_override is not _UNSET:
            # Override path: derive bind from a temporary session.
            with factory() as session:
                bind = session.get_bind()
                ArchiveBase.metadata.create_all(bind=bind)
                _ensure_archive_compat_columns(bind)
            _schema_ready = True
            return True
        if engine is None:
            return False
        try:
            ArchiveBase.metadata.create_all(bind=engine)
            _ensure_archive_compat_columns(engine)
            _schema_ready = True
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings archive schema ensure failed",
                extra={"event": "rankings_archive.schema.failed"},
            )
            return False


def _ensure_archive_compat_columns(bind: Engine) -> None:
    """Backfill columns for already-provisioned archive tables.

    ``create_all`` does not ALTER existing tables, so we apply additive
    compatibility updates explicitly.
    """
    inspector = inspect(bind)
    try:
        columns = {
            column["name"]
            for column in inspector.get_columns("ranking_snapshots_archive")
        }
    except Exception:  # noqa: BLE001
        # Table missing/metadata inaccessible: leave to create_all/normal errors.
        return
    if "tw_rankings_scope" in columns:
        return

    dialect = bind.dialect.name.lower()
    if dialect.startswith("mssql"):
        alter_sql = (
            "ALTER TABLE ranking_snapshots_archive "
            "ADD tw_rankings_scope NVARCHAR(32) NULL"
        )
    elif dialect.startswith("sqlite"):
        alter_sql = (
            "ALTER TABLE ranking_snapshots_archive "
            "ADD COLUMN tw_rankings_scope VARCHAR(32)"
        )
    else:
        alter_sql = (
            "ALTER TABLE ranking_snapshots_archive "
            "ADD COLUMN tw_rankings_scope VARCHAR(32) NULL"
        )

    with bind.begin() as connection:
        connection.execute(text(alter_sql))
