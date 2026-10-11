from fastapi import APIRouter, Depends, Query

from app.api.deps import get_effective_user_id
from app.models.stock import StockRecord
from app.schemas.stock import StockResponse
from app.services.search_service import search_service


router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(get_effective_user_id)],
)


@router.get("", response_model=list[StockResponse])
def search_stocks(
    q: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[StockResponse]:
    query = q.strip()
    # Short queries are noise; keep upstream catalog scans off the hot path (#272).
    if len(query) < 2:
        return []
    return [_to_schema(stock) for stock in search_service.search(query)[:limit]]


def _to_schema(stock: StockRecord) -> StockResponse:
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

