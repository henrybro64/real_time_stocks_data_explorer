import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas.quote import QuoteEnvelope, QuoteResponse
from app.schemas.stock import StockResponse
from app.services.quote_service import quote_service


router = APIRouter(tags=["stream"])


@router.websocket("/stream")
async def stream_quotes(websocket: WebSocket) -> None:
    ids_raw = websocket.query_params.get("ids", "")
    stock_ids = [item.strip() for item in ids_raw.split(",") if item.strip()]
    await websocket.accept()
    try:
        while True:
            payload = [
                QuoteEnvelope(
                    stock=StockResponse(
                        id=stock.id,
                        market=stock.market,
                        symbol=stock.symbol,
                        name=stock.name,
                        exchange=stock.exchange,
                        currency=stock.currency,
                        timezone=stock.timezone,
                        nameEn=stock.name_en,
                        nameZhTw=stock.name_zh_tw,
                    ),
                    quote=QuoteResponse(
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
                    ),
                ).model_dump()
                for stock, quote in quote_service.get_quotes(stock_ids)
            ]
            await websocket.send_json(payload)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return

