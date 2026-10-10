"""Persisted daily bars + rate-limited backfill for Taiwan/US history (#269)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import math
import threading
import time
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.repositories.daily_price_bar_repository import (
    bars_to_candles,
    daily_price_bar_repository,
)
from app.services.market_clock import is_market_open, is_trading_day, market_session_window
from app.services.quote_redis_cache import get_cached_quote

logger = logging.getLogger(__name__)

_DAILY_RANGES = frozenset({"1W", "1M", "3M", "6M", "1Y", "MAX"})
_RANGE_LIMITS = {
    "1W": 10,
    "1M": 90,
    "3M": 125,
    "6M": 190,
    "1Y": 320,
    "MAX": 800,
}
# Per-host spacing so cold backfills cannot stampede TWSE/TPEx/Yahoo.
_HOST_MIN_INTERVAL_SECONDS = {
    "twse": 1.25,
    "tpex": 1.25,
    "yahoo": 0.75,
}
_host_lock = threading.Lock()
_next_host_slot: dict[str, float] = dict.fromkeys(_HOST_MIN_INTERVAL_SECONDS, 0.0)
_MONTH_FAILURE_COOLDOWN_SECONDS = 15 * 60


class DailyPriceHistoryService:
    def __init__(self, repository=daily_price_bar_repository) -> None:
        self._repo = repository
        self._month_marks: set[tuple[str, str, str]] = set()
        self._month_retry_after: dict[tuple[str, str, str], float] = {}

    def supports_range(self, range_name: str) -> bool:
        return range_name in _DAILY_RANGES

    def get_daily_history(
        self,
        stock: StockRecord,
        range_name: str,
        *,
        limit: int | None = None,
        provider,
    ) -> list[CandleRecord]:
        """Return daily candles from Postgres, backfilling months never stored."""
        candle_limit = limit if limit is not None else _RANGE_LIMITS.get(range_name, 90)
        stored, readable = self._load_stored(stock, max(candle_limit + 5, 40))
        if not readable:
            stored = self._fetch_legacy(stock, range_name, candle_limit, provider)
        else:
            missing = self._missing_months(stock, stored, candle_limit)
            if missing:
                fetched, failed = self._fetch_missing_months(
                    stock,
                    missing,
                    range_name=range_name,
                    limit=candle_limit,
                    provider=provider,
                )
                if failed and not stored and not fetched:
                    raise RuntimeError("Daily history temporarily unavailable")
                if fetched:
                    if self._persist(stock, fetched):
                        reloaded, still_readable = self._load_stored(
                            stock, max(candle_limit + 5, 40)
                        )
                        stored = reloaded if still_readable and reloaded else _overlay(stored, fetched)
                    else:
                        stored = _overlay(stored, fetched)

        merged = self._merge_forming_candle_from_cache(stock, stored)
        if candle_limit > 0 and len(merged) > candle_limit:
            return merged[-candle_limit:]
        return merged

    def refresh_closed_current_month(self, stock: StockRecord, provider) -> bool:
        """After the close, fill the last completed session's month for one symbol."""
        now = datetime.now(ZoneInfo(stock.timezone))
        if is_market_open(stock.market, now):
            return False
        last_session = _last_completed_session_date(stock, now)
        if last_session is None:
            return False
        stored, readable = self._load_stored(stock, 40)
        if not readable:
            return False
        latest = _latest_bar_date(stored)
        if latest is not None and latest >= last_session:
            return False
        month = date(last_session.year, last_session.month, 1)
        if self._month_is_cooling(stock, month):
            return False
        _wait_for_host_slot(_host_for_stock(stock))
        try:
            fetched = self._fetch_one_month(stock, month, provider, range_name="1M", limit=40)
        except Exception as exc:  # noqa: BLE001 - keep the sampler moving
            logger.debug(
                "Closed-market month refresh failed",
                extra={
                    "event": "daily_price_history.month_refresh.failed",
                    "stock_id": stock.id,
                    "month": month.isoformat(),
                    "error_type": type(exc).__name__,
                },
            )
            self._cool_month(stock, month)
            return False
        if fetched:
            self._persist(stock, fetched)
        self._mark_month(stock, month, len(fetched))
        return True

    def _load_stored(
        self, stock: StockRecord, limit: int
    ) -> tuple[list[CandleRecord], bool]:
        if not self._repo.is_available():
            return [], False
        try:
            rows = self._repo.list_bars(
                market=stock.market,
                symbol=stock.symbol,
                limit=limit,
            )
            return bars_to_candles(rows), True
        except Exception as exc:  # noqa: BLE001 - unit tests / broken DATABASE_URL
            logger.debug(
                "Daily price bar read failed; using provider path",
                extra={
                    "event": "daily_price_history.store.read_failed",
                    "stock_id": stock.id,
                    "error_type": type(exc).__name__,
                },
            )
            return [], False

    def _persist(self, stock: StockRecord, candles: list[CandleRecord]) -> bool:
        if not self._repo.is_available():
            return False
        source = (
            "twse"
            if stock.exchange == "TWSE"
            else ("tpex" if stock.exchange == "TPEx" else "yahoo")
        )
        try:
            self._repo.upsert_candles(
                market=stock.market,
                symbol=stock.symbol,
                candles=candles,
                source=source,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Daily price bar write failed; continuing without store",
                extra={
                    "event": "daily_price_history.store.write_failed",
                    "stock_id": stock.id,
                    "error_type": type(exc).__name__,
                },
            )
            return False

    def _missing_months(
        self, stock: StockRecord, stored: list[CandleRecord], limit: int
    ) -> list[date]:
        today = datetime.now(ZoneInfo(stock.timezone)).date()
        covered = {candle.time[:7] for candle in stored if len(candle.time) >= 7}
        marked = self._known_month_keys(stock)
        missing: list[date] = []
        for offset in range(months_needed_for_limit(limit)):
            month = _month_start(today, offset)
            token = month.strftime("%Y-%m")
            if token in covered or token in marked:
                continue
            if self._month_is_cooling(stock, month):
                continue
            missing.append(month)
        return missing

    def _fetch_legacy(
        self,
        stock: StockRecord,
        range_name: str,
        limit: int,
        provider,
    ) -> list[CandleRecord]:
        _wait_for_host_slot(_host_for_stock(stock))
        return self._call_uncached(stock, range_name, limit, provider)

    def _fetch_missing_months(
        self,
        stock: StockRecord,
        missing: list[date],
        *,
        range_name: str,
        limit: int,
        provider,
    ) -> tuple[list[CandleRecord], bool]:
        fetch_month = getattr(provider, "fetch_daily_month", None)
        if callable(fetch_month):
            collected: list[CandleRecord] = []
            failed = False
            for month in missing:
                _wait_for_host_slot(_host_for_stock(stock))
                try:
                    bars = fetch_month(stock, month) or []
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Daily month fetch failed",
                        extra={
                            "event": "daily_price_history.month.failed",
                            "stock_id": stock.id,
                            "month": month.isoformat(),
                            "error_type": type(exc).__name__,
                        },
                    )
                    self._cool_month(stock, month)
                    failed = True
                    continue
                collected.extend(bars)
                self._mark_month(stock, month, len(bars))
            return collected, failed

        _wait_for_host_slot(_host_for_stock(stock))
        try:
            bars = self._call_uncached(stock, range_name, limit, provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Daily history backfill failed",
                extra={
                    "event": "daily_price_history.backfill.failed",
                    "stock_id": stock.id,
                    "range_name": range_name,
                    "error_type": type(exc).__name__,
                },
            )
            for month in missing:
                self._cool_month(stock, month)
            return [], True
        for month in missing:
            self._mark_month(stock, month, len(bars))
        return bars, False

    def _fetch_one_month(
        self,
        stock: StockRecord,
        month: date,
        provider,
        *,
        range_name: str,
        limit: int,
    ) -> list[CandleRecord]:
        fetch_month = getattr(provider, "fetch_daily_month", None)
        if callable(fetch_month):
            return list(fetch_month(stock, month) or [])
        return self._call_uncached(stock, range_name, limit, provider)

    def _call_uncached(
        self,
        stock: StockRecord,
        range_name: str,
        limit: int,
        provider,
    ) -> list[CandleRecord]:
        fetch_daily = getattr(provider, "fetch_daily_history_uncached", None)
        if callable(fetch_daily):
            return list(fetch_daily(stock, limit=limit, range_name=range_name) or [])
        get_history = getattr(provider, "get_history", None)
        if not callable(get_history):
            return []
        return list(get_history(stock.id, range_name, limit=limit) or [])

    def _known_month_keys(self, stock: StockRecord) -> set[str]:
        keys = {
            month
            for market, symbol, month in self._month_marks
            if market == stock.market and symbol == stock.symbol
        }
        list_fetched = getattr(self._repo, "list_fetched_months", None)
        if not callable(list_fetched):
            return keys
        try:
            keys |= set(list_fetched(market=stock.market, symbol=stock.symbol))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Fetched-month read failed",
                extra={
                    "event": "daily_price_history.month_mark.read_failed",
                    "stock_id": stock.id,
                    "error_type": type(exc).__name__,
                },
            )
        return keys

    def _mark_month(self, stock: StockRecord, month: date, bar_count: int) -> None:
        token = month.strftime("%Y-%m")
        self._month_marks.add((stock.market, stock.symbol, token))
        mark = getattr(self._repo, "mark_month_fetched", None)
        if not callable(mark):
            return
        try:
            mark(
                market=stock.market,
                symbol=stock.symbol,
                month_start=date(month.year, month.month, 1),
                bar_count=bar_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Fetched-month write failed",
                extra={
                    "event": "daily_price_history.month_mark.write_failed",
                    "stock_id": stock.id,
                    "error_type": type(exc).__name__,
                },
            )

    def _month_key(self, stock: StockRecord, month: date) -> tuple[str, str, str]:
        return (stock.market, stock.symbol, month.strftime("%Y-%m"))

    def _month_is_cooling(self, stock: StockRecord, month: date) -> bool:
        until = self._month_retry_after.get(self._month_key(stock, month), 0.0)
        return time.monotonic() < until

    def _cool_month(self, stock: StockRecord, month: date) -> None:
        self._month_retry_after[self._month_key(stock, month)] = (
            time.monotonic() + _MONTH_FAILURE_COOLDOWN_SECONDS
        )

    def _merge_forming_candle_from_cache(
        self,
        stock: StockRecord,
        candles: list[CandleRecord],
    ) -> list[CandleRecord]:
        """Build today's bar from the quote cache — never a fresh MIS call (#269)."""
        now = datetime.now(ZoneInfo(stock.timezone))
        if not is_trading_day(stock.market, now):
            return candles
        session_start, _session_end = market_session_window(stock.market, now.date())
        if now.time().replace(second=0, microsecond=0) < session_start:
            return candles
        quote = get_cached_quote(stock.id)
        session = _session_candle_from_quote(stock, quote, now)
        if session is None:
            return candles
        return _upsert_by_date(
            candles,
            session,
            replace_existing=is_market_open(stock.market, now),
        )


def _host_for_stock(stock: StockRecord) -> str:
    if stock.market == "TW":
        return "tpex" if stock.exchange == "TPEx" else "twse"
    return "yahoo"


def _wait_for_host_slot(host: str) -> None:
    interval = _HOST_MIN_INTERVAL_SECONDS.get(host, 1.0)
    with _host_lock:
        now = time.monotonic()
        slot = max(now, _next_host_slot.get(host, 0.0))
        _next_host_slot[host] = slot + interval
        delay = max(0.0, slot - now)
    if delay > 0:
        time.sleep(delay)


def _session_candle_from_quote(
    stock: StockRecord,
    quote: QuoteRecord | None,
    now: datetime,
) -> CandleRecord | None:
    if quote is None:
        return None
    open_price = quote.open
    high_price = quote.high
    low_price = quote.low
    last_price = quote.last_price
    if open_price is None or high_price is None or low_price is None:
        return None
    if not all(
        isinstance(value, (int, float)) and value > 0
        for value in (open_price, high_price, low_price, last_price)
    ):
        return None
    volume = int(quote.volume or 0)
    return CandleRecord(
        time=now.date().isoformat(),
        open=float(open_price),
        high=float(high_price),
        low=float(low_price),
        close=float(last_price),
        volume=volume,
    )


def _upsert_by_date(
    candles: list[CandleRecord],
    session: CandleRecord,
    *,
    replace_existing: bool,
) -> list[CandleRecord]:
    session_day = session.time[:10]
    result: list[CandleRecord] = []
    found = False
    for candle in candles:
        if candle.time[:10] == session_day:
            found = True
            if not replace_existing:
                result.append(candle)
            continue
        result.append(candle)
    if replace_existing or not found:
        result.append(session)
    return sorted(result, key=lambda item: item.time)


def months_needed_for_limit(limit: int) -> int:
    return max(2, math.ceil(limit / 20) + 1)


def _month_start(today: date, month_offset: int) -> date:
    year = today.year
    month = today.month - month_offset
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _latest_bar_date(candles: list[CandleRecord]) -> date | None:
    latest: date | None = None
    for candle in candles:
        try:
            bar_date = date.fromisoformat(candle.time[:10])
        except ValueError:
            continue
        if latest is None or bar_date > latest:
            latest = bar_date
    return latest


def _overlay(stored: list[CandleRecord], fetched: list[CandleRecord]) -> list[CandleRecord]:
    by_day = {candle.time[:10]: candle for candle in stored}
    for candle in fetched:
        by_day[candle.time[:10]] = candle
    return sorted(by_day.values(), key=lambda item: item.time)


def _last_completed_session_date(stock: StockRecord, now: datetime) -> date | None:
    day = now.date()
    for _ in range(12):
        probe = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo)
        if is_trading_day(stock.market, probe):
            _session_start, session_end = market_session_window(stock.market, day)
            if day < now.date() or now.time() >= session_end:
                return day
        day -= timedelta(days=1)
    return None


daily_price_history_service = DailyPriceHistoryService()
