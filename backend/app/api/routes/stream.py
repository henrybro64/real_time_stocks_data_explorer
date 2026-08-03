import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.routes.quotes import _batch_item_schema
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
                _batch_item_schema(item).model_dump()
                for item in quote_service.get_quotes_cache_first(stock_ids)
            ]
            await websocket.send_json(payload)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return
