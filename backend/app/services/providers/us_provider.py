from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError, fetch_json, fetch_text
from app.services.market_clock import is_market_open


US_STOCKS: dict[str, StockRecord] = {
    "US:AAPL": StockRecord(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        name_en="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:MSFT": StockRecord(
        id="US:MSFT",
        market="US",
        symbol="MSFT",
        name="Microsoft Corporation",
        name_en="Microsoft Corporation",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:NVDA": StockRecord(
        id="US:NVDA",
        market="US",
        symbol="NVDA",
        name="NVIDIA Corporation",
        name_en="NVIDIA Corporation",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:MU": StockRecord(
        id="US:MU",
        market="US",
        symbol="MU",
        name="Micron Technology, Inc.",
        name_en="Micron Technology, Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:TSLA": StockRecord(
        id="US:TSLA",
        market="US",
        symbol="TSLA",
        name="Tesla, Inc.",
        name_en="Tesla, Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:AMD": StockRecord(
        id="US:AMD",
        market="US",
        symbol="AMD",
        name="Advanced Micro Devices, Inc.",
        name_en="Advanced Micro Devices, Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:META": StockRecord(
        id="US:META",
        market="US",
        symbol="META",
        name="Meta Platforms, Inc.",
        name_en="Meta Platforms, Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:AMZN": StockRecord(
        id="US:AMZN",
        market="US",
        symbol="AMZN",
        name="Amazon.com, Inc.",
        name_en="Amazon.com, Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
    "US:GOOGL": StockRecord(
        id="US:GOOGL",
        market="US",
        symbol="GOOGL",
        name="Alphabet Inc.",
        name_en="Alphabet Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    ),
}

_CATALOG_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
_CATALOG_TTL = timedelta(hours=12)
_REQUEST_TIMEOUT_SECONDS = 10
_USER_AGENT = "stock-ticker-app/0.1 contact@example.com"
_STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
_YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
# v7 quote often returns Unauthorized for server-side requests; v8 chart is more reliable.
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class USProvider:
    market = "US"
    _catalog_cache: dict[str, StockRecord] | None = None
    _catalog_cached_at: datetime | None = None

    def list_stocks(self) -> list[StockRecord]:
        return list(self._stock_catalog().values())

    def search(self, query: str) -> list[StockRecord]:
        normalized = query.strip().lower()
        symbol = query.strip().upper()
        symbol_matches = [
            stock
            for stock in self.list_stocks()
            if symbol and stock.symbol.upper().startswith(symbol)
        ]
        if symbol_matches:
            # If the query maps to symbol prefixes, keep results ticker-focused
            # and avoid unrelated company-name substring matches.
            matches = symbol_matches
        else:
            matches = [
                stock
                for stock in self.list_stocks()
                if normalized in stock.symbol.lower() or normalized in stock.name.lower()
            ]
        matches.sort(key=lambda stock: _search_rank(stock, normalized, symbol))
        return matches

    def get_stock(self, stock_id: str) -> StockRecord | None:
        return self._stock_catalog().get(stock_id)

    def get_quote(self, stock_id: str) -> QuoteRecord | None:
        return self.get_live_quote(stock_id)

    def get_live_quote(self, stock_id: str) -> QuoteRecord | None:
        stock = self.get_stock(stock_id)
        if stock is None:
            return None

        return _fetch_live_quote(stock)

    def get_history(self, stock_id: str, range_name: str) -> list[CandleRecord]:
        stock = self.get_stock(stock_id)
        if stock is None:
            return []

        live_history = _fetch_yahoo_history(stock, range_name)
        if live_history:
            return live_history
        return []

    def _stock_catalog(self) -> dict[str, StockRecord]:
        now = datetime.now(ZoneInfo("UTC"))
        if (
            self.__class__._catalog_cache is not None
            and self.__class__._catalog_cached_at is not None
            and now - self.__class__._catalog_cached_at < _CATALOG_TTL
        ):
            return self.__class__._catalog_cache

        catalog = dict(US_STOCKS)
        try:
            payload = _load_catalog_payload()
        except (HttpFetchError, TimeoutError, ValueError):
            self.__class__._catalog_cache = catalog
            self.__class__._catalog_cached_at = now
            return catalog

        fields = payload.get("fields", [])
        rows = payload.get("data", [])
        field_positions = {field: index for index, field in enumerate(fields)}
        ticker_index = field_positions.get("ticker")
        name_index = field_positions.get("name")
        exchange_index = field_positions.get("exchange")

        if None in {ticker_index, name_index, exchange_index}:
            self.__class__._catalog_cache = catalog
            self.__class__._catalog_cached_at = now
            return catalog

        for row in rows:
            if not isinstance(row, list):
                continue
            stock = _stock_from_sec_row(row, ticker_index, name_index, exchange_index)
            if stock is not None:
                catalog[stock.id] = stock

        self.__class__._catalog_cache = catalog
        self.__class__._catalog_cached_at = now
        return catalog


def _load_catalog_payload() -> dict:
    return fetch_json(
        _CATALOG_URL,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=2,
    )


def _load_json_with_headers(url: str, params: dict[str, str], headers: dict[str, str]) -> dict:
    return fetch_json(
        url,
        params=params,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=2,
    )


def _load_text_with_headers(url: str, params: dict[str, str], headers: dict[str, str]) -> str:
    return fetch_text(
        url,
        params=params,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=2,
        encoding="utf-8",
    )


def _load_json_get(url: str, headers: dict[str, str]) -> dict:
    return fetch_json(
        url,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=2,
    )


def _stock_from_sec_row(
    row: list[object],
    ticker_index: int,
    name_index: int,
    exchange_index: int,
) -> StockRecord | None:
    if max(ticker_index, name_index, exchange_index) >= len(row):
        return None

    ticker = str(row[ticker_index]).strip().upper()
    name = str(row[name_index]).strip()
    exchange = str(row[exchange_index]).strip().upper()

    if not ticker or not name:
        return None

    return StockRecord(
        id=f"US:{ticker}",
        market="US",
        symbol=ticker,
        name=name,
        name_en=name,
        exchange=exchange or "US",
        currency="USD",
        timezone="America/New_York",
    )


def _looks_like_symbol(symbol: str) -> bool:
    return bool(_SYMBOL_PATTERN.match(symbol))


def _search_rank(stock: StockRecord, normalized_query: str, raw_symbol_query: str) -> tuple[int, str]:
    symbol = stock.symbol.upper()
    name = stock.name.lower()
    if raw_symbol_query and symbol == raw_symbol_query:
        return (0, symbol)
    if raw_symbol_query and symbol.startswith(raw_symbol_query):
        return (1, symbol)
    if normalized_query and normalized_query in name:
        return (2, symbol)
    return (3, symbol)


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


def _fetch_yahoo_chart_quote(stock: StockRecord) -> QuoteRecord | None:
    """Primary US quote: Yahoo chart API (works when v7 quote is blocked)."""
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    tz = ZoneInfo(stock.timezone)
    for candidate in (stock.symbol, f"{stock.symbol}.US"):
        path_symbol = quote(candidate, safe="-.")
        url = f"{_YAHOO_CHART_URL}/{path_symbol}?interval=1d&range=5d"
        try:
            payload = _load_json_get(url, headers)
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
            high=round(float(high_raw), 2) if isinstance(high_raw, (int, float)) else None,
            low=round(float(low_raw), 2) if isinstance(low_raw, (int, float)) else None,
            volume=volume,
            timestamp=quote_time.isoformat(),
            is_market_open=is_market_open(stock.market, quote_time),
        )

    return None


def _yahoo_v7_request_symbol(symbol: str) -> str:
    """Yahoo v7 ``symbols=`` prefers hyphens for class‑B style tickers (e.g. ``BRK-B``)."""
    return symbol.strip().upper().replace(".", "-")


def quote_from_yahoo_v7_item(item: dict, stock: StockRecord) -> QuoteRecord | None:
    """Build a :class:`QuoteRecord` from one Yahoo Finance v7 ``quote`` result row."""
    last_price = item.get("regularMarketPrice")
    previous_close = item.get("regularMarketPreviousClose")
    if not isinstance(last_price, (int, float)) or not isinstance(previous_close, (int, float)) or previous_close == 0:
        return None

    market_time = item.get("regularMarketTime")
    quote_time = (
        datetime.fromtimestamp(market_time, tz=ZoneInfo("America/New_York"))
        if isinstance(market_time, (int, float))
        else datetime.now(ZoneInfo("America/New_York"))
    )
    change = round(float(last_price) - float(previous_close), 2)
    change_percent = round((change / float(previous_close)) * 100, 2)

    volume_raw = item.get("regularMarketVolume")
    volume = int(volume_raw) if isinstance(volume_raw, (int, float)) else None
    open_raw = item.get("regularMarketOpen")
    high_raw = item.get("regularMarketDayHigh")
    low_raw = item.get("regularMarketDayLow")

    return QuoteRecord(
        id=stock.id,
        last_price=round(float(last_price), 2),
        change=change,
        change_percent=change_percent,
        previous_close=round(float(previous_close), 2),
        open=round(float(open_raw), 2) if isinstance(open_raw, (int, float)) else None,
        high=round(float(high_raw), 2) if isinstance(high_raw, (int, float)) else None,
        low=round(float(low_raw), 2) if isinstance(low_raw, (int, float)) else None,
        volume=volume,
        timestamp=quote_time.isoformat(),
        is_market_open=is_market_open(stock.market, quote_time),
    )


def yahoo_v7_quotes_batch(stocks: list[StockRecord]) -> dict[str, QuoteRecord]:
    """Fetch Yahoo v7 quotes for many tickers using a few batched HTTP calls.

    Keys are ``stock.id`` (``US:SYMBOL``). Missing or invalid rows are omitted.
    """
    if not stocks:
        return {}
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    out: dict[str, QuoteRecord] = {}
    chunk_size = 40
    for start in range(0, len(stocks), chunk_size):
        chunk = stocks[start : start + chunk_size]
        symbols_param = ",".join(_yahoo_v7_request_symbol(s.symbol) for s in chunk)
        try:
            payload = _load_json_with_headers(
                _YAHOO_QUOTE_URL,
                {"symbols": symbols_param},
                headers,
            )
        except (HttpFetchError, TimeoutError, ValueError):
            continue
        quote_response = payload.get("quoteResponse")
        if not isinstance(quote_response, dict) or quote_response.get("error"):
            continue
        result = quote_response.get("result")
        if not isinstance(result, list):
            continue

        def _match_stock(yahoo_symbol: object) -> StockRecord | None:
            if not isinstance(yahoo_symbol, str):
                return None
            yn = _yahoo_v7_request_symbol(yahoo_symbol)
            for st in chunk:
                if _yahoo_v7_request_symbol(st.symbol) == yn:
                    return st
            return None

        for item in result:
            if not isinstance(item, dict):
                continue
            y_sym = item.get("symbol")
            st = _match_stock(y_sym)
            if st is None:
                continue
            quote = quote_from_yahoo_v7_item(item, st)
            if quote is not None:
                out[st.id] = quote
    return out


def _fetch_yahoo_v7_quote(stock: StockRecord) -> QuoteRecord | None:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    item: dict | None = None
    for candidate in (stock.symbol, f"{stock.symbol}.US"):
        try:
            payload = _load_json_with_headers(
                _YAHOO_QUOTE_URL,
                {"symbols": candidate},
                headers,
            )
        except (HttpFetchError, TimeoutError, ValueError):
            continue

        quote_response = payload.get("quoteResponse")
        if not isinstance(quote_response, dict):
            continue
        if quote_response.get("error"):
            continue
        result = quote_response.get("result")
        if not isinstance(result, list) or not result:
            continue
        first = result[0]
        if isinstance(first, dict):
            item = first
            break

    if item is None:
        return None
    return quote_from_yahoo_v7_item(item, stock)


def _fetch_live_quote(stock: StockRecord) -> QuoteRecord | None:
    # Stooq first caused wrong/stale prices for some tickers (e.g. SIMO). Prefer Yahoo chart.
    for fetcher in (_fetch_yahoo_chart_quote, _fetch_yahoo_v7_quote, _fetch_stooq_quote):
        quote = fetcher(stock)
        if quote is not None:
            return quote
    return None


def _fetch_yahoo_history(stock: StockRecord, range_name: str) -> list[CandleRecord]:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    interval, range_param = _yahoo_history_window(range_name)
    for candidate in (stock.symbol, f"{stock.symbol}.US"):
        path_symbol = quote(candidate, safe="-.")
        url = f"{_YAHOO_CHART_URL}/{path_symbol}?interval={interval}&range={range_param}"
        try:
            payload = _load_json_get(url, headers)
        except (HttpFetchError, TimeoutError, ValueError):
            continue

        chart = payload.get("chart")
        if not isinstance(chart, dict) or chart.get("error"):
            continue
        results = chart.get("result")
        if not isinstance(results, list) or not results:
            continue
        block = results[0]
        if not isinstance(block, dict):
            continue
        timestamps = block.get("timestamp")
        if not isinstance(timestamps, list) or not timestamps:
            continue

        indicators = block.get("indicators")
        if not isinstance(indicators, dict):
            continue
        quotes = indicators.get("quote")
        if not isinstance(quotes, list) or not quotes:
            continue
        first = quotes[0]
        if not isinstance(first, dict):
            continue

        opens = first.get("open")
        highs = first.get("high")
        lows = first.get("low")
        closes = first.get("close")
        volumes = first.get("volume")
        if not all(isinstance(series, list) for series in (opens, highs, lows, closes)):
            continue
        if not isinstance(volumes, list):
            volumes = [None] * len(timestamps)

        meta = block.get("meta")
        tz_name = (
            meta.get("exchangeTimezoneName")
            if isinstance(meta, dict)
            and isinstance(meta.get("exchangeTimezoneName"), str)
            else stock.timezone
        )
        tz = ZoneInfo(tz_name)

        candles: list[CandleRecord] = []
        for i, ts in enumerate(timestamps):
            if not isinstance(ts, (int, float)):
                continue
            if i >= len(opens) or i >= len(highs) or i >= len(lows) or i >= len(closes):
                continue
            open_value = opens[i]
            high_value = highs[i]
            low_value = lows[i]
            close_value = closes[i]
            if not all(
                isinstance(v, (int, float))
                for v in (open_value, high_value, low_value, close_value)
            ):
                continue
            vol_raw = volumes[i] if i < len(volumes) else None
            vol = int(vol_raw) if isinstance(vol_raw, (int, float)) else 0
            candles.append(
                CandleRecord(
                    time=datetime.fromtimestamp(ts, tz=tz).isoformat(),
                    open=round(float(open_value), 2),
                    high=round(float(high_value), 2),
                    low=round(float(low_value), 2),
                    close=round(float(close_value), 2),
                    volume=vol,
                )
            )

        if candles:
            return candles

    return []


def _yahoo_history_window(range_name: str) -> tuple[str, str]:
    if range_name == "1D":
        return "5m", "1d"
    if range_name == "1W":
        return "30m", "5d"
    if range_name == "1M":
        return "1d", "1mo"
    if range_name == "3M":
        return "1d", "3mo"
    if range_name == "6M":
        return "1d", "6mo"
    if range_name == "1Y":
        return "1d", "1y"
    return "1d", "1mo"


def _fetch_stooq_quote(stock: StockRecord) -> QuoteRecord | None:
    symbol = f"{stock.symbol.lower()}.us"
    try:
        csv_payload = _load_text_with_headers(
            _STOOQ_QUOTE_URL,
            {"s": symbol, "i": "d"},
            {
                "User-Agent": _BROWSER_USER_AGENT,
                "Accept": "text/csv,text/plain,*/*",
            },
        )
    except (HttpFetchError, TimeoutError, ValueError):
        return None

    if "Exceeded the daily hits limit" in csv_payload or "N/D" == csv_payload.strip():
        return None

    lines = [line.strip() for line in csv_payload.strip().splitlines() if line.strip()]
    columns: list[str] | None = None
    date_text = ""
    for line in reversed(lines):
        cols = line.split(",")
        if len(cols) < 8:
            continue
        dt = cols[1].strip()
        if len(dt) != 8 or not dt.isdigit():
            continue
        close_value = _parse_number(cols[6])
        if close_value is None:
            continue
        sym_cell = cols[0].strip()
        if sym_cell and _normalize_stooq_symbol_cell(sym_cell) != stock.symbol.upper():
            continue
        columns = cols
        date_text = dt
        break

    if columns is None:
        return None

    open_value = _parse_number(columns[3])
    high_value = _parse_number(columns[4])
    low_value = _parse_number(columns[5])
    close_value = _parse_number(columns[6])
    volume_value = _parse_int_number(columns[7])
    if close_value is None:
        return None

    previous_close = open_value if open_value is not None else close_value
    change = round(close_value - previous_close, 2)
    change_percent = round((change / previous_close) * 100, 2) if previous_close else 0.0

    quote_time = _parse_stooq_timestamp(date_text, stock.timezone)
    return QuoteRecord(
        id=stock.id,
        last_price=round(close_value, 2),
        change=change,
        change_percent=change_percent,
        previous_close=round(previous_close, 2),
        open=round(open_value, 2) if open_value is not None else None,
        high=round(high_value, 2) if high_value is not None else None,
        low=round(low_value, 2) if low_value is not None else None,
        volume=volume_value,
        timestamp=quote_time.isoformat(),
        is_market_open=is_market_open(stock.market, quote_time),
    )


def _parse_number(raw_value: str) -> float | None:
    cleaned = raw_value.strip()
    if not cleaned or cleaned in {"N/D", "-"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int_number(raw_value: str) -> int | None:
    number = _parse_number(raw_value)
    return int(number) if number is not None else None


def _normalize_stooq_symbol_cell(sym_cell: str) -> str:
    normalized = sym_cell.strip().upper()
    for suffix in (".US", ".U"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def _parse_stooq_timestamp(raw_date: str, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    try:
        return datetime.strptime(raw_date.strip(), "%Y%m%d").replace(hour=16, minute=0, tzinfo=tz)
    except ValueError:
        return datetime.now(tz)
