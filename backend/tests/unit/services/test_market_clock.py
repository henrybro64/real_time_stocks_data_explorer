from datetime import date, datetime

from app.services.market_clock import (
    TW_DAILY_PUBLISH_TIME,
    current_rankings_snapshot_bucket,
    current_snapshot_bucket,
    is_market_open,
    is_within_hour_before_market_open,
    latest_trading_day_bucket,
    market_session_close,
    tw_institutional_daily_bucket_from_timestamp,
    tw_institutional_daily_snapshot_bucket,
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


def test_us_early_close_day_after_thanksgiving() -> None:
    # 2026-11-26 Thanksgiving; 2026-11-27 Black Friday early close at 13:00 ET.
    assert market_session_close("US", date(2026, 11, 27)).isoformat() == "13:00:00"
    assert market_session_close("US", date(2026, 11, 24)).isoformat() == "16:00:00"

    during_session = datetime.fromisoformat("2026-11-27T12:00:00-05:00")
    after_early_close = datetime.fromisoformat("2026-11-27T14:00:00-05:00")

    assert is_market_open("US", during_session) is True
    assert is_market_open("US", after_early_close) is False


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


def test_us_rankings_bucket_uses_0930_window() -> None:
    now = datetime.fromisoformat("2026-03-30T09:30:00-04:00")

    bucket = current_rankings_snapshot_bucket("US", now)

    assert bucket is not None
    assert bucket.isoformat() == "2026-03-30T13:30:00+00:00"


def test_us_rankings_bucket_keeps_0959_in_same_window() -> None:
    now = datetime.fromisoformat("2026-03-30T09:59:00-04:00")

    bucket = current_rankings_snapshot_bucket("US", now)

    assert bucket is not None
    assert bucket.isoformat() == "2026-03-30T13:30:00+00:00"


def test_us_rankings_bucket_moves_at_1000() -> None:
    now = datetime.fromisoformat("2026-03-30T10:00:00-04:00")

    bucket = current_rankings_snapshot_bucket("US", now)

    assert bucket is not None
    assert bucket.isoformat() == "2026-03-30T14:00:00+00:00"


def test_tw_rankings_bucket_uses_0900_window() -> None:
    now = datetime.fromisoformat("2026-04-01T09:00:00+08:00")

    bucket = current_rankings_snapshot_bucket("TW", now)

    assert bucket is not None
    assert bucket.isoformat() == "2026-04-01T01:00:00+00:00"


def test_tw_rankings_bucket_uses_last_open_window_at_1330_boundary() -> None:
    now = datetime.fromisoformat("2026-04-01T13:30:00+08:00")

    bucket = current_rankings_snapshot_bucket("TW", now)

    assert bucket is not None
    assert bucket.isoformat() == "2026-04-01T05:00:00+00:00"


def test_tw_daily_bucket_uses_latest_available_trading_day_before_publish(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset(),
    )

    bucket = latest_trading_day_bucket(
        "TW",
        datetime.fromisoformat("2026-04-06T10:00:00+08:00"),
        available_after=datetime.fromisoformat("2026-04-06T18:00:00+08:00").time(),
    )

    assert bucket is not None
    assert bucket.isoformat() == "2026-04-02T16:00:00+00:00"


def test_tw_institutional_daily_snapshot_bucket_uses_previous_trading_day_before_taipei_cutoff(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset(),
    )

    bucket = tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-06T17:59:00+08:00"),
    )

    assert bucket is not None
    assert bucket.isoformat() == "2026-04-03T10:00:00+00:00"


def test_tw_institutional_daily_snapshot_bucket_uses_today_at_taipei_cutoff(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset(),
    )

    bucket = tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-06T18:00:00+08:00"),
    )

    assert bucket is not None
    assert bucket.isoformat() == "2026-04-06T10:00:00+00:00"


def test_tw_institutional_daily_snapshot_bucket_uses_friday_on_weekend(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset(),
    )

    saturday = tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-04T10:00:00+08:00"),
    )
    sunday = tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-05T19:00:00+08:00"),
    )

    assert saturday is not None
    assert sunday is not None
    assert saturday.isoformat() == "2026-04-03T10:00:00+00:00"
    assert sunday == saturday


def test_tw_institutional_daily_snapshot_bucket_skips_weekday_holiday(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset({date(2026, 4, 6)}) if year == 2026 else frozenset(),
    )

    bucket = tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-06T19:00:00+08:00"),
    )

    assert bucket is not None
    assert bucket.isoformat() == "2026-04-03T10:00:00+00:00"


def test_tw_institutional_daily_bucket_from_timestamp_normalizes_to_publish_time() -> None:
    midnight = tw_institutional_daily_bucket_from_timestamp("2026-07-02T00:00:00+08:00")
    publish = tw_institutional_daily_bucket_from_timestamp("2026-07-02T18:00:00+08:00")

    assert midnight is not None
    assert publish is not None
    assert midnight == publish
    assert midnight.isoformat() == "2026-07-02T10:00:00+00:00"
    assert TW_DAILY_PUBLISH_TIME.hour == 18
    assert TW_DAILY_PUBLISH_TIME.minute == 0


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
