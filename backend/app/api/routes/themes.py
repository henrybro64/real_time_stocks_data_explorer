from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_effective_user_id
from app.models.stock import StockRecord
from app.schemas.stock import StockResponse
from app.schemas.theme import ThemeResponse, ThemeStocksResponse
from app.services.stock_theme_service import ThemeStockGroup, ThemeDefinition, stock_theme_service


router = APIRouter(
    prefix="/themes",
    tags=["themes"],
    dependencies=[Depends(get_effective_user_id)],
)


@router.get("", response_model=list[ThemeResponse])
def list_themes() -> list[ThemeResponse]:
    return [_theme_schema(theme) for theme in stock_theme_service.list_themes()]


@router.get("/{theme_key}/stocks", response_model=ThemeStocksResponse)
def get_theme_stocks(theme_key: str) -> ThemeStocksResponse:
    try:
        group = stock_theme_service.get_stocks_for_theme(theme_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ThemeStocksResponse(
        theme=_theme_schema(group.theme),
        stocks=[_stock_schema(stock) for stock in group.stocks],
    )


def _theme_schema(theme: ThemeDefinition) -> ThemeResponse:
    return ThemeResponse(
        key=theme.key,
        label=theme.label,
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
