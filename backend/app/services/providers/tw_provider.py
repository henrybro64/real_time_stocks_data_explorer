from __future__ import annotations

import json
import math
import re
import ssl
from datetime import date, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.market_clock import is_market_open


TW_STOCKS: dict[str, StockRecord] = {
    "TW:2330": StockRecord(
        id="TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        name_en="TSMC",
        name_zh_tw="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:2317": StockRecord(
        id="TW:2317",
        market="TW",
        symbol="2317",
        name="鴻海",
        name_en="Hon Hai Precision",
        name_zh_tw="鴻海",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:2454": StockRecord(
        id="TW:2454",
        market="TW",
        symbol="2454",
        name="聯發科",
        name_en="MediaTek",
        name_zh_tw="聯發科",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
}

BASE_PRICES = {
    "TW:2330": 948.0,
    "TW:2317": 171.5,
    "TW:2454": 1325.0,
}

_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_HISTORY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
_COMPANY_LIST_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_ISIN_LISTED_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
_ISIN_OTC_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
_REQUEST_TIMEOUT_SECONDS = 5
_CATALOG_TTL = timedelta(hours=12)


class TWProvider:
    market = "TW"
    _catalog_cache: dict[str, StockRecord] | None = None
    _catalog_cached_at: datetime | None = None

    def list_stocks(self) -> list[StockRecord]:
        return list(self._stock_catalog().values())

    def search(self, query: str) -> list[StockRecord]:
        normalized = query.strip().lower()
        return [
            stock
            for stock in self.list_stocks()
            if normalized in stock.symbol.lower()
            or normalized in stock.name.lower()
            or (stock.name_zh_tw and normalized in stock.name_zh_tw.lower())
            or (stock.name_en and normalized in stock.name_en.lower())
        ]

    def get_stock(self, stock_id: str) -> StockRecord | None:
        return self._stock_catalog().get(stock_id)

    def get_quote(self, stock_id: str) -> QuoteRecord | None:
        stock = self.get_stock(stock_id)
        if stock is None:
            return None

        quote = self._fetch_quote(stock)
        if quote is not None:
            return quote
        return self._mock_quote(stock_id)

    def get_history(self, stock_id: str, range_name: str) -> list[CandleRecord]:
        stock = self.get_stock(stock_id)
        if stock is None:
            return []

        if range_name == "1D":
            quote = self._fetch_quote(stock)
            if quote is not None:
                return _approximate_intraday_candles(stock, quote)
            return self._mock_history(stock_id, range_name)

        candles = self._fetch_daily_history(stock, limit=_daily_limit(range_name))
        if candles:
            return candles
        return self._mock_history(stock_id, range_name)

    def _fetch_quote(self, stock: StockRecord) -> QuoteRecord | None:
        try:
            payload = _load_json(
                _REALTIME_URL,
                {
                    "ex_ch": f"{_quote_exchange_prefix(stock.exchange)}_{stock.symbol}.tw",
                    "json": "1",
                    "delay": "0",
                },
                referer="https://mis.twse.com.tw/stock/index.jsp",
            )
        except (HTTPError, URLError, TimeoutError, ValueError):
            return None

        items = payload.get("msgArray")
        if not items:
            return None

        item = items[0]
        last_price = _first_number(item.get("z"), item.get("pz"), item.get("y"))
        previous_close = _parse_float(item.get("y"))
        open_price = _parse_float(item.get("o"))
        high_price = _parse_float(item.get("h"))
        low_price = _parse_float(item.get("l"))
        volume_lots = _parse_int(item.get("v"))
        volume = volume_lots * 1000 if volume_lots is not None else None

        if last_price is None or previous_close in (None, 0):
            return None

        quote_timestamp = _parse_quote_timestamp(
            raw_date=item.get("d"),
            raw_time=item.get("t") or item.get("%") or item.get("ot"),
            timezone_name=stock.timezone,
        )
        change = round(last_price - previous_close, 2)
        change_percent = round((change / previous_close) * 100, 2)

        return QuoteRecord(
            id=stock.id,
            last_price=round(last_price, 2),
            change=change,
            change_percent=change_percent,
            previous_close=round(previous_close, 2),
            open=round(open_price, 2) if open_price is not None else None,
            high=round(high_price, 2) if high_price is not None else None,
            low=round(low_price, 2) if low_price is not None else None,
            volume=volume,
            timestamp=quote_timestamp.isoformat(),
            is_market_open=is_market_open(stock.market, quote_timestamp),
        )

    def _fetch_daily_history(self, stock: StockRecord, limit: int) -> list[CandleRecord]:
        if stock.exchange != "TWSE":
            return []

        today = datetime.now(ZoneInfo(stock.timezone)).date()
        # Fetch enough monthly slices to satisfy the requested candle limit.
        months_to_fetch = max(2, math.ceil(limit / 20) + 1)
        candles_by_time: dict[str, CandleRecord] = {}

        for month_offset in range(months_to_fetch):
            month_date = _month_anchor(today, month_offset)
            try:
                payload = _load_json(
                    _HISTORY_URL,
                    {
                        "response": "json",
                        "date": month_date.strftime("%Y%m01"),
                        "stockNo": stock.symbol,
                    },
                )
            except (HTTPError, URLError, TimeoutError, ValueError):
                continue

            if payload.get("stat") != "OK":
                continue

            for row in payload.get("data", []):
                candle = _parse_daily_row(row, stock.timezone)
                if candle is not None:
                    candles_by_time[candle.time] = candle

        candles = sorted(candles_by_time.values(), key=lambda item: item.time)
        return candles[-limit:]

    def _stock_catalog(self) -> dict[str, StockRecord]:
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        if (
            self.__class__._catalog_cache is not None
            and self.__class__._catalog_cached_at is not None
            and now - self.__class__._catalog_cached_at < _CATALOG_TTL
        ):
            return self.__class__._catalog_cache

        catalog = dict(TW_STOCKS)
        detailed_company_rows: dict[str, StockRecord] = {}
        try:
            payload = _load_json(_COMPANY_LIST_URL, {})
        except (HTTPError, URLError, TimeoutError, ValueError):
            payload = None

        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    continue
                stock = _stock_from_company_row(row)
                if stock is not None:
                    detailed_company_rows[stock.id] = stock
                    catalog[stock.id] = stock

        for html_url in (_ISIN_LISTED_URL, _ISIN_OTC_URL):
            try:
                html = _load_text(html_url)
            except (HTTPError, URLError, TimeoutError, UnicodeDecodeError):
                continue

            for stock in _stocks_from_isin_html(html, detailed_company_rows):
                catalog[stock.id] = stock

        self.__class__._catalog_cache = catalog
        self.__class__._catalog_cached_at = now
        return catalog

    def _mock_quote(self, stock_id: str) -> QuoteRecord | None:
        stock = self.get_stock(stock_id)
        if stock is None:
            return None

        timestamp = datetime.now(ZoneInfo(stock.timezone))
        base_price = _base_price_for_stock(stock_id, stock.symbol)
        drift = math.sin(timestamp.minute / 4) * 6 + math.cos(timestamp.second / 18) * 2
        last_price = round(base_price + drift, 2)
        previous_close = round(base_price - 8.0, 2)
        change = round(last_price - previous_close, 2)
        change_percent = round((change / previous_close) * 100, 2)
        return QuoteRecord(
            id=stock.id,
            last_price=last_price,
            change=change,
            change_percent=change_percent,
            previous_close=previous_close,
            open=round(previous_close + 3.5, 2),
            high=round(last_price + 7.5, 2),
            low=round(last_price - 6.8, 2),
            volume=18_500_000 + timestamp.minute * 28_000,
            timestamp=timestamp.isoformat(),
            is_market_open=is_market_open(stock.market, timestamp),
        )

    def _mock_history(self, stock_id: str, range_name: str) -> list[CandleRecord]:
        stock = self.get_stock(stock_id)
        if stock is None:
            return []

        now = datetime.now(ZoneInfo(stock.timezone))
        step, points = _history_shape(range_name)
        base_price = _base_price_for_stock(stock_id, stock.symbol)
        candles: list[CandleRecord] = []
        for index in range(points):
            point_time = now - step * (points - index)
            wave = math.sin(index / 3.1) * 8.0
            open_price = round(base_price + wave, 2)
            close_price = round(open_price + math.cos(index / 2.0) * 4.5, 2)
            high_price = round(max(open_price, close_price) + 3.2, 2)
            low_price = round(min(open_price, close_price) - 3.0, 2)
            candles.append(
                CandleRecord(
                    time=point_time.isoformat(),
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=2_500_000 + index * 48_000,
                )
            )
        return candles


def _load_json(url: str, params: dict[str, str], referer: str | None = None) -> object:
    query = urlencode(params)
    request = Request(f"{url}?{query}")
    request.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    )
    request.add_header("Accept", "application/json,text/plain,*/*")
    if referer:
        request.add_header("Referer", referer)

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except URLError as exc:
        if not _should_retry_without_tls_verification(url, exc):
            raise

        # TWSE's TLS chain intermittently fails Python's verifier on macOS; retry
        # without certificate validation so the app can still reach the official endpoint.
        insecure_context = ssl._create_unverified_context()
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS, context=insecure_context) as response:
            return json.load(response)


def _load_text(url: str) -> str:
    request = Request(url)
    request.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    )
    request.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return response.read().decode("big5-hkscs", errors="ignore")
    except URLError as exc:
        if not _should_retry_without_tls_verification(url, exc):
            raise

        insecure_context = ssl._create_unverified_context()
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS, context=insecure_context) as response:
            return response.read().decode("big5-hkscs", errors="ignore")


def _first_number(*values: str | None) -> float | None:
    for value in values:
        parsed = _parse_float(value)
        if parsed is not None:
            return parsed
    return None


def _parse_float(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None

    cleaned = raw_value.strip().replace(",", "")
    if not cleaned or cleaned in {"-", "--"}:
        return None

    primary_value = cleaned.split("_", 1)[0]
    try:
        return float(primary_value)
    except ValueError:
        return None


def _parse_int(raw_value: str | None) -> int | None:
    number = _parse_float(raw_value)
    return int(number) if number is not None else None


def _parse_quote_timestamp(raw_date: str | None, raw_time: str | None, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if not raw_date or not raw_time:
        return datetime.now(tz)

    try:
        return datetime.strptime(f"{raw_date}{raw_time}", "%Y%m%d%H:%M:%S").replace(tzinfo=tz)
    except ValueError:
        return datetime.now(tz)


def _should_retry_without_tls_verification(url: str, exc: URLError) -> bool:
    if not url.startswith("https://") or "twse.com.tw" not in url:
        return False
    return isinstance(exc.reason, ssl.SSLCertVerificationError)


def _quote_exchange_prefix(exchange: str) -> str:
    return "otc" if exchange == "TPEx" else "tse"


def _month_anchor(today: date, month_offset: int) -> date:
    year = today.year
    month = today.month - month_offset

    while month <= 0:
        month += 12
        year -= 1

    return date(year, month, 1)


def _daily_limit(range_name: str) -> int:
    if range_name == "1W":
        return 7
    if range_name == "1M":
        return 30
    if range_name == "3M":
        return 65
    if range_name == "6M":
        return 130
    if range_name == "1Y":
        return 260
    return 30


def _parse_daily_row(row: list[str], timezone_name: str) -> CandleRecord | None:
    if len(row) < 8:
        return None

    trade_date = _parse_minguo_date(row[0], timezone_name)
    open_price = _parse_float(row[3])
    high_price = _parse_float(row[4])
    low_price = _parse_float(row[5])
    close_price = _parse_float(row[6])
    volume = _parse_int(row[1])

    if trade_date is None or None in {open_price, high_price, low_price, close_price, volume}:
        return None

    return CandleRecord(
        time=trade_date.isoformat(),
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
    )


def _parse_minguo_date(raw_date: str, timezone_name: str) -> datetime | None:
    try:
        year, month, day = (int(part) for part in raw_date.split("/"))
    except ValueError:
        return None

    return datetime(year + 1911, month, day, 13, 30, tzinfo=ZoneInfo(timezone_name))


def _stock_from_company_row(row: dict) -> StockRecord | None:
    symbol = str(row.get("公司代號", "")).strip()
    if not symbol.isdigit():
        return None

    short_name = str(row.get("公司簡稱", "")).strip()
    full_name = str(row.get("公司名稱", "")).strip()
    english_name = str(row.get("英文簡稱", "")).strip() or None
    display_name = short_name or full_name
    if not display_name:
        return None

    return StockRecord(
        id=f"TW:{symbol}",
        market="TW",
        symbol=symbol,
        name=display_name,
        name_en=english_name,
        name_zh_tw=full_name or display_name,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _stocks_from_isin_html(html: str, detailed_company_rows: dict[str, StockRecord]) -> list[StockRecord]:
    pattern = re.compile(
        r"<tr><td bgcolor=#FAFAD2>([^<]+)</td>"
        r"<td bgcolor=#FAFAD2>[^<]*</td>"
        r"<td bgcolor=#FAFAD2>[^<]*</td>"
        r"<td bgcolor=#FAFAD2>([^<]*)</td>"
        r"<td bgcolor=#FAFAD2>[^<]*</td>"
        r"<td bgcolor=#FAFAD2>([^<]*)</td>"
        r"<td bgcolor=#FAFAD2>[^<]*</td></tr>"
    )

    stocks: list[StockRecord] = []
    for code_and_name, market_label, cfi_code in pattern.findall(html):
        stock = _stock_from_isin_row(code_and_name, market_label, cfi_code, detailed_company_rows)
        if stock is not None:
            stocks.append(stock)
    return stocks


def _stock_from_isin_row(
    code_and_name: str,
    market_label: str,
    cfi_code: str,
    detailed_company_rows: dict[str, StockRecord],
) -> StockRecord | None:
    normalized_market = market_label.strip()
    normalized_cfi = cfi_code.strip().upper()
    if normalized_market not in {"上市", "上櫃"} or not normalized_cfi.startswith("E"):
        return None

    parts = code_and_name.replace("&nbsp;", " ").split()
    if len(parts) < 2:
        return None

    symbol = parts[0].strip().upper()
    if not symbol:
        return None

    short_name = "".join(parts[1:]).strip()
    if not short_name:
        return None

    stock_id = f"TW:{symbol}"
    detailed = detailed_company_rows.get(stock_id)
    exchange = "TPEx" if normalized_market == "上櫃" else "TWSE"

    return StockRecord(
        id=stock_id,
        market="TW",
        symbol=symbol,
        name=short_name,
        name_en=detailed.name_en if detailed is not None else None,
        name_zh_tw=detailed.name_zh_tw if detailed is not None else short_name,
        exchange=exchange,
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _base_price_for_stock(stock_id: str, symbol: str) -> float:
    if stock_id in BASE_PRICES:
        return BASE_PRICES[stock_id]

    seed = sum(ord(character) for character in symbol)
    return round(18 + (seed % 1480) + ((seed // 9) % 100) / 100, 2)


def _approximate_intraday_candles(stock: StockRecord, quote: QuoteRecord) -> list[CandleRecord]:
    tz = ZoneInfo(stock.timezone)
    quote_time = datetime.fromisoformat(quote.timestamp).astimezone(tz)
    market_open_time = datetime.combine(quote_time.date(), time(9, 0), tzinfo=tz)
    market_close_time = datetime.combine(quote_time.date(), time(13, 30), tzinfo=tz)

    session_end = min(max(quote_time, market_open_time), market_close_time)
    total_minutes = max(int((session_end - market_open_time).total_seconds() // 60), 1)
    points = 32

    start_price = quote.open or quote.previous_close
    end_price = quote.last_price
    high_price = max(filter(None, [quote.high, start_price, end_price]))
    low_price = min(filter(None, [quote.low, start_price, end_price]))
    total_volume = quote.volume or 0

    candles: list[CandleRecord] = []
    accumulated_volume = 0
    for index in range(points):
        progress = (index + 1) / points
        candle_minutes = int(total_minutes * progress)
        candle_time = market_open_time + timedelta(minutes=candle_minutes)

        baseline = start_price + (end_price - start_price) * progress
        wave = math.sin(progress * math.pi * 2.6) * max(high_price - low_price, 0.01) * 0.06
        close_price = round(min(max(baseline + wave, low_price), high_price), 2)
        open_price = start_price if index == 0 else candles[-1].close
        candle_high = round(max(open_price, close_price), 2)
        candle_low = round(min(open_price, close_price), 2)

        next_volume_total = int(total_volume * progress)
        candle_volume = max(next_volume_total - accumulated_volume, 0)
        accumulated_volume = next_volume_total

        candles.append(
            CandleRecord(
                time=candle_time.isoformat(),
                open=round(open_price, 2),
                high=candle_high,
                low=candle_low,
                close=close_price,
                volume=candle_volume,
            )
        )

    if candles:
        candles[-1] = CandleRecord(
            time=candles[-1].time,
            open=candles[-1].open,
            high=round(max(candles[-1].open, quote.last_price, high_price), 2),
            low=round(min(candles[-1].open, quote.last_price, low_price), 2),
            close=round(quote.last_price, 2),
            volume=max(total_volume - sum(candle.volume for candle in candles[:-1]), 0),
        )

    return candles


def _history_shape(range_name: str) -> tuple[timedelta, int]:
    if range_name == "1W":
        return timedelta(hours=4), 28
    if range_name == "1M":
        return timedelta(days=1), 30
    if range_name == "3M":
        return timedelta(days=1), 90
    if range_name == "6M":
        return timedelta(days=1), 180
    if range_name == "1Y":
        return timedelta(days=2), 182
    return timedelta(minutes=10), 32
