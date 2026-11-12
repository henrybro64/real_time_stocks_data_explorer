"""Bounded TW holiday-calendar preload shared by Profile (#331) and chip evidence (#346/#347).

Callers preload at most once per request (never per record). Failed preloads are
negative-cached for 10 minutes. Network fetches are capped at ``max_fetches=4``.
"""

from __future__ import annotations

import time
from threading import Lock

from app.services.market_clock import (
    ensure_tw_holiday_calendars_loaded,
    preload_tw_holiday_calendars,
)

_PRELOAD_NEGATIVE_TTL_SECONDS = 600.0
_preload_lock = Lock()
# year-tuple → monotonic deadline until which failed preloads are not retried
_preload_failed_until: dict[tuple[int, ...], float] = {}


def ensure_tw_calendars_bounded(years: list[int] | tuple[int, ...]) -> bool:
    """Load TW calendars for the given years; preload at most once per year set.

    Negative-caches failed preloads for 10 minutes. Returns True only when every
    requested year is known after the bounded attempt.
    """
    unique = tuple(sorted({int(year) for year in years}))
    if not unique:
        return True
    if ensure_tw_holiday_calendars_loaded(unique):
        return True
    with _preload_lock:
        if ensure_tw_holiday_calendars_loaded(unique):
            return True
        now_m = time.monotonic()
        failed_until = _preload_failed_until.get(unique, 0.0)
        if now_m < failed_until:
            return False
        result = preload_tw_holiday_calendars(
            unique, allow_network=True, max_fetches=4
        )
        if bool(result.get("ok")) and ensure_tw_holiday_calendars_loaded(unique):
            _preload_failed_until.pop(unique, None)
            return True
        _preload_failed_until[unique] = now_m + _PRELOAD_NEGATIVE_TTL_SECONDS
        return False


def reset_for_tests() -> None:
    """Clear negative cache between unit tests."""
    with _preload_lock:
        _preload_failed_until.clear()
