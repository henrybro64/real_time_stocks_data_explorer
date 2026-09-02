from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.db.models import RealizedTrade
from app.schemas.realized_trade import (
    RealizedTradeCreate,
    RealizedTradeCurrency,
    RealizedTradeResponse,
    RealizedTradeUpdate,
)
from app.services.realized_trade_service import (
    RealizedTradeNotFoundError,
    RealizedTradeValidationError,
    calculate_realized_metrics,
    realized_trade_service,
)


router = APIRouter(prefix="/realized-trades", tags=["realized-trades"])

REALIZED_TRADE_ERROR_RESPONSES = {
    400: {"description": "Invalid realized trade payload"},
    404: {"description": "Realized trade not found"},
}


@router.get("")
def list_realized_trades(
    currency: RealizedTradeCurrency | None = Query(default=None),
    user_id: str = Depends(get_effective_user_id),
) -> list[RealizedTradeResponse]:
    trades = realized_trade_service.list_trades(user_id, currency=currency)
    return [_to_response(trade) for trade in trades]


@router.post("", responses=REALIZED_TRADE_ERROR_RESPONSES)
def create_realized_trade(
    payload: RealizedTradeCreate,
    user_id: str = Depends(get_effective_user_id),
) -> RealizedTradeResponse:
    try:
        trade = realized_trade_service.create_trade(user_id, payload)
    except RealizedTradeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(trade)


@router.patch("/{trade_id}", responses=REALIZED_TRADE_ERROR_RESPONSES)
def update_realized_trade(
    trade_id: int,
    payload: RealizedTradeUpdate,
    user_id: str = Depends(get_effective_user_id),
) -> RealizedTradeResponse:
    try:
        trade = realized_trade_service.update_trade(user_id, trade_id, payload)
    except RealizedTradeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RealizedTradeValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(trade)


@router.delete("/{trade_id}", status_code=204, responses=REALIZED_TRADE_ERROR_RESPONSES)
def delete_realized_trade(
    trade_id: int,
    user_id: str = Depends(get_effective_user_id),
) -> None:
    try:
        realized_trade_service.delete_trade(user_id, trade_id)
    except RealizedTradeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _to_response(trade: RealizedTrade) -> RealizedTradeResponse:
    realized_gain, realized_return_percent = calculate_realized_metrics(
        trade.buy_price,
        trade.sell_price,
        trade.shares,
    )
    return RealizedTradeResponse(
        id=trade.id,
        symbol=trade.symbol,
        currency=trade.currency,  # type: ignore[arg-type]
        buyPrice=float(trade.buy_price),
        sellPrice=float(trade.sell_price),
        shares=float(trade.shares),
        buyDate=None if trade.buy_date is None else trade.buy_date.isoformat(),
        sellDate=trade.sell_date.isoformat(),
        realizedGain=realized_gain,
        realizedReturnPercent=realized_return_percent,
        createdAt=trade.created_at.isoformat(),
        updatedAt=trade.updated_at.isoformat(),
    )
