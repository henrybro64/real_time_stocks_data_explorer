"""Shared Yahoo chart quote math (#258 follow-up)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.providers.yahoo_chart import coherent_price_change, previous_session_close

_NY = "America/New_York"


def _ts(day: int) -> int:
    return int(datetime(2026, 10, day, 9, 30, tzinfo=ZoneInfo(_NY)).timestamp())


def _block(closes: list[float | None], days: list[int]) -> dict[str, object]:
    return {
        "timestamp": [_ts(day) for day in days],
        "indicators": {"quote": [{"close": closes}]},
    }


def test_uses_the_bar_immediately_before_the_session() -> None:
    block = _block([100.0, 101.0, 102.0], [2, 5, 6])

    assert previous_session_close(block, {}, session_date=date(2026, 10, 6), timezone_name=_NY) == 101.0


def test_missing_prior_close_falls_back_to_meta_not_an_older_bar() -> None:
    block = _block([100.0, None, 102.0], [2, 5, 6])
    meta = {"previousClose": 101.5, "chartPreviousClose": 95.0}

    assert previous_session_close(block, meta, session_date=date(2026, 10, 6), timezone_name=_NY) == 101.5


def test_missing_prior_close_without_meta_is_unknown() -> None:
    """Oct 2's 100.0 would turn a two-day move into the daily change."""
    block = _block([100.0, None, 102.0], [2, 5, 6])
    meta = {"chartPreviousClose": 95.0}

    assert previous_session_close(block, meta, session_date=date(2026, 10, 6), timezone_name=_NY) is None


def test_chart_previous_close_only_when_no_earlier_bar_exists() -> None:
    block = _block([102.0], [6])

    assert (
        previous_session_close(block, {"chartPreviousClose": 101.0}, session_date=date(2026, 10, 6), timezone_name=_NY)
        == 101.0
    )


def test_coherent_change_is_derived_from_the_rounded_pair() -> None:
    assert coherent_price_change(333.634, 332.891) == (333.63, 332.89, 0.74, 0.22)


@pytest.mark.parametrize("previous", [0.004, 0.0, -1.0])
def test_previous_close_that_rounds_to_zero_has_no_change(previous: float) -> None:
    assert coherent_price_change(0.01, previous) is None
