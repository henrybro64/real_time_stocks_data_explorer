from fastapi import APIRouter, HTTPException

from app.schemas.watchlist import WatchlistItemCreate, WatchlistResponse
from app.services.watchlist_service import watchlist_service


router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=WatchlistResponse)
def get_watchlist() -> WatchlistResponse:
    return WatchlistResponse(ids=watchlist_service.get_ids())


@router.post("/items", response_model=WatchlistResponse)
def add_watchlist_item(payload: WatchlistItemCreate) -> WatchlistResponse:
    try:
        ids = watchlist_service.add(payload.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WatchlistResponse(ids=ids)


@router.delete("/items/{stock_id}", response_model=WatchlistResponse)
def remove_watchlist_item(stock_id: str) -> WatchlistResponse:
    return WatchlistResponse(ids=watchlist_service.remove(stock_id))

