from datetime import datetime, time
from zoneinfo import ZoneInfo


MARKET_TIMEZONES = {
    "US": ZoneInfo("America/New_York"),
    "TW": ZoneInfo("Asia/Taipei"),
}

MARKET_WINDOWS = {
    "US": (time(9, 30), time(16, 0)),
    "TW": (time(9, 0), time(13, 30)),
}


def market_now(market: str) -> datetime:
    return datetime.now(MARKET_TIMEZONES[market])


def is_market_open(market: str, now: datetime | None = None) -> bool:
    current = now or market_now(market)
    if current.weekday() >= 5:
        return False
    start, end = MARKET_WINDOWS[market]
    current_time = current.time().replace(second=0, microsecond=0)
    return start <= current_time <= end


def market_label(market: str) -> str:
    return {
        "US": "U.S. Market",
        "TW": "台灣市場",
    }[market]

