from fastapi import APIRouter, HTTPException, Query

from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.schemas.quote import CandleResponse, HistoryEnvelope
from app.schemas.stock import StockResponse
from app.services.news_service import news_service
from app.services.quote_service import quote_service


router = APIRouter(prefix="/stocks", tags=["history"])


@router.get("/{stock_id}/history", response_model=HistoryEnvelope)
def get_history(
    stock_id: str,
    range_name: str = Query(default="1D", alias="range"),
) -> HistoryEnvelope:
    data = quote_service.get_history(stock_id, range_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    stock, candles = data
    return HistoryEnvelope(
        stock=_stock_schema(stock),
        range=range_name,
        candles=[_candle_schema(candle) for candle in candles],
    )


@router.get("/{stock_id}/news")
def get_stock_news(stock_id: str) -> list[dict[str, str]]:
    stock = quote_service.get_stock(stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return [
        {
            "title": item.title,
            "url": item.url,
            "source": item.source,
            "publishedAt": item.published_at,
        }
        for item in news_service.get_recent_news(stock)
    ]


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


def _candle_schema(candle: CandleRecord) -> CandleResponse:
    return CandleResponse(
        time=candle.time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )
