from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from html.parser import HTMLParser
import logging
from zoneinfo import ZoneInfo

from app.services.http_client import HttpFetchError, fetch_text


logger = logging.getLogger(__name__)

MARKET_TIMEZONES = {
    "US": ZoneInfo("America/New_York"),
    "TW": ZoneInfo("Asia/Taipei"),
}

MARKET_WINDOWS = {
    "US": (time(9, 30), time(16, 0)),
    "TW": (time(9, 0), time(13, 30)),
}

# NYSE/Nasdaq early close is 13:00 ET on these recurring session types.
_US_EARLY_CLOSE = time(13, 0)

_TWSE_HOLIDAY_URL = "https://www.twse.com.tw/holidaySchedule/holidaySchedule"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class _TableRowParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_row is not None and self._cell_parts is not None:
            cell = "".join(self._cell_parts).strip()
            self._current_row.append(" ".join(cell.split()))
            self._cell_parts = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = None
            self._cell_parts = None


def market_now(market: str) -> datetime:
    return datetime.now(MARKET_TIMEZONES[market])


def market_local_time(market: str, now: datetime | None = None) -> datetime:
    current = now or market_now(market)
    return current.astimezone(MARKET_TIMEZONES[market])


def is_trading_day(market: str, now: datetime | None = None) -> bool:
    current = market_local_time(market, now)
    current_date = current.date()
    if current.weekday() >= 5:
        return False
    if market == "US":
        return current_date not in _us_market_holidays(current_date.year)
    if market == "TW":
        return current_date not in _tw_market_holidays(current_date.year)
    raise KeyError(market)


def market_session_close(market: str, on_date: date) -> time:
    """Return the regular or early session close for ``market`` on ``on_date``."""
    _start, default_close = MARKET_WINDOWS[market]
    if market == "US" and on_date in _us_early_close_days(on_date.year):
        return _US_EARLY_CLOSE
    return default_close


def market_session_window(market: str, on_date: date) -> tuple[time, time]:
    start, _default_close = MARKET_WINDOWS[market]
    return start, market_session_close(market, on_date)


def is_market_open(market: str, now: datetime | None = None) -> bool:
    current = market_local_time(market, now)
    if not is_trading_day(market, current):
        return False
    start, end = market_session_window(market, current.date())
    current_time = current.time().replace(second=0, microsecond=0)
    return start <= current_time <= end


def is_within_hour_before_market_open(
    market: str, now: datetime | None = None
) -> bool:
    """Trading day, venue closed, local time in [session_start - 1h, session_start)."""
    if is_market_open(market, now):
        return False
    current = market_local_time(market, now)
    if not is_trading_day(market, current):
        return False
    start, _end = MARKET_WINDOWS[market]
    session_start = current.replace(
        hour=start.hour,
        minute=start.minute,
        second=0,
        microsecond=0,
    )
    window_start = session_start - timedelta(hours=1)
    current_minute = current.replace(second=0, microsecond=0)
    return window_start <= current_minute < session_start


def current_snapshot_bucket(market: str, now: datetime | None = None) -> datetime | None:
    current = market_local_time(market, now)
    if not is_market_open(market, current):
        return None

    start, end = market_session_window(market, current.date())
    effective_current = current
    if current.time().replace(second=0, microsecond=0) == end:
        effective_current = current - timedelta(minutes=1)

    bucket_start = effective_current.replace(minute=0, second=0, microsecond=0)
    bucket_end = bucket_start + timedelta(hours=1)
    session_start = effective_current.replace(
        hour=start.hour,
        minute=start.minute,
        second=0,
        microsecond=0,
    )
    session_end = effective_current.replace(
        hour=end.hour,
        minute=end.minute,
        second=0,
        microsecond=0,
    )
    if bucket_end <= session_start or bucket_start > session_end:
        return None
    return bucket_start.astimezone(timezone.utc)


def current_rankings_snapshot_bucket(market: str, now: datetime | None = None) -> datetime | None:
    return _current_intraday_bucket(market, now, bucket_minutes=30)


# End-of-day TW institutional files (T86 / TPEx dailyTrade) become the applicable
# rankings cache key at 18:00 Asia/Taipei. Keep this as the single cutoff used by
# snapshot lookup, persist, and item timestamps. Separate from
# settings.taiwan_market_data_refresh_hour, which gates the detail-page store job.
TW_DAILY_PUBLISH_TIME = time(18, 0)


def latest_trading_day_bucket(
    market: str,
    now: datetime | None = None,
    *,
    available_after: time | None = None,
    lookback_days: int = 10,
) -> datetime | None:
    current = market_local_time(market, now)
    candidate_date = current.date()
    current_time = current.time().replace(second=0, microsecond=0)
    if available_after is not None and current_time < available_after:
        candidate_date -= timedelta(days=1)

    for day_offset in range(lookback_days):
        current_date = candidate_date - timedelta(days=day_offset)
        trading_probe = datetime.combine(
            current_date,
            time(12, 0),
            tzinfo=MARKET_TIMEZONES[market],
        )
        if not is_trading_day(market, trading_probe):
            continue
        return datetime.combine(
            current_date,
            time(0, 0),
            tzinfo=MARKET_TIMEZONES[market],
        ).astimezone(timezone.utc)
    return None


def tw_institutional_daily_bucket_for_date(ranking_date: date) -> datetime:
    """Return the UTC cache key for a TW institutional ranking date (18:00 Taipei)."""
    return datetime.combine(
        ranking_date,
        TW_DAILY_PUBLISH_TIME,
        tzinfo=MARKET_TIMEZONES["TW"],
    ).astimezone(timezone.utc)


def tw_institutional_daily_bucket_from_timestamp(raw: str) -> datetime | None:
    """Normalize a live item timestamp onto the shared 18:00 Taipei daily key."""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    ranking_date = parsed.astimezone(MARKET_TIMEZONES["TW"]).date()
    return tw_institutional_daily_bucket_for_date(ranking_date)


def tw_institutional_daily_snapshot_bucket(now: datetime | None = None) -> datetime | None:
    """Return the 18:00 Asia/Taipei bucket for the latest published TW daily file.

    Institutional rankings are end-of-day. Before 18:00 Taipei the previous
    trading day's snapshot is still the applicable cache key; at/after 18:00
    today's trading day becomes the expected key.
    """
    trading_day_start = latest_trading_day_bucket(
        "TW",
        now,
        available_after=TW_DAILY_PUBLISH_TIME,
    )
    if trading_day_start is None:
        return None
    ranking_date = trading_day_start.astimezone(MARKET_TIMEZONES["TW"]).date()
    return tw_institutional_daily_bucket_for_date(ranking_date)


def _current_intraday_bucket(
    market: str,
    now: datetime | None,
    *,
    bucket_minutes: int,
) -> datetime | None:
    current = market_local_time(market, now)
    if not is_market_open(market, current):
        return None

    start, end = market_session_window(market, current.date())
    effective_current = current
    if current.time().replace(second=0, microsecond=0) == end:
        effective_current = current - timedelta(minutes=1)

    session_start = effective_current.replace(
        hour=start.hour,
        minute=start.minute,
        second=0,
        microsecond=0,
    )
    session_end = effective_current.replace(
        hour=end.hour,
        minute=end.minute,
        second=0,
        microsecond=0,
    )
    minutes_since_start = int(
        (effective_current - session_start).total_seconds() // 60
    )
    bucket_offset_minutes = (minutes_since_start // bucket_minutes) * bucket_minutes
    bucket_start = session_start + timedelta(minutes=bucket_offset_minutes)
    bucket_end = bucket_start + timedelta(minutes=bucket_minutes)
    if bucket_end <= session_start or bucket_start > session_end:
        return None
    return bucket_start.astimezone(timezone.utc)


def iter_open_snapshot_windows(now: datetime | None = None) -> list[tuple[str, datetime]]:
    current = now or datetime.now(timezone.utc)
    windows: list[tuple[str, datetime]] = []
    for market in MARKET_TIMEZONES:
        bucket = current_snapshot_bucket(market, current)
        if bucket is not None:
            windows.append((market, bucket))
    return windows


def market_label(market: str) -> str:
    return {
        "US": "U.S. Market",
        "TW": "台灣市場",
    }[market]


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    current = date(year, month, day)
    if current.weekday() == 5:
        return current - timedelta(days=1)
    if current.weekday() == 6:
        return current + timedelta(days=1)
    return current


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(weeks=occurrence - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    while current.weekday() != weekday:
        current -= timedelta(days=1)
    return current


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


@lru_cache(maxsize=16)
def _us_market_holidays(year: int) -> frozenset[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),  # MLK Day
        _nth_weekday(year, 2, 0, 3),  # Presidents Day
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed_fixed_holiday(year, 6, 19),  # Juneteenth
        _observed_fixed_holiday(year, 7, 4),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed_fixed_holiday(year, 12, 25),  # Christmas
    }
    next_new_year_observed = _observed_fixed_holiday(year + 1, 1, 1)
    if next_new_year_observed.year == year:
        holidays.add(next_new_year_observed)
    return frozenset(holidays)


@lru_cache(maxsize=16)
def _us_early_close_days(year: int) -> frozenset[date]:
    """U.S. equity sessions that close at 13:00 ET instead of 16:00."""
    holidays = _us_market_holidays(year)
    early_closes: set[date] = set()

    thanksgiving = _nth_weekday(year, 11, 3, 4)
    day_after_thanksgiving = thanksgiving + timedelta(days=1)
    if day_after_thanksgiving not in holidays and day_after_thanksgiving.weekday() < 5:
        early_closes.add(day_after_thanksgiving)

    christmas_eve = date(year, 12, 24)
    if christmas_eve not in holidays and christmas_eve.weekday() < 5:
        early_closes.add(christmas_eve)

    july_third = date(year, 7, 3)
    if july_third not in holidays and july_third.weekday() < 5:
        early_closes.add(july_third)

    return frozenset(early_closes)


def _tw_market_holidays(year: int) -> frozenset[date]:
    try:
        return _tw_market_holidays_cached(year)
    except HttpFetchError:
        logger.warning("Failed to fetch TWSE holiday schedule for %s", year)
        return frozenset()


@lru_cache(maxsize=8)
def _tw_market_holidays_cached(year: int) -> frozenset[date]:
    query_year = year - 1911
    html = fetch_text(
        _TWSE_HOLIDAY_URL,
        params={"response": "html", "queryYear": str(query_year)},
        headers={"User-Agent": _BROWSER_USER_AGENT},
        timeout_seconds=8.0,
        retries=1,
    )

    parser = _TableRowParser()
    parser.feed(html)

    holidays: set[date] = set()
    for row in parser.rows:
        if len(row) < 2:
            continue
        try:
            current_date = date.fromisoformat(row[0])
        except ValueError:
            continue
        label = " ".join(row[1:])
        if "開始交易" in label or "最後交易日" in label:
            continue
        holidays.add(current_date)
    return frozenset(holidays)
