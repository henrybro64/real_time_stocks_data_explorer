from datetime import date, datetime

from app.services.market_clock import (
    current_snapshot_bucket,
    is_market_open,
    is_within_hour_before_market_open,
)


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


def test_us_market_is_closed_on_observed_independence_day() -> None:
    now = datetime.fromisoformat("2026-07-03T10:00:00-04:00")

    assert is_market_open("US", now) is False


def test_tw_market_is_closed_on_configured_holiday(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset({date(2026, 5, 1)}),
    )

    now = datetime.fromisoformat("2026-05-01T10:00:00+08:00")

    assert is_market_open("TW", now) is False


def test_snapshot_bucket_uses_last_open_hour_at_close_boundary() -> None:
    now = datetime.fromisoformat("2026-03-30T16:00:00-04:00")

    bucket = current_snapshot_bucket("US", now)

    assert bucket is not None
    assert bucket.isoformat() == "2026-03-30T19:00:00+00:00"


def test_us_within_hour_before_open_on_trading_day() -> None:
    now = datetime.fromisoformat("2026-03-30T12:45:00+00:00")

    assert is_within_hour_before_market_open("US", now) is True


def test_us_not_within_hour_before_open_before_window() -> None:
    now = datetime.fromisoformat("2026-03-30T12:20:00+00:00")

    assert is_within_hour_before_market_open("US", now) is False


def test_us_not_within_hour_before_open_when_session_has_started() -> None:
    now = datetime.fromisoformat("2026-03-30T13:35:00+00:00")

    assert is_within_hour_before_market_open("US", now) is False


def test_tw_within_hour_before_open_on_trading_day() -> None:
    now = datetime.fromisoformat("2026-04-01T00:30:00+00:00")

    assert is_within_hour_before_market_open("TW", now) is True


def test_tw_not_within_hour_before_open_on_weekend() -> None:
    now = datetime.fromisoformat("2026-04-04T00:30:00+00:00")

    assert is_within_hour_before_market_open("TW", now) is False
