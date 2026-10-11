"""NYSE New Year's Day observance (#275)."""

from __future__ import annotations

from datetime import date

from app.services.market_clock import _observed_new_years_day, _us_market_holidays


def test_saturday_new_years_is_not_observed_on_friday() -> None:
    # 1 January 2028 is a Saturday → no Friday observance on 2027-12-31.
    assert _observed_new_years_day(2028) is None
    assert date(2027, 12, 31) not in _us_market_holidays(2027)


def test_sunday_new_years_moves_to_monday() -> None:
    # 1 January 2034 is a Sunday → observed Monday 2 January.
    assert _observed_new_years_day(2034) == date(2034, 1, 2)
    assert date(2034, 1, 2) in _us_market_holidays(2034)


def test_weekday_new_years_is_itself() -> None:
    # 1 January 2026 is a Thursday.
    assert _observed_new_years_day(2026) == date(2026, 1, 1)
    assert date(2026, 1, 1) in _us_market_holidays(2026)
