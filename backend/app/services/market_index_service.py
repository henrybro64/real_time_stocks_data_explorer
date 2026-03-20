from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.services.market_clock import is_market_open

_REQUEST_TIMEOUT_SECONDS = 8
_STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
_TWSE_INDEX_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


@dataclass(frozen=True)
class MarketIndexRecord:
    id: str
    market: str
    label: str
    value: float
    change: float
    change_percent: float
    timestamp: str
    is_market_open: bool


class MarketIndexService:
    def get_latest(self) -> list[MarketIndexRecord]:
        records: list[MarketIndexRecord] = []

        twse = _fetch_twse_index()
        if twse is not None:
            records.append(twse)

        for symbol, label in (
            ("^DJI", "DOW Jones"),
            ("^NDQ", "Nasdaq"),
            ("^SPX", "S&P 500"),
        ):
            record = _fetch_stooq_index(symbol, label)
            if record is not None:
                records.append(record)

        return records


market_index_service = MarketIndexService()


def _fetch_stooq_index(
    stooq_symbol: str, label: str
) -> MarketIndexRecord | None:
    try:
        payload = _load_text(
            _STOOQ_QUOTE_URL,
            {"s": stooq_symbol.lower(), "i": "d"},
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/csv,text/plain,*/*",
            },
        )
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None

    rows = payload.strip().splitlines()
    if not rows:
        return None

    parts = [part.strip() for part in rows[0].split(",")]
    if len(parts) < 8:
        return None

    date_text, time_text = parts[1], parts[2]
    open_value = _parse_float(parts[3])
    close_value = _parse_float(parts[6])
    if close_value is None:
        return None

    previous_close = open_value if open_value is not None else close_value
    change = close_value - previous_close
    change_percent = (change / previous_close * 100) if previous_close else 0.0
    quote_time = _parse_stooq_timestamp(
        date_text, time_text, "America/New_York"
    )

    return MarketIndexRecord(
        id=stooq_symbol,
        market="US",
        label=label,
        value=round(close_value, 2),
        change=round(change, 2),
        change_percent=round(change_percent, 2),
        timestamp=quote_time.isoformat(),
        is_market_open=is_market_open("US", quote_time),
    )


def _fetch_twse_index() -> MarketIndexRecord | None:
    try:
        payload = _load_json(
            _TWSE_INDEX_URL,
            {"ex_ch": "tse_t00.tw", "json": "1", "delay": "0"},
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://mis.twse.com.tw/stock/index.jsp",
            },
        )
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None

    msg_array = payload.get("msgArray")
    if not isinstance(msg_array, list) or not msg_array:
        return None
    first = msg_array[0]
    if not isinstance(first, dict):
        return None

    value = _parse_float(str(first.get("z") or ""))
    previous_close = _parse_float(str(first.get("y") or ""))
    raw_date = str(first.get("d") or "")
    raw_time = str(first.get("t") or "")
    if value is None or previous_close in {None, 0}:
        return None

    change = value - previous_close
    change_percent = change / previous_close * 100
    quote_time = _parse_twse_timestamp(raw_date, raw_time, "Asia/Taipei")

    return MarketIndexRecord(
        id="TWSE:TAIEX",
        market="TW",
        label="Taiwan Stock Exchange",
        value=round(value, 2),
        change=round(change, 2),
        change_percent=round(change_percent, 2),
        timestamp=quote_time.isoformat(),
        is_market_open=is_market_open("TW", quote_time),
    )


def _load_json(
    url: str, params: dict[str, str], headers: dict[str, str]
) -> dict:
    request = Request(f"{url}?{urlencode(params)}")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except URLError as exc:
        if not _should_retry_insecure(url, exc):
            raise
        insecure_context = ssl._create_unverified_context()  # noqa: SLF001
        with urlopen(
            request,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            context=insecure_context,
        ) as response:
            return json.load(response)


def _load_text(
    url: str, params: dict[str, str], headers: dict[str, str]
) -> str:
    request = Request(f"{url}?{urlencode(params)}")
    for key, value in headers.items():
        request.add_header(key, value)
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="ignore")


def _parse_float(raw_value: str) -> float | None:
    value = raw_value.strip().replace(",", "")
    if not value or value in {"N/D", "-", "--"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_stooq_timestamp(
    raw_date: str, raw_time: str, timezone_name: str
) -> datetime:
    tz = ZoneInfo(timezone_name)
    if (
        len(raw_date) == 8
        and len(raw_time) == 6
        and raw_date.isdigit()
        and raw_time.isdigit()
    ):
        try:
            return datetime.strptime(
                f"{raw_date}{raw_time}", "%Y%m%d%H%M%S"
            ).replace(tzinfo=tz)
        except ValueError:
            return datetime.now(tz)
    return datetime.now(tz)


def _parse_twse_timestamp(
    raw_date: str, raw_time: str, timezone_name: str
) -> datetime:
    tz = ZoneInfo(timezone_name)
    if len(raw_date) == 8 and raw_date.isdigit():
        try:
            if raw_time:
                return datetime.strptime(
                    f"{raw_date}{raw_time}", "%Y%m%d%H:%M:%S"
                ).replace(tzinfo=tz)
            return datetime.strptime(raw_date, "%Y%m%d").replace(
                hour=13,
                minute=30,
                tzinfo=tz,
            )
        except ValueError:
            return datetime.now(tz)
    return datetime.now(tz)


def _should_retry_insecure(url: str, exc: URLError) -> bool:
    if "twse.com.tw" not in url:
        return False
    return isinstance(exc.reason, ssl.SSLCertVerificationError)
