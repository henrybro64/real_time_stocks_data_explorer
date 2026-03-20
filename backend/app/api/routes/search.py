from fastapi import APIRouter, Query

from app.models.stock import StockRecord
from app.schemas.stock import StockResponse
from app.services.search_service import search_service


router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[StockResponse])
def search_stocks(
    q: str = Query(default=""),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[StockResponse]:
    return [_to_schema(stock) for stock in search_service.search(q)[:limit]]


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

