from fastapi import APIRouter, HTTPException, Query

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.quote import QuoteEnvelope, QuoteFreshnessResponse, QuoteResponse
from app.schemas.stock import StockResponse
from app.services.quote_service import QuoteBatchItem, quote_service


router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("", response_model=list[QuoteEnvelope])
def get_quotes(ids: str = Query(...)) -> list[QuoteEnvelope]:
    stock_ids = [item.strip() for item in ids.split(",") if item.strip()]
    return [
        _batch_item_schema(item)
        for item in quote_service.get_quotes_cache_first(stock_ids)
    ]


@router.get(
    "/{stock_id}",
    response_model=QuoteEnvelope,
    responses={
        404: {"description": "Stock not found"},
        502: {"description": "Quote provider unavailable"},
    },
)
def get_quote(stock_id: str) -> QuoteEnvelope:
    try:
        item = quote_service.get_quote_detail(stock_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Quote provider unavailable") from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    # Allow null quote with freshness when the refresher owns upstream (cold miss).
    return _batch_item_schema(item)


def _batch_item_schema(item: QuoteBatchItem) -> QuoteEnvelope:
    return QuoteEnvelope(
        stock=_stock_schema(item.stock),
        quote=_quote_schema(item.quote) if item.quote is not None else None,
        freshness=QuoteFreshnessResponse(
            cachedAt=item.cached_at,
            ageSeconds=item.age_seconds,
            stale=item.stale,
            source=item.source,
            refreshStatus=item.refresh_status,
        ),
    )


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
