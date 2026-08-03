from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord


def stock_record(
    stock_id: str,
    *,
    market: str,
    symbol: str,
    name: str,
    exchange: str,
    currency: str,
    timezone: str,
    name_en: str | None = None,
    name_zh_tw: str | None = None,
) -> StockRecord:
    return StockRecord(
        id=stock_id,
        market=market,
        symbol=symbol,
        name=name,
        exchange=exchange,
        currency=currency,
        timezone=timezone,
        name_en=name_en,
        name_zh_tw=name_zh_tw,
    )


def quote_record(
    stock_id: str,
    *,
    last_price: float,
    change: float,
    change_percent: float,
    previous_close: float,
    timestamp: str,
    is_market_open: bool,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int | None = None,
) -> QuoteRecord:
    return QuoteRecord(
        id=stock_id,
        last_price=last_price,
        change=change,
        change_percent=change_percent,
        previous_close=previous_close,
        timestamp=timestamp,
        is_market_open=is_market_open,
        open=open_price,
        high=high,
        low=low,
        volume=volume,
    )


def candle_record(
    *,
    time: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> CandleRecord:
    return CandleRecord(
        time=time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class StubUSProvider:
    """Shared US provider stub for quote service / refresher unit tests."""

    market = "US"

    def __init__(self) -> None:
        self.quote_calls: list[str] = []
        self.live_quote_calls: list[str] = []
        self._stocks = {
            "US:AAPL": stock_record(
                "US:AAPL",
                market="US",
                symbol="AAPL",
                name="Apple Inc.",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Apple Inc.",
            ),
            "US:MSFT": stock_record(
                "US:MSFT",
                market="US",
                symbol="MSFT",
                name="Microsoft Corporation",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Microsoft Corporation",
            ),
        }
        self._quotes = {
            "US:AAPL": quote_record(
                "US:AAPL",
                last_price=187.0,
                change=1.0,
                change_percent=0.5,
                previous_close=186.0,
                timestamp="2026-05-12T10:00:00-04:00",
                is_market_open=True,
                volume=100,
            ),
            "US:MSFT": quote_record(
                "US:MSFT",
                last_price=100.0,
                change=1.0,
                change_percent=1.0,
                previous_close=99.0,
                timestamp="2026-05-12T10:00:00-04:00",
                is_market_open=True,
                volume=300,
            ),
        }
        self.quote_delay_seconds = 0.0
        self.fail_ids: set[str] = set()
        self.fail_exc: Exception | None = None

    def list_stocks(self):
        return list(self._stocks.values())

    def search(self, query: str):
        return []

    def get_stock(self, stock_id: str):
        return self._stocks.get(stock_id)

    def get_quote(self, stock_id: str):
        self.quote_calls.append(stock_id)
        if self.quote_delay_seconds:
            import time

            time.sleep(self.quote_delay_seconds)
        if stock_id in self.fail_ids:
            raise self.fail_exc or RuntimeError(f"provider failed for {stock_id}")
        return self._quotes.get(stock_id)

    def get_live_quote(self, stock_id: str):
        self.live_quote_calls.append(stock_id)
        if stock_id == "US:MSFT":
            return self._quotes["US:MSFT"]
        return None

    def get_history(self, stock_id: str, range_name: str, *, limit: int | None = None):
        del range_name, limit
        return []


class FakeTrackedIdsRepo:
    """In-memory watchlist/portfolio repo stub for refresher unit tests."""

    def __init__(self, ids: list[str] | None = None) -> None:
        self.ids = list(ids or [])

    def is_available(self) -> bool:
        return True

    def list_distinct_stock_ids(self) -> list[str]:
        return list(self.ids)

    def list_distinct_position_stock_ids(self) -> list[str]:
        return list(self.ids)


def age_memory_cached_quote(
    stock_id: str,
    *,
    cached_at: str = "2026-01-01T00:00:00Z",
) -> None:
    """Force a soft-stale cache entry in the in-process quote cache."""
    import json

    from app.services import quote_redis_cache as cache

    with cache._memory_lock:
        raw = cache._memory_quotes[stock_id][1]
    payload = json.loads(raw)
    payload["cached_at"] = cached_at
    with cache._memory_lock:
        cache._memory_quotes[stock_id] = (1e18, json.dumps(payload))


def install_in_memory_sessionlocal(monkeypatch):
    """Create a SQLite SessionLocal for repository unit tests."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("app.repositories.session.SessionLocal", session_local)
    return session_local


class FakeQuoteSource:
    def __init__(self, stock=None, stocks=None):
        self._stock = stock
        self._stocks = list(stocks or ([] if stock is None else [stock]))

    def get_stock(self, stock_id: str):
        if self._stock is not None and self._stock.id == stock_id:
            return self._stock
        for stock in self._stocks:
            if stock.id == stock_id:
                return stock
        return None

    def list_stocks(self):
        if self._stocks:
            return list(self._stocks)
        return [self._stock] if self._stock is not None else []


def sample_margin_short_result():
    from app.services.tw_margin_short_service import (
        TaiwanMarginShortRecord,
        TaiwanMarginShortResult,
    )

    record = TaiwanMarginShortRecord(
        date="2026-07-09",
        symbol="1303",
        name="南亞",
        close_price=185.0,
        margin_balance_lots=31_339.0,
        short_margin_balance_lots=0.0,
        borrowed_short_balance_lots=12_345.0,
        borrowed_short_balance_change_lots=141.0,
        borrowed_short_balance_change_amount_10k=2609,
    )
    return TaiwanMarginShortResult(
        stock_id="TW:1303",
        symbol="1303",
        market="TWSE",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TWSE official margin and borrowed-short daily data",
    )
