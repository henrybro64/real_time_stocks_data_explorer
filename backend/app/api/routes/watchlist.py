from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_effective_user_id
from app.schemas.watchlist import WatchlistItemCreate, WatchlistResponse
from app.services.watchlist_service import watchlist_service


router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=WatchlistResponse)
def get_watchlist(
    user_id: str = Depends(get_effective_user_id),
) -> WatchlistResponse:
    return WatchlistResponse(ids=watchlist_service.get_ids(user_id))


@router.post("/items", response_model=WatchlistResponse)
def add_watchlist_item(
    payload: WatchlistItemCreate,
    user_id: str = Depends(get_effective_user_id),
) -> WatchlistResponse:
    try:
        ids = watchlist_service.add(user_id, payload.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WatchlistResponse(ids=ids)


@router.delete("/items/{stock_id}", response_model=WatchlistResponse)
def remove_watchlist_item(
    stock_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> WatchlistResponse:
    return WatchlistResponse(ids=watchlist_service.remove(user_id, stock_id))
