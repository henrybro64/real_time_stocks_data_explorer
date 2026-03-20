from fastapi import APIRouter, HTTPException, Query

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.quote import QuoteEnvelope, QuoteResponse
from app.schemas.stock import StockResponse
from app.services.quote_service import quote_service


router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("", response_model=list[QuoteEnvelope])
def get_quotes(ids: str = Query(...)) -> list[QuoteEnvelope]:
    stock_ids = [item.strip() for item in ids.split(",") if item.strip()]
    return [
        QuoteEnvelope(stock=_stock_schema(stock), quote=_quote_schema(quote))
        for stock, quote in quote_service.get_quotes(stock_ids)
    ]


@router.get("/{stock_id}", response_model=QuoteEnvelope)
def get_quote(stock_id: str) -> QuoteEnvelope:
    stock = quote_service.get_stock(stock_id)
    quote = quote_service.get_quote(stock_id)
    if stock is None or quote is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return QuoteEnvelope(stock=_stock_schema(stock), quote=_quote_schema(quote))


def _stock_schema(stock: StockRecord) -> StockResponse:
    return StockResponse(
        id=stock.id,
        market=stock.market,
        symbol=stock.symbol,
        name=stock.name,
        exchange=stock.exchange,
        currency=stock.currency,
        timezone=stock.timezone,
        nameEn=stock.name_en,
        nameZhTw=stock.name_zh_tw,
    )


def _quote_schema(quote: QuoteRecord) -> QuoteResponse:
    return QuoteResponse(
        id=quote.id,
        lastPrice=quote.last_price,
        change=quote.change,
        changePercent=quote.change_percent,
        previousClose=quote.previous_close,
        open=quote.open,
        high=quote.high,
        low=quote.low,
        volume=quote.volume,
        timestamp=quote.timestamp,
        isMarketOpen=quote.is_market_open,
    )

