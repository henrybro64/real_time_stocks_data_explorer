"""Liveness and last-success tracking for in-process background jobs (#266)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from typing import Any

logger = logging.getLogger(__name__)

_lock = Lock()
_jobs: dict[str, _JobState] = {}


@dataclass
class _JobState:
    enabled: bool = False
    thread: Thread | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_type: str | None = None


def reset_job_health_for_tests() -> None:
    with _lock:
        _jobs.clear()


def register_background_job(
    job_name: str,
    *,
    enabled: bool,
    thread: Thread | None = None,
) -> None:
    with _lock:
        state = _jobs.setdefault(job_name, _JobState())
        state.enabled = enabled
        state.thread = thread


def note_job_success(job_name: str) -> None:
    with _lock:
        state = _jobs.setdefault(job_name, _JobState())
        state.last_success_at = datetime.now(UTC)


def note_job_failure(job_name: str, exc: BaseException) -> None:
    with _lock:
        state = _jobs.setdefault(job_name, _JobState())
        state.last_error_at = datetime.now(UTC)
        state.last_error_type = type(exc).__name__


# Return this from a tick that stayed alive but did not complete its work.
# ``last_success_at`` stays put so /health does not treat a retry or an idle
# skip as a successful run.
JOB_TICK_INCOMPLETE = object()


def run_guarded_tick(
    job_name: str,
    *,
    event: str,
    tick: Callable[[], Any],
) -> None:
    """Run one sampler iteration; log and keep the loop alive on failure.

    A tick that returns ``JOB_TICK_INCOMPLETE`` is not a success. Every other
    return value records ``last_success_at``.
    """
    try:
        outcome = tick()
    except Exception as exc:  # noqa: BLE001 - keep daemon loops alive
        note_job_failure(job_name, exc)
        logger.exception(
            "Background job iteration failed",
            extra={
                "event": event,
                "job_name": job_name,
                "error_type": type(exc).__name__,
            },
        )
        return
    if outcome is JOB_TICK_INCOMPLETE:
        return
    note_job_success(job_name)


def get_background_jobs_health() -> dict[str, Any]:
    with _lock:
        snapshot = {
            name: _JobState(
                enabled=state.enabled,
                thread=state.thread,
                last_success_at=state.last_success_at,
                last_error_at=state.last_error_at,
                last_error_type=state.last_error_type,
            )
            for name, state in _jobs.items()
        }
    return {
        name: {
            "enabled": state.enabled,
            "alive": bool(state.thread is not None and state.thread.is_alive()),
            "last_success_at": _iso_utc(state.last_success_at),
            "last_error_at": _iso_utc(state.last_error_at),
            "last_error_type": state.last_error_type,
        }
        for name, state in sorted(snapshot.items())
    }


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
