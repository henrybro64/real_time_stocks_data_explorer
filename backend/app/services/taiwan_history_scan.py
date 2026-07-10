from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Generic, TypeVar
from zoneinfo import ZoneInfo

_TW_TIMEZONE = ZoneInfo("Asia/Taipei")
_START_LOOKBACK_HOUR = 18
_MIN_SCAN_WINDOW_DAYS = 100
_SCAN_WINDOW_MULTIPLIER = 2
_MAX_SCAN_WINDOW_DAYS = 360
RESULT_CACHE_TTL = timedelta(minutes=5)

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


@dataclass(frozen=True)
class ResultCacheEntry(Generic[T]):
    cached_at: datetime
    result: T


def candidate_start_date(now: datetime) -> date:
    local_now = now.astimezone(_TW_TIMEZONE)
    candidate = local_now.date()
    if local_now.hour < _START_LOOKBACK_HOUR:
        candidate -= timedelta(days=1)
    return candidate


def iter_candidate_weekdays(start_date: date, window_days: int) -> Iterator[date]:
    """Yield weekdays only so weekend probes do not hit official endpoints."""
    for day_offset in range(window_days):
        current_date = start_date - timedelta(days=day_offset)
        if current_date.weekday() >= 5:
            continue
        yield current_date


def scan_window_days(days: int) -> int:
    requested = max(days, 1)
    return min(
        _MAX_SCAN_WINDOW_DAYS,
        max(_MIN_SCAN_WINDOW_DAYS, requested * _SCAN_WINDOW_MULTIPLIER),
    )


def get_cached_or_compute(
    cache: dict[K, ResultCacheEntry[T]],
    key: K,
    *,
    now: datetime,
    ttl: timedelta,
    compute: Callable[[], T],
) -> T:
    cached = cache.get(key)
    if cached is not None and now - cached.cached_at < ttl:
        return cached.result
    result = compute()
    cache[key] = ResultCacheEntry(cached_at=now, result=result)
    return result


def collect_dated_history(
    *,
    requested_days: int,
    start_date: date,
    fetch_day: Callable[[date], tuple[T | None, bool]],
    record_date: Callable[[T], str],
) -> tuple[list[T], int]:
    """Scan weekdays backward, dedupe by resolved trading date, stop at requested_days."""
    history: list[T] = []
    seen_dates: set[str] = set()
    successful_fetches = 0

    for current_date in iter_candidate_weekdays(start_date, scan_window_days(requested_days)):
        record, request_succeeded = fetch_day(current_date)
        if request_succeeded:
            successful_fetches += 1
        if record is None:
            continue
        # Official payloads may resolve holiday probes to a prior trading date.
        resolved = record_date(record)
        if resolved in seen_dates:
            continue
        seen_dates.add(resolved)
        history.append(record)
        if len(history) >= requested_days:
            break

    return history, successful_fetches
