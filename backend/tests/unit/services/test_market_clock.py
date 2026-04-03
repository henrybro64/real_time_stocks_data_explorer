from datetime import datetime

from app.services.market_clock import is_market_open


def test_us_market_is_open_at_exact_open_boundary() -> None:
    now = datetime.fromisoformat("2026-03-30T09:30:00-04:00")

    assert is_market_open("US", now) is True


def test_us_market_is_closed_before_open() -> None:
    now = datetime.fromisoformat("2026-03-30T09:29:00-04:00")

    assert is_market_open("US", now) is False


def test_tw_market_is_closed_on_weekends() -> None:
    now = datetime.fromisoformat("2026-04-04T10:00:00+08:00")

    assert is_market_open("TW", now) is False


def test_tw_market_closes_after_1330() -> None:
    open_boundary = datetime.fromisoformat("2026-04-01T13:30:00+08:00")
    closed_after = datetime.fromisoformat("2026-04-01T13:31:00+08:00")

    assert is_market_open("TW", open_boundary) is True
    assert is_market_open("TW", closed_after) is False
