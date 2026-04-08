from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.http_client import HttpFetchError, fetch_json, fetch_text
from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.market_clock import is_market_open


TW_STOCKS: dict[str, StockRecord] = {
    "TW:0050": StockRecord(
        id="TW:0050",
        market="TW",
        symbol="0050",
        name="元大台灣50",
        name_en="Yuanta Taiwan 50 ETF",
        name_zh_tw="元大台灣50",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
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
    "TW:0050": 191.0,
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
_QUOTE_CACHE_TTL = timedelta(minutes=15)
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _yahoo_tw_symbol_candidates(stock: StockRecord) -> tuple[str, ...]:
    sym = stock.symbol.strip()
    if stock.exchange == "TPEx":
        return (f"{sym}.TWO", f"{sym}.TW")
    return (f"{sym}.TW", f"{sym}.TWO")


def _previous_close_from_chart_block(block: dict) -> float | None:
    quotes = block.get("indicators", {}).get("quote")
    if not isinstance(quotes, list) or not quotes:
        return None
    closes = quotes[0].get("close")
    if not isinstance(closes, list):
        return None
    numeric = [float(c) for c in closes if isinstance(c, (int, float))]
    if len(numeric) < 2:
        return None
    return numeric[-2]


class TWProvider:
    market = "TW"
    _catalog_cache: dict[str, StockRecord] | None = None
    _catalog_cached_at: datetime | None = None
    _quote_cache: dict[str, QuoteRecord] = {}
    _quote_cached_at: dict[str, datetime] = {}

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

        if settings.tw_use_mock_quotes:
            return self._mock_quote(stock_id)

        quote = self._fetch_quote(stock)
        if quote is not None:
            self.__class__._quote_cache[stock_id] = quote
            self.__class__._quote_cached_at[stock_id] = datetime.now(
                ZoneInfo(stock.timezone)
            )
            return quote
        quote = self._fetch_yahoo_tw_quote(stock)
        if quote is not None:
            self.__class__._quote_cache[stock_id] = quote
            self.__class__._quote_cached_at[stock_id] = datetime.now(
                ZoneInfo(stock.timezone)
            )
            return quote
        cached_quote = self._cached_quote(stock)
        if cached_quote is not None:
            return cached_quote
        if settings.tw_use_mock_quotes:
            return self._mock_quote(stock_id)
        return None

    def get_history(self, stock_id: str, range_name: str) -> list[CandleRecord]:
        stock = self.get_stock(stock_id)
        if stock is None:
            return []

        if settings.tw_use_mock_quotes:
            return self._mock_history(stock_id, range_name)

        if range_name == "1D":
            quote = self._fetch_quote(stock)
            if quote is not None:
                self.__class__._quote_cache[stock_id] = quote
                self.__class__._quote_cached_at[stock_id] = datetime.now(
                    ZoneInfo(stock.timezone)
                )
                return _approximate_intraday_candles(stock, quote)
            quote = self._fetch_yahoo_tw_quote(stock)
            if quote is not None:
                self.__class__._quote_cache[stock_id] = quote
                self.__class__._quote_cached_at[stock_id] = datetime.now(
                    ZoneInfo(stock.timezone)
                )
                return _approximate_intraday_candles(stock, quote)
            cached_quote = self._cached_quote(stock)
            if cached_quote is not None:
                return _approximate_intraday_candles(stock, cached_quote)
            if settings.tw_use_mock_quotes:
                return self._mock_history(stock_id, range_name)
            return []

        candles = self._fetch_daily_history(stock, limit=_daily_limit(range_name))
        if candles:
            return candles
        if settings.tw_use_mock_quotes:
            return self._mock_history(stock_id, range_name)
        return []

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
        except (HttpFetchError, TimeoutError, ValueError):
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

    def _fetch_yahoo_tw_quote(self, stock: StockRecord) -> QuoteRecord | None:
        """Yahoo chart meta when TWSE mis API is flaky or blocked."""
        headers = {
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        }
        tz = ZoneInfo(stock.timezone)
        for candidate in _yahoo_tw_symbol_candidates(stock):
            path_symbol = quote(candidate, safe="-.")
            url = f"{_YAHOO_CHART_URL}/{path_symbol}?interval=1d&range=5d"
            try:
                payload = fetch_json(
                    url,
                    headers=headers,
                    timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                    retries=2,
                )
            except (HttpFetchError, TimeoutError, ValueError):
                continue

            chart = payload.get("chart")
            if not isinstance(chart, dict) or chart.get("error"):
                continue
            results = chart.get("result")
            if not isinstance(results, list) or not results:
                continue
            block = results[0]
            meta = block.get("meta")
            if not isinstance(meta, dict):
                continue

            last_price = meta.get("regularMarketPrice")
            if not isinstance(last_price, (int, float)):
                continue

            previous_close = meta.get("chartPreviousClose")
            if not isinstance(previous_close, (int, float)) or previous_close == 0:
                alt = meta.get("previousClose")
                if isinstance(alt, (int, float)) and alt != 0:
                    previous_close = alt
                else:
                    inferred = _previous_close_from_chart_block(block)
                    if inferred is None or inferred == 0:
                        continue
                    previous_close = inferred

            market_time = meta.get("regularMarketTime")
            quote_time = (
                datetime.fromtimestamp(market_time, tz=tz)
                if isinstance(market_time, (int, float))
                else datetime.now(tz)
            )
            change = round(float(last_price) - float(previous_close), 2)
            change_percent = round((change / float(previous_close)) * 100, 2)

            volume_raw = meta.get("regularMarketVolume")
            volume = int(volume_raw) if isinstance(volume_raw, (int, float)) else None
            high_raw = meta.get("regularMarketDayHigh")
            low_raw = meta.get("regularMarketDayLow")

            return QuoteRecord(
                id=stock.id,
                last_price=round(float(last_price), 2),
                change=change,
                change_percent=change_percent,
                previous_close=round(float(previous_close), 2),
                open=None,
                high=round(float(high_raw), 2)
                if isinstance(high_raw, (int, float))
                else None,
                low=round(float(low_raw), 2)
                if isinstance(low_raw, (int, float))
                else None,
                volume=volume,
                timestamp=quote_time.isoformat(),
                is_market_open=is_market_open(stock.market, quote_time),
            )

        return None

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
            except (HttpFetchError, TimeoutError, ValueError):
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
        if settings.tw_use_mock_quotes:
            return dict(TW_STOCKS)

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
        except (HttpFetchError, TimeoutError, ValueError):
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
            except (HttpFetchError, TimeoutError, UnicodeDecodeError):
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

    def _cached_quote(self, stock: StockRecord) -> QuoteRecord | None:
        cached_quote = self.__class__._quote_cache.get(stock.id)
        cached_at = self.__class__._quote_cached_at.get(stock.id)
        if cached_quote is None or cached_at is None:
            return None

        now = datetime.now(ZoneInfo(stock.timezone))
        if now - cached_at > _QUOTE_CACHE_TTL:
            self.__class__._quote_cache.pop(stock.id, None)
            self.__class__._quote_cached_at.pop(stock.id, None)
            return None
        return cached_quote


def _load_json(url: str, params: dict[str, str], referer: str | None = None) -> object:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }
    if referer:
        headers["Referer"] = referer

    return fetch_json(
        url,
        params=params,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=2,
        allow_insecure_fallback_hosts={"mis.twse.com.tw", "www.twse.com.tw"},
    )


def _load_text(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    return fetch_text(
        url,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=2,
        encoding="big5-hkscs",
        allow_insecure_fallback_hosts={"isin.twse.com.tw"},
    )


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
    # Include both common shares (E*) and fund/ETF-style instruments (C*).
    if normalized_market not in {"上市", "上櫃"} or not normalized_cfi.startswith(("E", "C")):
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
