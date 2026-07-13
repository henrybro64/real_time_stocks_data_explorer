from __future__ import annotations

from datetime import date

from app.services.taiwan_history_scan import collect_dated_history, scan_window_days


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
        window_days=7,
    )

    assert history == []
    assert successful == len(probed)
    assert len(probed) <= 7
    assert all(day.weekday() < 5 for day in probed)
    # Without an explicit bound, a 90-day request would probe far more weekdays.
    assert scan_window_days(90) > 7
