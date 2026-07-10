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
