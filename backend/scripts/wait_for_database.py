"""Wait for Postgres before the migrate-db init container runs Alembic (#308).

After a node or k3s restart the backend pod can start before Postgres accepts
connections. ``alembic upgrade head`` then fails at once, and kubelet's
init-container backoff grows to five minutes between tries. This waits with a
short, bounded backoff and logs where it is connecting and why it failed, so a
stuck Postgres is visible in ``kubectl logs ... -c migrate-db``.

Exit status is 0 once ``select 1`` succeeds, 1 when the budget runs out.
The password in ``DATABASE_URL`` is never logged.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from collections.abc import Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

# Sleep between attempts; the last value repeats.
_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0)
# libpq treats a connect_timeout below 2 as 2.
_MIN_CONNECT_TIMEOUT_SECONDS = 2
_MAX_CONNECT_TIMEOUT_SECONDS = 10
_RUNBOOK = "docs/postgres-apparmor.md"


def describe_target(url: URL) -> str:
    """``host:port/database`` without credentials."""
    return f"{url.host or 'localhost'}:{url.port or 5432}/{url.database or ''}"


def probe_database(url: URL, connect_timeout: int) -> None:
    """Open one connection and run ``select 1``. Raises on failure."""
    connect_args: dict[str, object] = {}
    if url.get_backend_name() == "postgresql":
        # Overrides connect_timeout in the URL query so one attempt fits the budget.
        connect_args["connect_timeout"] = connect_timeout
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    finally:
        engine.dispose()


def _reason(exc: BaseException, password: str | None, username: str | None = None) -> str:
    cause = getattr(exc, "orig", None) or exc
    lines = str(cause).strip().splitlines()
    reason = lines[0] if lines else ""
    # A password equal to the user name (the in-cluster default) is not a secret,
    # and masking it would also hide the user and parts of the host name.
    if password and password != username:
        reason = reason.replace(password, "***")
    return f"{type(cause).__name__}: {reason}" if reason else type(cause).__name__


def wait_for_database(
    timeout_seconds: float,
    *,
    database_url: str | None = None,
    probe: Callable[[URL, int], None] = probe_database,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Return True once Postgres answers, False when ``timeout_seconds`` runs out.

    Each attempt's connect timeout is capped by the time still left. libpq's
    two-second floor means the last attempt can overrun the budget by up to
    two seconds.
    """
    raw_url = settings.sqlalchemy_database_url if database_url is None else database_url.strip()
    if not raw_url:
        logger.error(
            "DATABASE_URL is not set; cannot run migrations",
            extra={"event": "db.wait.not_configured"},
        )
        return False
    try:
        url = make_url(raw_url)
    except ArgumentError:
        # The parser's message repeats the whole URL, password included.
        logger.error(
            "DATABASE_URL could not be parsed; cannot run migrations",
            extra={"event": "db.wait.invalid_url"},
        )
        return False
    target = describe_target(url)
    budget = max(0.0, float(timeout_seconds))
    started = monotonic()
    deadline = started + budget
    attempt = 0
    last_reason = "no attempt made"
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            logger.error(
                "Postgres still unreachable; giving up before migrations",
                extra={
                    "event": "db.wait.timed_out",
                    "target": target,
                    "attempts": attempt,
                    "timeout_seconds": budget,
                    "last_error": last_reason,
                    "hint": (
                        "Check the stock-ticker-postgres pod is Ready and its logs. "
                        f"If PID 1 is stuck in gosu, see {_RUNBOOK}."
                    ),
                },
            )
            return False
        attempt += 1
        connect_timeout = min(
            _MAX_CONNECT_TIMEOUT_SECONDS,
            max(_MIN_CONNECT_TIMEOUT_SECONDS, math.ceil(remaining)),
        )
        try:
            probe(url, connect_timeout)
        except (SQLAlchemyError, OSError) as exc:
            last_reason = _reason(exc, url.password, url.username)
            backoff = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
            pause = max(0.0, min(backoff, deadline - monotonic()))
            logger.warning(
                "Postgres not reachable yet; retrying",
                extra={
                    "event": "db.wait.retry",
                    "target": target,
                    "attempt": attempt,
                    "error": last_reason,
                    "retry_in_seconds": round(pause, 1),
                },
            )
            if pause > 0:
                sleep(pause)
            continue
        logger.info(
            "Postgres is reachable",
            extra={
                "event": "db.wait.ready",
                "target": target,
                "attempts": attempt,
                "waited_seconds": round(monotonic() - started, 1),
            },
        )
        return True


def main() -> int:
    configure_logging()
    return 0 if wait_for_database(settings.database_wait_seconds) else 1


if __name__ == "__main__":
    sys.exit(main())
