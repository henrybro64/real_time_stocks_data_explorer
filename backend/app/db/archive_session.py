"""Lazy optional session factory for the rankings archive database.

Uses ``RANKINGS_ARCHIVE_DATABASE_URL`` only. Never reuses primary ``DATABASE_URL``.
Connection failures are contained so FastAPI can start without Azure SQL.

Failed connects are retried after a short cooldown so Azure SQL Free-offer
AutoPause / cold starts do not stick the process in a permanent unavailable
state until restart.
"""

from __future__ import annotations

import logging
from threading import Lock
from time import monotonic

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.archive_models import ArchiveBase

logger = logging.getLogger(__name__)

# After a failed connect (including AutoPause wake timeouts), wait before retrying.
_INIT_RETRY_COOLDOWN_SECONDS = 30.0

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


def _initialize_engine_locked() -> Engine | None:
    global _engine, _session_factory, _last_init_failure_at
    if _engine is not None:
        return _engine
    if _last_init_failure_at is not None:
        elapsed = monotonic() - _last_init_failure_at
        if elapsed < _INIT_RETRY_COOLDOWN_SECONDS:
            return None
    url = rankings_archive_database_url()
    if not url:
        _engine = None
        _session_factory = None
        return None
    engine: Engine | None = None
    try:
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
        _engine = engine
        _session_factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        _last_init_failure_at = None
        return _engine
    except Exception:  # noqa: BLE001
        logger.exception(
            "Rankings archive database unavailable",
            extra={"event": "rankings_archive.engine.unavailable"},
        )
        # Dispose any partially constructed engine (create_engine succeeded, connect failed).
        if engine is not None:
            engine.dispose()
        if _engine is not None and _engine is not engine:
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
            _schema_ready = True
            return True
        if engine is None:
            return False
        try:
            ArchiveBase.metadata.create_all(bind=engine)
            _schema_ready = True
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings archive schema ensure failed",
                extra={"event": "rankings_archive.schema.failed"},
            )
            return False
