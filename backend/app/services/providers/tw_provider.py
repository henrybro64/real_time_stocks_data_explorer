from __future__ import annotations

import logging
import math
import re
import threading
from typing import NoReturn
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from time import monotonic, perf_counter
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.core.diagnostics import elapsed_ms
from app.services.http_client import HttpFetchError, fetch_json, fetch_text
from app.models.quote import CandleRecord, PriceLimitStatus, QuoteRecord
from app.models.stock import StockRecord
from app.services.market_clock import (
    is_market_open,
    is_trading_day,
    market_session_close,
    market_session_window,
)
from app.services.tw_price_limits import (
    classify_taiwan_price_limit,
    decimal_to_api_float,
    parse_price,
)

logger = logging.getLogger(__name__)

# MIS disconnect circuit: after repeated RemoteProtocol/network failures, skip MIS
# for a cooldown so concurrent ranking enrichment cannot re-storm the endpoint (#183).
_MIS_FAILURE_THRESHOLD = 3
_MIS_CIRCUIT_OPEN_SECONDS = 45.0
_MIS_SESSION_TTL_SECONDS = 300.0
_mis_circuit_lock = threading.Lock()
_mis_warmup_lock = threading.Lock()
_mis_consecutive_failures = 0
_mis_circuit_open_until = 0.0
_mis_session_cookie_header: str | None = None
# 0.0 means warm-up has never been attempted; None cookie can still be cached.
_mis_session_warmed_at = 0.0


@dataclass(frozen=True)
class _DailyHistoryCollection:
    candles_by_time: dict[str, CandleRecord]
    fetch_attempts: int
    fetch_failures: int
    soft_rejections: int


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
    "TW:2303": StockRecord(
        id="TW:2303",
        market="TW",
        symbol="2303",
        name="聯電",
        name_en="UMC",
        name_zh_tw="聯電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:2881": StockRecord(
        id="TW:2881",
        market="TW",
        symbol="2881",
        name="富邦金",
        name_en="Fubon Financial",
        name_zh_tw="富邦金",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:2882": StockRecord(
        id="TW:2882",
        market="TW",
        symbol="2882",
        name="國泰金",
        name_en="Cathay Financial",
        name_zh_tw="國泰金",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:6488": StockRecord(
        id="TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        name_en="GlobalWafers",
        name_zh_tw="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:8299": StockRecord(
        id="TW:8299",
        market="TW",
        symbol="8299",
        name="群聯",
        name_en="Phison",
        name_zh_tw="群聯",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:8271": StockRecord(
        id="TW:8271",
        market="TW",
        symbol="8271",
        name="宇瞻",
        name_en="Apacer",
        name_zh_tw="宇瞻",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:6215": StockRecord(
        id="TW:6215",
        market="TW",
        symbol="6215",
        name="和椿",
        name_en="Hotron",
        name_zh_tw="和椿",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:6643": StockRecord(
        id="TW:6643",
        market="TW",
        symbol="6643",
        name="M31",
        name_en="M31 Technology",
        name_zh_tw="M31",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:6562": StockRecord(
        id="TW:6562",
        market="TW",
        symbol="6562",
        name="聯亞",
        name_en="Allied Industrial",
        name_zh_tw="聯亞",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:6451": StockRecord(
        id="TW:6451",
        market="TW",
        symbol="6451",
        name="訊芯-KY",
        name_en="SmartEC",
        name_zh_tw="訊芯-KY",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
    "TW:6462": StockRecord(
        id="TW:6462",
        market="TW",
        symbol="6462",
        name="神盾",
        name_en="Egis Technology",
        name_zh_tw="神盾",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    ),
}

_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_HISTORY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
_TPEX_HISTORY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
# TPEx tradingStock volume column is 成交仟股 (thousand shares), not lots.
_TPEX_HISTORY_VOLUME_SCALE = 1000
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
_TWSE_NO_DATA_STAT_MARKERS = (
    "沒有符合條件",
    "查無資料",
    "無符合",
)


def _yahoo_tw_symbol_candidates(stock: StockRecord) -> tuple[str, ...]:
    listing = _yahoo_listing_symbol(stock)
    alternate = f"{stock.symbol.strip()}.TW" if listing.endswith(".TWO") else f"{stock.symbol.strip()}.TWO"
    return (listing, alternate)


def _yahoo_listing_symbol(stock: StockRecord) -> str:
    """Yahoo suffix for this stock's exchange. Never the other board."""
    symbol = stock.symbol.strip()
    if stock.exchange == "TPEx":
        return f"{symbol}.TWO"
    return f"{symbol}.TW"


def _last_daily_bar(
    block: dict,
    *,
    session_date: date,
    timezone_name: str,
) -> dict[str, float | int] | None:
    """OHLC from the Yahoo daily bar on ``session_date`` only."""
    timestamps = block.get("timestamp")
    if not isinstance(timestamps, list):
        return None
    indicators = block.get("indicators")
    if not isinstance(indicators, dict):
        return None
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        return None
    first = quotes[0]
    opens = first.get("open")
    highs = first.get("high")
    lows = first.get("low")
    closes = first.get("close")
    volumes = first.get("volume")
    if not all(isinstance(series, list) for series in (opens, highs, lows, closes)):
        return None
    tz = ZoneInfo(timezone_name)
    for index in range(len(closes) - 1, -1, -1):
        if (
            index >= len(timestamps)
            or index >= len(opens)
            or index >= len(highs)
            or index >= len(lows)
        ):
            continue
        raw_ts = timestamps[index]
        if not isinstance(raw_ts, (int, float)):
            continue
        if datetime.fromtimestamp(raw_ts, tz=tz).date() != session_date:
            continue
        open_value = opens[index]
        high_value = highs[index]
        low_value = lows[index]
        close_value = closes[index]
        if not all(
            isinstance(value, (int, float))
            for value in (open_value, high_value, low_value, close_value)
        ):
            continue
        volume_raw = volumes[index] if isinstance(volumes, list) and index < len(volumes) else None
        volume = int(volume_raw) if isinstance(volume_raw, (int, float)) else 0
        return {
            "open": float(open_value),
            "high": float(high_value),
            "low": float(low_value),
            "close": float(close_value),
            "volume": volume,
        }
    return None


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
    _catalog_lock = threading.Lock()
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
        return None

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

        if range_name == "1D":
            return self._fetch_yahoo_intraday_candles(stock)

        daily_limit = limit if limit is not None else _daily_limit(range_name)
        candles = self._fetch_daily_history(
            stock,
            limit=daily_limit,
            range_name=range_name,
        )
        if not candles:
            return []
        return self._merge_live_session_daily_candle(stock, candles)

    def _fetch_quote(self, stock: StockRecord) -> QuoteRecord | None:
        if _mis_circuit_is_open():
            return None
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
        except HttpFetchError as exc:
            if exc.classification == "network_failure":
                _note_mis_failure(classification=exc.classification)
            return None
        except (TimeoutError, ValueError):
            _note_mis_failure(classification="network_failure")
            return None

        items = payload.get("msgArray") if isinstance(payload, dict) else None
        if not items:
            # Reachable but empty is not a disconnect storm signal.
            _note_mis_success()
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
            _note_mis_success()
            return None

        quote_timestamp = _parse_quote_timestamp(
            raw_date=item.get("d"),
            raw_time=item.get("t") or item.get("%") or item.get("ot"),
            timezone_name=stock.timezone,
        )
        change = round(last_price - previous_close, 2)
        change_percent = round((change / previous_close) * 100, 2)
        price_limit_status, reference_price, limit_up_price, limit_down_price = (
            _mis_price_limit_fields(item, stock.symbol)
        )
        _note_mis_success()

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
            price_limit_status=price_limit_status,
            reference_price=reference_price,
            limit_up_price=limit_up_price,
            limit_down_price=limit_down_price,
        )

    def _fetch_yahoo_listing_quote(self, stock: StockRecord) -> QuoteRecord | None:
        """Yahoo quote for this exchange only. Used to build today's candle."""
        return self._fetch_yahoo_chart_quote(stock, (_yahoo_listing_symbol(stock),))

    def _fetch_yahoo_tw_quote(self, stock: StockRecord) -> QuoteRecord | None:
        """Yahoo chart meta when TWSE mis API is flaky or blocked."""
        return self._fetch_yahoo_chart_quote(stock, _yahoo_tw_symbol_candidates(stock))

    def _fetch_yahoo_chart_quote(
        self,
        stock: StockRecord,
        symbols: tuple[str, ...],
    ) -> QuoteRecord | None:
        headers = {
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        }
        tz = ZoneInfo(stock.timezone)
        for candidate in symbols:
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
            session_bar = (
                _last_daily_bar(
                    block,
                    session_date=quote_time.date(),
                    timezone_name=stock.timezone,
                )
                if isinstance(block, dict)
                else None
            )
            high_raw = meta.get("regularMarketDayHigh")
            if not isinstance(high_raw, (int, float)) and session_bar is not None:
                high_raw = session_bar["high"]
            low_raw = meta.get("regularMarketDayLow")
            if not isinstance(low_raw, (int, float)) and session_bar is not None:
                low_raw = session_bar["low"]
            open_raw = meta.get("regularMarketOpen")
            if not isinstance(open_raw, (int, float)) and session_bar is not None:
                open_raw = session_bar["open"]
            if volume is None and session_bar is not None:
                volume = session_bar["volume"]

            return QuoteRecord(
                id=stock.id,
                last_price=round(float(last_price), 2),
                change=change,
                change_percent=change_percent,
                previous_close=round(float(previous_close), 2),
                open=round(float(open_raw), 2) if isinstance(open_raw, (int, float)) else None,
                high=round(float(high_raw), 2)
                if isinstance(high_raw, (int, float))
                else None,
                low=round(float(low_raw), 2)
                if isinstance(low_raw, (int, float))
                else None,
                volume=volume,
                timestamp=quote_time.isoformat(),
                is_market_open=is_market_open(stock.market, quote_time),
                # Yahoo chart meta has no official MIS u/w band. Do not copy a
                # previous limit classification or infer one from change percent.
                price_limit_status="unknown",
                reference_price=None,
                limit_up_price=None,
                limit_down_price=None,
            )

        return None

    def _fetch_yahoo_intraday_candles(self, stock: StockRecord) -> list[CandleRecord]:
        """Yahoo 5-minute bars for this listing only.

        A failed or unusable chart raises. A chart that answered with no complete
        OHLC bars returns an empty list instead of another board's prices.
        """
        listing = _yahoo_listing_symbol(stock)
        path_symbol = quote(listing, safe="-.")
        url = f"{_YAHOO_CHART_URL}/{path_symbol}?interval=5m&range=1d"
        try:
            payload = fetch_json(
                url,
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
                retries=2,
            )
        except (HttpFetchError, TimeoutError, ValueError):
            _raise_intraday_history_unavailable(stock, reason="fetch_failed")
        if _yahoo_chart_block(payload) is None:
            _raise_intraday_history_unavailable(stock, reason="chart_unavailable")
        return _candles_from_yahoo_chart(payload, stock)

    def _merge_live_session_daily_candle(
        self,
        stock: StockRecord,
        candles: list[CandleRecord],
    ) -> list[CandleRecord]:
        """Append or refresh today's session bar from a live quote. Never invent one."""
        now = datetime.now(ZoneInfo(stock.timezone))
        if not is_trading_day(stock.market, now):
            return candles
        session_start, _session_end = market_session_window(stock.market, now.date())
        if now.time().replace(second=0, microsecond=0) < session_start:
            return candles

        quote = self._fetch_quote(stock)
        if quote is None:
            quote = self._fetch_yahoo_listing_quote(stock)
        session = _session_daily_candle(stock, quote, now)
        if session is None:
            return candles
        return _upsert_candle_by_local_date(
            candles,
            session,
            replace_existing=is_market_open(stock.market, now),
        )

    def _fetch_daily_history(
        self,
        stock: StockRecord,
        limit: int,
        *,
        range_name: str,
    ) -> list[CandleRecord]:
        source = _daily_history_source(stock.exchange)
        if stock.exchange not in {"TWSE", "TPEx"}:
            logger.warning(
                "Unsupported Taiwan exchange for daily history",
                extra={
                    "event": "tw.history.unsupported_exchange",
                    "stock_id": stock.id,
                    "exchange": stock.exchange,
                    "range_name": range_name,
                    "source": source,
                },
            )
            return []

        started_at = perf_counter()
        today = datetime.now(ZoneInfo(stock.timezone)).date()
        months_to_fetch = max(2, math.ceil(limit / 20) + 1)
        volume_scale = _TPEX_HISTORY_VOLUME_SCALE if stock.exchange == "TPEx" else 1
        collected = self._collect_daily_history_months(
            stock,
            today=today,
            months_to_fetch=months_to_fetch,
            limit=limit,
            range_name=range_name,
            source=source,
            volume_scale=volume_scale,
        )
        trimmed = sorted(collected.candles_by_time.values(), key=lambda item: item.time)[-limit:]
        if collected.fetch_failures > 0:
            _raise_history_unavailable(
                stock,
                range_name=range_name,
                source=source,
                fetch_attempts=collected.fetch_attempts,
                fetch_failures=collected.fetch_failures,
                soft_rejections=collected.soft_rejections,
                candle_count=len(trimmed),
                duration_ms=elapsed_ms(started_at),
            )
        return _finish_daily_history(
            stock,
            trimmed=trimmed,
            range_name=range_name,
            source=source,
            fetch_attempts=collected.fetch_attempts,
            fetch_failures=collected.fetch_failures,
            soft_rejections=collected.soft_rejections,
            duration_ms=elapsed_ms(started_at),
        )

    def _collect_daily_history_months(
        self,
        stock: StockRecord,
        *,
        today: date,
        months_to_fetch: int,
        limit: int,
        range_name: str,
        source: str,
        volume_scale: int,
    ) -> _DailyHistoryCollection:
        candles_by_time: dict[str, CandleRecord] = {}
        fetch_attempts = 0
        fetch_failures = 0
        soft_rejections = 0
        for month_offset in range(months_to_fetch):
            # Newest months first; stop once the requested candle budget is met so
            # a transient failure in an unused older surplus month cannot 502.
            if len(candles_by_time) >= limit:
                break
            month_date = _month_anchor(today, month_offset)
            fetch_attempts += 1
            month_started_at = perf_counter()
            try:
                rows = self._fetch_month_rows(stock, month_date)
            except (HttpFetchError, TimeoutError, ValueError) as exc:
                fetch_failures += 1
                _log_history_month_failed(
                    stock,
                    range_name=range_name,
                    source=source,
                    month_date=month_date,
                    exc=exc,
                    duration_ms=elapsed_ms(month_started_at),
                )
                continue
            if not rows:
                soft_rejections += 1
                _log_history_month_empty(
                    stock,
                    range_name=range_name,
                    source=source,
                    month_date=month_date,
                    duration_ms=elapsed_ms(month_started_at),
                )
            _ingest_daily_rows(candles_by_time, rows, stock.timezone, volume_scale)
        return _DailyHistoryCollection(
            candles_by_time=candles_by_time,
            fetch_attempts=fetch_attempts,
            fetch_failures=fetch_failures,
            soft_rejections=soft_rejections,
        )

    def _fetch_month_rows(self, stock: StockRecord, month_date: date) -> list[list[object]]:
        if stock.exchange == "TWSE":
            return self._fetch_twse_month_rows(stock, month_date)
        return self._fetch_tpex_month_rows(stock, month_date)

    def _fetch_twse_month_rows(self, stock: StockRecord, month_date: date) -> list[list[object]]:
        payload = _load_json(
            _HISTORY_URL,
            {
                "response": "json",
                "date": month_date.strftime("%Y%m01"),
                "stockNo": stock.symbol,
            },
        )
        if not isinstance(payload, dict):
            raise ValueError("TWSE history response is not a JSON object")
        stat = payload.get("stat")
        if stat != "OK":
            if _is_twse_no_data_stat(stat):
                logger.info(
                    "TWSE daily history reported no matching data for month",
                    extra={
                        "event": "tw.history.twse_no_data",
                        "stock_id": stock.id,
                        "exchange": stock.exchange,
                        "month": month_date.isoformat(),
                        "stat": str(stat)[:200] if stat is not None else None,
                    },
                )
                return []
            raise ValueError(f"TWSE history rejected with stat={stat!r}")
        rows = payload.get("data")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise ValueError("TWSE history data field is not a list")
        return [row for row in rows if isinstance(row, list)]

    def _fetch_tpex_month_rows(self, stock: StockRecord, month_date: date) -> list[list[object]]:
        from app.services.taiwan_market_data import fetch_tpex_data

        payload = fetch_tpex_data(
            _TPEX_HISTORY_URL,
            params={
                "code": stock.symbol,
                "date": month_date.strftime("%Y/%m/01"),
                "response": "json",
            },
            timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
            retries=2,
            operation_name="tw_provider_tpex_daily_history",
            resource_id=f"{stock.symbol}:{month_date.isoformat()}",
        )
        return _extract_tpex_daily_rows(payload)

    def _cached_catalog_if_fresh(self, now: datetime) -> dict[str, StockRecord] | None:
        cached = self.__class__._catalog_cache
        cached_at = self.__class__._catalog_cached_at
        if cached is not None and cached_at is not None and now - cached_at < _CATALOG_TTL:
            return cached
        return None

    def _stock_catalog(self) -> dict[str, StockRecord]:
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        cached = self._cached_catalog_if_fresh(now)
        if cached is not None:
            return cached

        with self.__class__._catalog_lock:
            now = datetime.now(ZoneInfo("Asia/Taipei"))
            cached = self._cached_catalog_if_fresh(now)
            if cached is not None:
                return cached
            return self._load_stock_catalog(now)

    def _load_stock_catalog(self, now: datetime) -> dict[str, StockRecord]:
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


def _mis_circuit_is_open() -> bool:
    with _mis_circuit_lock:
        return monotonic() < _mis_circuit_open_until


def _note_mis_success() -> None:
    global _mis_consecutive_failures
    with _mis_circuit_lock:
        _mis_consecutive_failures = 0


def _note_mis_failure(*, classification: str) -> None:
    global _mis_consecutive_failures, _mis_circuit_open_until
    with _mis_circuit_lock:
        _mis_consecutive_failures += 1
        if _mis_consecutive_failures < _MIS_FAILURE_THRESHOLD:
            return
        _mis_circuit_open_until = monotonic() + _MIS_CIRCUIT_OPEN_SECONDS
        _mis_consecutive_failures = 0
        logger.warning(
            "TWSE MIS circuit opened after repeated disconnects",
            extra={
                "event": "twse_mis.circuit.opened",
                "provider": "twse",
                "classification": classification,
                "open_seconds": _MIS_CIRCUIT_OPEN_SECONDS,
            },
        )


def mis_circuit_is_open() -> bool:
    return _mis_circuit_is_open()


def note_mis_transport_success() -> None:
    _note_mis_success()


def note_mis_transport_failure(classification: str) -> None:
    _note_mis_failure(classification=classification)


def reset_mis_circuit_for_tests() -> None:
    """Test helper: clear MIS circuit and warmed session state."""
    global _mis_consecutive_failures, _mis_circuit_open_until
    global _mis_session_cookie_header, _mis_session_warmed_at
    with _mis_circuit_lock:
        _mis_consecutive_failures = 0
        _mis_circuit_open_until = 0.0
        _mis_session_cookie_header = None
        _mis_session_warmed_at = 0.0


def _mis_cookie_header() -> str | None:
    """Best-effort session warm-up against the MIS index page (no secrets).

    Caches successful cookies and failed/cookie-less outcomes for the TTL so
    concurrent quote enrichment cannot stampede the index page (#183 Codex).
    """
    global _mis_session_cookie_header, _mis_session_warmed_at
    with _mis_warmup_lock:
        with _mis_circuit_lock:
            if (
                _mis_session_warmed_at > 0.0
                and monotonic() - _mis_session_warmed_at < _MIS_SESSION_TTL_SECONDS
            ):
                return _mis_session_cookie_header

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        cookie_header: str | None = None
        try:
            response = httpx.get(
                "https://mis.twse.com.tw/stock/index.jsp",
                headers=headers,
                timeout=5.0,
                follow_redirects=True,
            )
            if response.cookies:
                cookie_header = "; ".join(
                    f"{key}={value}" for key, value in response.cookies.items()
                )
        except Exception:  # noqa: BLE001 - warm-up is best-effort only
            cookie_header = None

        with _mis_circuit_lock:
            _mis_session_cookie_header = cookie_header
            _mis_session_warmed_at = monotonic()
        return cookie_header


def _load_json(url: str, params: dict[str, str], referer: str | None = None) -> object:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    is_mis = "mis.twse.com.tw" in url
    if is_mis:
        headers["Origin"] = "https://mis.twse.com.tw"
        cookie_header = _mis_cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header

    return fetch_json(
        url,
        params=params,
        headers=headers,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        # Bound only MIS quote disconnect retries; keep catalog/history resilient.
        retries=1 if is_mis else 2,
        allow_insecure_fallback_hosts={"mis.twse.com.tw", "www.twse.com.tw"},
        provider_name="twse" if is_mis else None,
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


def _mis_price_limit_fields(
    item: dict,
    symbol: str,
) -> tuple[PriceLimitStatus, float | None, float | None, float | None]:
    """Classify the MIS trade price ``z`` against official ``u`` / ``w``.

    ``_fetch_quote`` may display ``pz`` or reference ``y`` when ``z`` is missing.
    That fallback is not a trade, so it must stay ``unknown`` even if the
    displayed number equals a limit price or the change is about ±10%.

    Both official limits must be present. The rankings ±10% calculator is not
    used here: a quote with no ``u``/``w`` band is incomplete, not a limit.
    """
    trade_text = item.get("z") if isinstance(item.get("z"), str) else None
    traded_price = parse_price(trade_text) if _parse_float(trade_text) is not None else None
    status, reference, limit_up, limit_down = classify_taiwan_price_limit(
        last_price=traded_price,
        reference_price=parse_price(item.get("y")),
        limit_up_price_value=parse_price(item.get("u")),
        limit_down_price_value=parse_price(item.get("w")),
        traded=traded_price is not None,
        symbol=symbol,
        allow_calculated=False,
    )
    return (
        status,
        decimal_to_api_float(reference),
        decimal_to_api_float(limit_up),
        decimal_to_api_float(limit_down),
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


def _is_twse_no_data_stat(stat: object) -> bool:
    if not isinstance(stat, str):
        return False
    return any(marker in stat for marker in _TWSE_NO_DATA_STAT_MARKERS)


def _daily_history_source(exchange: str) -> str:
    return "twse_stock_day" if exchange == "TWSE" else "tpex_trading_stock"


def _ingest_daily_rows(
    candles_by_time: dict[str, CandleRecord],
    rows: list[list[object]],
    timezone_name: str,
    volume_scale: int,
) -> None:
    for row in rows:
        if not isinstance(row, list):
            continue
        candle = _parse_daily_row(
            [str(cell) for cell in row],
            timezone_name,
            volume_scale=volume_scale,
        )
        if candle is not None:
            candles_by_time[candle.time] = candle


def _log_history_month_failed(
    stock: StockRecord,
    *,
    range_name: str,
    source: str,
    month_date: date,
    exc: Exception,
    duration_ms: float,
) -> None:
    logger.warning(
        "Taiwan daily history month fetch failed",
        extra={
            "event": "tw.history.month_failed",
            "stock_id": stock.id,
            "exchange": stock.exchange,
            "range_name": range_name,
            "source": source,
            "month": month_date.isoformat(),
            "error_type": type(exc).__name__,
            "error_detail": str(exc)[:300],
            "duration_ms": duration_ms,
        },
    )


def _log_history_month_empty(
    stock: StockRecord,
    *,
    range_name: str,
    source: str,
    month_date: date,
    duration_ms: float,
) -> None:
    logger.info(
        "Taiwan daily history month returned no rows",
        extra={
            "event": "tw.history.month_empty",
            "stock_id": stock.id,
            "exchange": stock.exchange,
            "range_name": range_name,
            "source": source,
            "month": month_date.isoformat(),
            "duration_ms": duration_ms,
        },
    )


def _raise_intraday_history_unavailable(stock: StockRecord, *, reason: str) -> NoReturn:
    logger.warning(
        "Taiwan intraday history temporarily unavailable",
        extra={
            "event": "tw.history.intraday_unavailable",
            "stock_id": stock.id,
            "exchange": stock.exchange,
            "reason": reason,
        },
    )
    raise RuntimeError(
        f"Taiwan intraday history temporarily unavailable for {stock.id} ({stock.exchange})"
    )


def _raise_history_unavailable(
    stock: StockRecord,
    *,
    range_name: str,
    source: str,
    fetch_attempts: int,
    fetch_failures: int,
    soft_rejections: int,
    candle_count: int,
    duration_ms: float,
) -> None:
    logger.warning(
        "Taiwan daily history temporarily unavailable",
        extra={
            "event": "tw.history.unavailable",
            "stock_id": stock.id,
            "exchange": stock.exchange,
            "range_name": range_name,
            "source": source,
            "fetch_attempts": fetch_attempts,
            "fetch_failures": fetch_failures,
            "soft_rejections": soft_rejections,
            "candle_count": candle_count,
            "duration_ms": duration_ms,
        },
    )
    raise RuntimeError(
        f"Taiwan daily history temporarily unavailable for {stock.id} ({stock.exchange})"
    )


def _finish_daily_history(
    stock: StockRecord,
    *,
    trimmed: list[CandleRecord],
    range_name: str,
    source: str,
    fetch_attempts: int,
    fetch_failures: int,
    soft_rejections: int,
    duration_ms: float,
) -> list[CandleRecord]:
    if trimmed:
        logger.info(
            "Taiwan daily history fetch completed",
            extra={
                "event": "tw.history.completed",
                "stock_id": stock.id,
                "exchange": stock.exchange,
                "range_name": range_name,
                "source": source,
                "candle_count": len(trimmed),
                "duration_ms": duration_ms,
            },
        )
        return trimmed

    logger.info(
        "Taiwan daily history returned no candles",
        extra={
            "event": "tw.history.empty",
            "stock_id": stock.id,
            "exchange": stock.exchange,
            "range_name": range_name,
            "source": source,
            "fetch_attempts": fetch_attempts,
            "fetch_failures": fetch_failures,
            "soft_rejections": soft_rejections,
            "duration_ms": duration_ms,
        },
    )
    return []


def _list_of_list_rows(rows: object, *, field_name: str) -> list[list[object]]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError(f"TPEx history {field_name} field is not a list")
    return [row for row in rows if isinstance(row, list)]


def _extract_tpex_tables_rows(tables: list[object]) -> list[list[object]]:
    if not tables:
        return []
    for table in tables:
        if not isinstance(table, dict) or "data" not in table:
            continue
        return _list_of_list_rows(table.get("data"), field_name="tables[].data")
    raise ValueError("TPEx history tables present but missing usable data")


def _extract_tpex_legacy_rows(payload: dict) -> list[list[object]] | None:
    for key in ("aaData", "data"):
        if key not in payload:
            continue
        return _list_of_list_rows(payload.get(key), field_name=key)
    return None


def _extract_tpex_daily_rows(payload: object) -> list[list[object]]:
    """Normalize modern tables[].data and legacy aaData/data TPEx shapes.

    Recognized empty shapes return ``[]``. Unrecognized / malformed payloads raise
    ``ValueError`` so callers can treat them as upstream failures instead of
    silent no-data.
    """
    if not isinstance(payload, dict):
        raise ValueError("TPEx history response is not a JSON object")

    tables = payload.get("tables")
    if isinstance(tables, list):
        return _extract_tpex_tables_rows(tables)

    legacy_rows = _extract_tpex_legacy_rows(payload)
    if legacy_rows is not None:
        return legacy_rows

    raise ValueError("TPEx history payload missing tables/aaData/data")


def _parse_daily_row(
    row: list[str],
    timezone_name: str,
    *,
    volume_scale: int = 1,
) -> CandleRecord | None:
    if len(row) < 8:
        return None

    trade_date = _parse_minguo_date(row[0], timezone_name)
    open_price = _parse_float(row[3])
    high_price = _parse_float(row[4])
    low_price = _parse_float(row[5])
    close_price = _parse_float(row[6])
    volume_raw = _parse_int(row[1])

    if trade_date is None or None in {open_price, high_price, low_price, close_price, volume_raw}:
        return None

    volume = int(volume_raw) * max(volume_scale, 1)
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


def _yahoo_chart_block(payload: object) -> dict | None:
    """Chart result block when Yahoo answered. ``None`` means the payload is unusable."""
    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error"):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return None
    return results[0]


def _candles_from_yahoo_chart(payload: object, stock: StockRecord) -> list[CandleRecord]:
    block = _yahoo_chart_block(payload)
    if block is None:
        return []
    timestamps = block.get("timestamp")
    if not isinstance(timestamps, list) or not timestamps:
        return []
    indicators = block.get("indicators")
    if not isinstance(indicators, dict):
        return []
    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        return []
    first = quotes[0]
    opens = first.get("open")
    highs = first.get("high")
    lows = first.get("low")
    closes = first.get("close")
    volumes = first.get("volume")
    if not all(isinstance(series, list) for series in (opens, highs, lows, closes)):
        return []
    if not isinstance(volumes, list):
        volumes = [None] * len(timestamps)

    meta = block.get("meta")
    tz_name = (
        meta.get("exchangeTimezoneName")
        if isinstance(meta, dict) and isinstance(meta.get("exchangeTimezoneName"), str)
        else stock.timezone
    )
    tz = ZoneInfo(tz_name)
    candles: list[CandleRecord] = []
    for index, raw_ts in enumerate(timestamps):
        if not isinstance(raw_ts, (int, float)):
            continue
        if index >= len(opens) or index >= len(highs) or index >= len(lows) or index >= len(closes):
            continue
        open_value = opens[index]
        high_value = highs[index]
        low_value = lows[index]
        close_value = closes[index]
        if not all(isinstance(value, (int, float)) for value in (open_value, high_value, low_value, close_value)):
            continue
        volume_raw = volumes[index] if index < len(volumes) else None
        volume = int(volume_raw) if isinstance(volume_raw, (int, float)) else 0
        candles.append(
            CandleRecord(
                time=datetime.fromtimestamp(raw_ts, tz=tz).isoformat(),
                open=round(float(open_value), 2),
                high=round(float(high_value), 2),
                low=round(float(low_value), 2),
                close=round(float(close_value), 2),
                volume=volume,
            )
        )
    candles.sort(key=lambda item: item.time)
    return candles


def _session_daily_candle(
    stock: StockRecord,
    quote: QuoteRecord | None,
    now: datetime,
) -> CandleRecord | None:
    if quote is None:
        return None
    open_price = quote.open
    high_price = quote.high
    low_price = quote.low
    volume = quote.volume
    if open_price is None or high_price is None or low_price is None or volume is None:
        return None
    tz = ZoneInfo(stock.timezone)
    try:
        quote_time = datetime.fromisoformat(quote.timestamp)
    except ValueError:
        return None
    if quote_time.tzinfo is None:
        quote_time = quote_time.replace(tzinfo=tz)
    if quote_time.astimezone(tz).date() != now.astimezone(tz).date():
        return None
    session_day = now.astimezone(tz).date()
    session_close = datetime.combine(
        session_day,
        market_session_close(stock.market, session_day),
        tzinfo=tz,
    )
    return CandleRecord(
        time=session_close.isoformat(),
        open=round(float(open_price), 2),
        high=round(float(high_price), 2),
        low=round(float(low_price), 2),
        close=round(float(quote.last_price), 2),
        volume=int(volume),
    )


def _candle_local_date(candle: CandleRecord, timezone_name: str) -> date:
    tz = ZoneInfo(timezone_name)
    candle_time = datetime.fromisoformat(candle.time)
    if candle_time.tzinfo is None:
        candle_time = candle_time.replace(tzinfo=tz)
    return candle_time.astimezone(tz).date()


def _upsert_candle_by_local_date(
    candles: list[CandleRecord],
    session: CandleRecord,
    *,
    replace_existing: bool,
) -> list[CandleRecord]:
    session_date = _candle_local_date(session, "Asia/Taipei")
    kept: list[CandleRecord] = []
    found = False
    for candle in candles:
        if _candle_local_date(candle, "Asia/Taipei") == session_date:
            found = True
            if not replace_existing:
                kept.append(candle)
            continue
        kept.append(candle)
    if replace_existing or not found:
        kept.append(session)
    kept.sort(key=lambda item: item.time)
    return kept
