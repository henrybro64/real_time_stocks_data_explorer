from __future__ import annotations

from datetime import date

from app.services.taiwan_history_scan import (
    calendar_window_for_weekdays,
    collect_dated_history,
    scan_window_days,
)


def test_calendar_window_for_weekdays_matches_scheduler_sizing() -> None:
    assert calendar_window_for_weekdays(1) == 15
    assert calendar_window_for_weekdays(7) == 21
    assert calendar_window_for_weekdays(7) >= 7


def test_collect_dated_history_respects_weekday_limit_not_raw_calendar_days() -> None:
    probed: list[date] = []

    def fetch_day(day: date):
        probed.append(day)
        return None, True

    # Weekend candidate start: raw window_days=1 would probe Saturday only (skipped)
    # and yield zero weekday fetches. weekday_limit + expanded calendar span finds Friday.
    history, successful = collect_dated_history(
        requested_days=90,
        start_date=date(2026, 7, 11),  # Saturday
        fetch_day=fetch_day,
        record_date=lambda item: item,
        window_days=calendar_window_for_weekdays(1),
        weekday_limit=1,
    )

    assert history == []
    assert successful == 1
    assert probed == [date(2026, 7, 10)]  # Friday
    assert all(day.weekday() < 5 for day in probed)


def test_collect_dated_history_respects_explicit_window_days() -> None:
    probed: list[date] = []

    def fetch_day(day: date):
        probed.append(day)
        return None, True

    history, successful = collect_dated_history(
        requested_days=90,
        start_date=date(2026, 7, 9),
        fetch_day=fetch_day,
        record_date=lambda item: item,
        window_days=calendar_window_for_weekdays(7),
        weekday_limit=7,
    )

    assert history == []
    assert successful == len(probed)
    assert len(probed) == 7
    assert all(day.weekday() < 5 for day in probed)
    # Without an explicit bound, a 90-day request would probe far more weekdays.
    assert scan_window_days(90) > calendar_window_for_weekdays(7)
