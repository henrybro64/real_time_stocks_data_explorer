"""Shared soft/hard collector deadline for First-Pass provider steps (#360).

A Python soft deadline cannot interrupt blocked I/O. Callers must cap each
HTTP timeout to ``min(configured, remaining)`` and skip work when
``remaining ≤ 1s`` so a request that cannot finish before the deadline is
never started.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import time

_COLLECTOR_DEADLINE: ContextVar[tuple[float, Callable[[], float]] | None] = ContextVar(
    "tw_research_collector_deadline", default=None
)

# Skip starting a request that cannot complete with a useful timeout floor.
_MIN_USEFUL_TIMEOUT_SECONDS = 1.0


class CollectorDeadlineExceeded(Exception):
    """Per-symbol collector deadline elapsed.

    Deliberately **not** an ``OSError``/``ValueError``/``RuntimeError`` so
    upstream-unavailable handlers do not swallow it; first-pass records
    ``collector_deadline_exceeded`` on the field only.
    """


@contextmanager
def collector_deadline_scope(
    deadline_monotonic: float | None,
    monotonic_clock: Callable[[], float] | None = None,
) -> Iterator[None]:
    """Bound provider steps to a soft deadline (checked between iterations)."""
    if deadline_monotonic is None:
        yield
        return
    token = _COLLECTOR_DEADLINE.set(
        (float(deadline_monotonic), monotonic_clock or time.monotonic)
    )
    try:
        yield
    finally:
        _COLLECTOR_DEADLINE.reset(token)


def check_collector_deadline() -> None:
    """Raise ``CollectorDeadlineExceeded`` when the active soft deadline elapsed."""
    active = _COLLECTOR_DEADLINE.get()
    if active is None:
        return
    deadline, clock = active
    if clock() >= deadline:
        raise CollectorDeadlineExceeded("collector_deadline_exceeded")


def remaining_collector_deadline_seconds() -> float | None:
    """Seconds left on the active deadline, or ``None`` when no deadline is set."""
    active = _COLLECTOR_DEADLINE.get()
    if active is None:
        return None
    deadline, clock = active
    return float(deadline) - float(clock())


def capped_request_timeout_seconds(configured: float) -> float:
    """Cap a provider timeout to the remaining collector deadline.

    Raises ``CollectorDeadlineExceeded`` when remaining time is too small to
    start a useful request (≤ 1s).
    """
    configured_f = max(0.0, float(configured))
    remaining = remaining_collector_deadline_seconds()
    if remaining is None:
        return configured_f
    if remaining <= _MIN_USEFUL_TIMEOUT_SECONDS:
        raise CollectorDeadlineExceeded("collector_deadline_exceeded")
    return min(configured_f, remaining)
