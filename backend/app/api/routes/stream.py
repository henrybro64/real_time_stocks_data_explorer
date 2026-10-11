import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import identity_from_session
from app.api.routes.quotes import _batch_item_schema, normalize_quote_ids
from app.core.config import settings
from app.services.quote_service import quote_service


router = APIRouter(tags=["stream"])


@router.websocket("/stream")
async def stream_quotes(websocket: WebSocket) -> None:
    """Push cache-first quote snapshots. Requires a session when auth is on (#272)."""
    # SessionMiddleware stores the session on the WebSocket scope. Building an
    # HTTP Request from that scope asserts scope["type"] == "http" and crashes.
    identity = await asyncio.to_thread(
        identity_from_session,
        websocket.scope.get("session"),
    )
    await websocket.accept()
    if identity.auth_enabled and not identity.is_authenticated:
        await websocket.close(code=4401)
        return

    ids_raw = websocket.query_params.get("ids", "")
    raw_ids = [item.strip() for item in ids_raw.split(",") if item.strip()]
    try:
        stock_ids = normalize_quote_ids(raw_ids)
    except ValueError:
        await websocket.close(code=4400)
        return
    max_ids = int(settings.quote_batch_ids_max)
    if len(stock_ids) > max_ids:
        await websocket.close(code=4400)
        return
    try:
        while True:
            # Keep Redis / catalog work off the event loop (#272).
            items = await asyncio.to_thread(
                quote_service.get_quotes_cache_first, stock_ids
            )
            payload = [_batch_item_schema(item).model_dump() for item in items]
            await websocket.send_json(payload)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return
