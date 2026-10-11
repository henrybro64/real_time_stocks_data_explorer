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
    max_stale_seconds: float | None = None
    registered_at: datetime | None = None
    # Heartbeat: set on every loop pass (success, failure, or skip) (#289).
    last_tick_at: datetime | None = None
    max_tick_gap_seconds: float | None = None


def reset_job_health_for_tests() -> None:
    with _lock:
        _jobs.clear()


def register_background_job(
    job_name: str,
    *,
    enabled: bool,
    thread: Thread | None = None,
    max_stale_seconds: float | None = None,
    max_tick_gap_seconds: float | None = None,
) -> None:
    with _lock:
        state = _jobs.setdefault(job_name, _JobState())
        state.enabled = enabled
        state.thread = thread
        state.max_stale_seconds = max_stale_seconds
        state.max_tick_gap_seconds = max_tick_gap_seconds
        if state.registered_at is None:
            state.registered_at = datetime.now(UTC)


def note_job_success(job_name: str) -> None:
    with _lock:
        state = _jobs.setdefault(job_name, _JobState())
        state.last_success_at = datetime.now(UTC)


def note_job_tick(job_name: str) -> None:
    """Record that the loop completed a pass, whatever the outcome (#289)."""
    with _lock:
        state = _jobs.setdefault(job_name, _JobState())
        state.last_tick_at = datetime.now(UTC)


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
    return value records ``last_success_at``. Every pass records a heartbeat.
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
    finally:
        note_job_tick(job_name)
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
                max_stale_seconds=state.max_stale_seconds,
                registered_at=state.registered_at,
                last_tick_at=state.last_tick_at,
            )
            for name, state in _jobs.items()
        }
    return {
        name: {
            "enabled": state.enabled,
            "alive": bool(state.thread is not None and state.thread.is_alive()),
            "last_success_at": _iso_utc(state.last_success_at),
            "last_tick_at": _iso_utc(state.last_tick_at),
            "last_error_at": _iso_utc(state.last_error_at),
            "last_error_type": state.last_error_type,
        }
        for name, state in sorted(snapshot.items())
    }


def _thread_died(state: _JobState) -> bool:
    return state.enabled and state.thread is not None and not state.thread.is_alive()


def dead_job_names() -> list[str]:
    """Enabled jobs whose thread has exited.

    Liveness uses this and ``hung_job_names``: a restart repairs a dead or stuck
    thread, but not a stale job waiting on Postgres or a provider (#289).
    """
    with _lock:
        snapshot = list(_jobs.items())
    return [name for name, state in snapshot if _thread_died(state)]


def hung_job_names(now: datetime | None = None) -> list[str]:
    """Enabled jobs whose thread is alive but has not finished a loop pass in time.

    A tick blocked forever (a DB lock wait, a half-open connection) never fails or
    succeeds, so only a missing heartbeat shows it. A restart does fix that (#289).
    """
    current = now or datetime.now(UTC)
    with _lock:
        snapshot = list(_jobs.items())
    names: list[str] = []
    for name, state in snapshot:
        if not state.enabled or state.thread is None or not state.thread.is_alive():
            continue
        gap = state.max_tick_gap_seconds
        anchor = state.last_tick_at or state.registered_at
        if gap and anchor is not None and (current - anchor).total_seconds() > float(gap):
            names.append(name)
    return names


def unhealthy_job_names(now: datetime | None = None) -> list[str]:
    """Enabled jobs whose thread died or whose last success is past its budget.

    A job with no success yet is healthy until ``max_stale_seconds`` after it
    was registered, so a daily job is not failed during startup.
    """
    current = now or datetime.now(UTC)
    with _lock:
        snapshot = list(_jobs.items())
    names: list[str] = []
    for name, state in snapshot:
        if not state.enabled:
            continue
        if _thread_died(state):
            names.append(name)
            continue
        stale_after = state.max_stale_seconds
        if not stale_after:
            continue
        anchor = state.last_success_at or state.registered_at
        if anchor is None:
            continue
        if (current - anchor).total_seconds() > float(stale_after):
            names.append(name)
    return names


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
