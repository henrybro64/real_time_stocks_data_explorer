from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError, fetch_json
from app.services.market_clock import is_market_open
from app.services.providers.yahoo_chart import (
    coherent_price_change,
    market_time,
    previous_session_close,
)

logger = logging.getLogger(__name__)


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
_YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
# v7 quote often returns Unauthorized for server-side requests; v8 chart is more reliable.
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_US_YAHOO_QUOTE_TYPES = frozenset({"EQUITY", "ETF"})
_REJECTED_YAHOO_QUOTE_TYPES = frozenset(
    {
        "CRYPTOCURRENCY",
        "CURRENCY",
        "FUTURE",
        "INDEX",
        "OPTION",
        "MUTUALFUND",
        "MONEYMARKET",
    },
)
_REJECTED_YAHOO_EXCHANGE_MARKERS = (
    "CRYPTO",
    "CCC",
    "FOREX",
    "CURRENCY",
    " CBOE CA",
    "CBOE CA",
)
_US_LISTED_EXCHANGES = frozenset({"NYSE", "NASDAQ", "AMEX", "CBOE"})
_CRYPTO_PAIR_SUFFIXES = frozenset({"USD", "EUR", "GBP", "JPY", "USDT", "BTC", "ETH"})


class USProvider:
    market = "US"
    _catalog_cache: dict[str, StockRecord] | None = None
    _catalog_cached_at: datetime | None = None

    def list_stocks(self) -> list[StockRecord]:
        return list(self._stock_catalog().values())

    def search(self, query: str) -> list[StockRecord]:
        search_query = _strip_us_prefix(query)
        normalized = search_query.lower()
        normalized_symbol = _normalize_us_symbol(query)
        symbol = normalized_symbol or search_query.upper()
        stocks = self.list_stocks()
        symbol_matches = [
            stock
            for stock in stocks
            if symbol and stock.symbol.upper().startswith(symbol)
        ]

        dynamic_stock: StockRecord | None = None
        if normalized_symbol and (not symbol_matches or len(normalized_symbol) > 2):
            dynamic_stock = self.get_stock(f"US:{normalized_symbol}")

        if symbol_matches:
            matches = list(symbol_matches)
        elif dynamic_stock is not None:
            matches = [dynamic_stock]
        else:
            matches = [
                stock
                for stock in stocks
                if normalized in stock.symbol.lower() or normalized in stock.name.lower()
            ]

        if dynamic_stock is not None:
            matches = [dynamic_stock] + [
                stock for stock in matches if stock.id != dynamic_stock.id
            ]

        rank_symbol = normalized_symbol or symbol
        matches.sort(key=lambda stock: _search_rank(stock, normalized, rank_symbol))
        return matches

    def get_stock(self, stock_id: str) -> StockRecord | None:
        normalized_stock_id = _normalize_us_stock_id(stock_id)
        if normalized_stock_id is None:
            return None

        catalog = self._stock_catalog()
        stock = catalog.get(normalized_stock_id)
        if stock is not None:
            return stock

        dynamic_stock = _fetch_yahoo_chart_stock(normalized_stock_id)
        if dynamic_stock is None:
            return None
        catalog[dynamic_stock.id] = dynamic_stock
        self.__class__._catalog_cache = catalog
        return dynamic_stock

    def get_catalog_stock(self, stock_id: str) -> StockRecord | None:
        """Return a catalog/built-in name only; never fetch Yahoo chart metadata."""
        normalized_stock_id = _normalize_us_stock_id(stock_id)
        if normalized_stock_id is None:
            return None
        return self._stock_catalog().get(normalized_stock_id)

    def get_quote(self, stock_id: str) -> QuoteRecord | None:
        return self.get_live_quote(stock_id)

    def get_live_quote(self, stock_id: str) -> QuoteRecord | None:
        stock = self.get_stock(stock_id)
        if stock is None:
            return None

        return _fetch_live_quote(stock)

    def get_history(
        self,
        stock_id: str,
        range_name: str,
        *,
        limit: int | None = None,
    ) -> list[CandleRecord]:
        stock = self.get_stock(stock_id)
        if stock is None:
            return []

        live_history = _fetch_yahoo_history(stock, range_name, min_candles=limit)
        if live_history and limit is not None:
            return live_history[-limit:]
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


def _load_json_with_headers(
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    *,
    retries: int = 2,
) -> dict:
    return fetch_json(
        url,
        params=params,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        retries=retries,
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


def _looks_like_crypto_pair_symbol(symbol: str) -> bool:
    upper = symbol.upper()
    if "-" not in upper:
        return False
    _, _, suffix = upper.partition("-")
    if not suffix:
        return False
    # Class shares such as BRK-B use a single-letter suffix.
    if len(suffix) == 1 and suffix.isalpha():
        return False
    return suffix in _CRYPTO_PAIR_SUFFIXES


def _yahoo_quote_kind(meta: dict[object, object]) -> str | None:
    for key in ("quoteType", "instrumentType"):
        raw = meta.get(key)
        if isinstance(raw, str):
            kind = raw.strip().upper()
            if kind:
                return kind
    return None


def _yahoo_meta_supports_us_equity_or_etf(meta: dict[object, object]) -> bool:
    kind = _yahoo_quote_kind(meta)
    if kind is not None:
        if kind in _REJECTED_YAHOO_QUOTE_TYPES:
            return False
        if kind not in _US_YAHOO_QUOTE_TYPES:
            return False

    currency_raw = meta.get("currency")
    if isinstance(currency_raw, str) and currency_raw.strip():
        if currency_raw.strip().upper() != "USD":
            return False

    exchange_raw = (
        meta.get("fullExchangeName")
        or meta.get("exchangeName")
        or meta.get("exchange")
    )
    if not isinstance(exchange_raw, str) or not exchange_raw.strip():
        return False

    upper = exchange_raw.upper()
    if any(marker in upper for marker in _REJECTED_YAHOO_EXCHANGE_MARKERS):
        return False

    return _normalize_us_exchange(exchange_raw) in _US_LISTED_EXCHANGES


def _strip_us_prefix(value: str) -> str:
    normalized = value.strip()
    if normalized.upper().startswith("US:"):
        return normalized.split(":", 1)[1].strip()
    return normalized


def _normalize_us_symbol(value: str) -> str | None:
    candidate = _strip_us_prefix(value).upper()
    if not candidate or not _looks_like_symbol(candidate):
        return None
    if _looks_like_crypto_pair_symbol(candidate):
        return None
    return candidate


def _normalize_us_stock_id(stock_id: str) -> str | None:
    symbol = _normalize_us_symbol(stock_id)
    if symbol is None:
        return None
    return f"US:{symbol}"


def _normalize_us_exchange(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        return "NYSE"
    upper = raw.upper()
    if "AMEX" in upper or "ARCA" in upper or upper.startswith("ASE") or upper.startswith("PCX"):
        return "AMEX"
    if upper.startswith("BTS") or upper.startswith("BZX") or "CBOE US" in upper:
        return "CBOE"
    if "NASDAQ" in upper or upper.startswith("NMS") or upper.startswith("NGM") or upper.startswith("NCM"):
        return "NASDAQ"
    if "NYSE" in upper or upper.startswith("NYS") or upper.startswith("NYQ"):
        return "NYSE"
    return raw.strip()


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


def _stock_from_yahoo_chart_meta(stock_id: str, meta: dict[object, object]) -> StockRecord | None:
    requested_symbol = _normalize_us_symbol(stock_id)
    if requested_symbol is None:
        return None

    resolved_symbol_raw = meta.get("symbol")
    resolved_symbol = (
        _normalize_us_symbol(str(resolved_symbol_raw))
        if isinstance(resolved_symbol_raw, str)
        else requested_symbol
    )
    if resolved_symbol is None:
        resolved_symbol = requested_symbol

    name_raw = meta.get("longName") or meta.get("shortName")
    name = str(name_raw).strip() if isinstance(name_raw, str) and name_raw.strip() else resolved_symbol
    currency_raw = meta.get("currency")
    timezone_raw = meta.get("exchangeTimezoneName")
    exchange_raw = meta.get("fullExchangeName") or meta.get("exchangeName") or meta.get("exchange")
    if not _yahoo_meta_supports_us_equity_or_etf(meta):
        return None

    return StockRecord(
        id=f"US:{requested_symbol}",
        market="US",
        symbol=resolved_symbol,
        name=name,
        name_en=name,
        exchange=_normalize_us_exchange(exchange_raw),
        currency=str(currency_raw).strip().upper() if isinstance(currency_raw, str) and currency_raw.strip() else "USD",
        timezone=str(timezone_raw).strip() if isinstance(timezone_raw, str) and timezone_raw.strip() else "America/New_York",
    )


def _fetch_yahoo_chart_stock(stock_id: str) -> StockRecord | None:
    symbol = _normalize_us_symbol(stock_id)
    if symbol is None:
        return None

    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    for candidate in (symbol, f"{symbol}.US"):
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
        if not isinstance(block, dict):
            continue
        meta = block.get("meta")
        if not isinstance(meta, dict):
            continue
        stock = _stock_from_yahoo_chart_meta(stock_id, meta)
        if stock is not None:
            return stock

    return None


def _fetch_yahoo_chart_quote(stock: StockRecord) -> QuoteRecord | None:
    """Primary US quote: Yahoo chart API (works when v7 quote is blocked)."""
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
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

        quote_time = market_time(meta, stock.timezone)
        if quote_time is None:
            # Without the market time we cannot tell a live price from an old one.
            continue
        previous_close = previous_session_close(
            block,
            meta,
            session_date=quote_time.date(),
            timezone_name=stock.timezone,
        )
        if previous_close is None:
            continue
        last_value, previous_value, change, change_percent = coherent_price_change(
            float(last_price), previous_close
        )

        volume_raw = meta.get("regularMarketVolume")
        volume = int(volume_raw) if isinstance(volume_raw, (int, float)) else None
        high_raw = meta.get("regularMarketDayHigh")
        low_raw = meta.get("regularMarketDayLow")

        return QuoteRecord(
            id=stock.id,
            last_price=last_value,
            change=change,
            change_percent=change_percent,
            previous_close=previous_value,
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

    quote_time = market_time(item, stock.timezone)
    if quote_time is None:
        return None
    last_value, previous_value, change, change_percent = coherent_price_change(
        float(last_price), float(previous_close)
    )

    volume_raw = item.get("regularMarketVolume")
    volume = int(volume_raw) if isinstance(volume_raw, (int, float)) else None
    open_raw = item.get("regularMarketOpen")
    high_raw = item.get("regularMarketDayHigh")
    low_raw = item.get("regularMarketDayLow")

    return QuoteRecord(
        id=stock.id,
        last_price=last_value,
        change=change,
        change_percent=change_percent,
        previous_close=previous_value,
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
    Auth denials abort remaining chunks so callers can fall back to chart/live
    paths without burning latency on every chunk (#182).
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
            # Keep normal retries for transient 429/5xx/timeouts. Auth denials are
            # already non-retryable in http_client (#182 Codex).
            payload = _load_json_with_headers(
                _YAHOO_QUOTE_URL,
                {"symbols": symbols_param},
                headers,
            )
        except HttpFetchError as exc:
            if exc.classification == "auth_denied":
                logger.warning(
                    "Yahoo v7 quote batch auth denied; aborting remaining chunks",
                    extra={
                        "event": "yahoo_v7_quotes_batch.auth_denied",
                        "provider": "yahoo_finance",
                        "chunk_start": start,
                        "chunk_size": len(chunk),
                        "status_code": exc.status_code,
                        "response_classification": exc.classification,
                    },
                )
                break
            continue
        except (TimeoutError, ValueError):
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
    # Stooq is not used: its quote row has no previous close, so it cannot give a
    # correct daily change (it used to compare against the day's open).
    for fetcher in (_fetch_yahoo_chart_quote, _fetch_yahoo_v7_quote):
        quote = fetcher(stock)
        if quote is not None:
            return quote
    return None


def _fetch_yahoo_history(
    stock: StockRecord,
    range_name: str,
    *,
    min_candles: int | None = None,
) -> list[CandleRecord]:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    interval, range_param = _yahoo_history_window(range_name, min_candles=min_candles)
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


def _yahoo_history_window(
    range_name: str,
    *,
    min_candles: int | None = None,
) -> tuple[str, str]:
    if range_name == "1D":
        return "5m", "1d"
    if range_name == "1W":
        return "30m", "5d"

    base_window = {
        "1M": ("1d", "1mo"),
        "3M": ("1d", "3mo"),
        "6M": ("1d", "6mo"),
        "1Y": ("1d", "1y"),
    }.get(range_name, ("1d", "1mo"))

    if min_candles is None:
        return base_window

    # Escalate the Yahoo window so SMA warm-up candles are available.
    # Keep generous headroom: 3M+60≈125 trading days needs more than ~6mo.
    if min_candles <= 65:
        return "1d", "3mo"
    if min_candles <= 95:
        return "1d", "6mo"
    if min_candles <= 220:
        return "1d", "1y"
    return "1d", "2y"
