from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_effective_user_id
from app.schemas.tw_institutional import (
    TaiwanInstitutionalInvestorHistoryPointResponse,
    TaiwanInstitutionalInvestorsResponse,
    TaiwanInstitutionalInvestorSummaryResponse,
)
from app.services.taiwan_market_data import net_shares_to_lots, taiwan_shares_per_lot
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorRecord,
    TaiwanInstitutionalInvestorsResult,
    tw_institutional_service,
)


router = APIRouter(
    prefix="/stocks",
    tags=["tw-institutional"],
    dependencies=[Depends(get_effective_user_id)],
)


@router.get(
    "/{stock_id}/institutional-investors",
    responses={
        404: {"description": "Stock not found or not a Taiwan stock"},
        400: {"description": "Taiwan institutional market is unavailable for this stock"},
        502: {"description": "Official upstream temporarily unavailable"},
    },
)
def get_tw_institutional_investors(
    stock_id: str,
    days: Annotated[int, Query(ge=1, le=180)] = 90,
) -> TaiwanInstitutionalInvestorsResponse:
    try:
        result = tw_institutional_service.get_institutional_investors(
            stock_id,
            days=days,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {
            "Stock not found",
            "Taiwan institutional data is only available for TW stocks",
        }:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _tw_institutional_schema(result)


def _tw_institutional_summary_schema(
    record: TaiwanInstitutionalInvestorRecord,
) -> TaiwanInstitutionalInvestorSummaryResponse:
    return TaiwanInstitutionalInvestorSummaryResponse(
        foreignNetLots=_lots_from_shares(record.foreign_net_shares, record.symbol) or 0.0,
        investmentTrustNetLots=_lots_from_shares(record.investment_trust_net_shares, record.symbol)
        or 0.0,
        dealerNetLots=_lots_from_shares(record.dealer_net_shares, record.symbol) or 0.0,
        totalNetLots=_lots_from_shares(record.total_net_shares, record.symbol) or 0.0,
        dealerSelfNetLots=_lots_from_shares(record.dealer_self_net_shares, record.symbol),
        dealerHedgeNetLots=_lots_from_shares(record.dealer_hedge_net_shares, record.symbol),
    )


def _tw_institutional_history_point_schema(
    record: TaiwanInstitutionalInvestorRecord,
) -> TaiwanInstitutionalInvestorHistoryPointResponse:
    return TaiwanInstitutionalInvestorHistoryPointResponse(
        date=record.date,
        foreignNetLots=_lots_from_shares(record.foreign_net_shares, record.symbol) or 0.0,
        investmentTrustNetLots=_lots_from_shares(record.investment_trust_net_shares, record.symbol)
        or 0.0,
        dealerNetLots=_lots_from_shares(record.dealer_net_shares, record.symbol) or 0.0,
        totalNetLots=_lots_from_shares(record.total_net_shares, record.symbol) or 0.0,
        dealerSelfNetLots=_lots_from_shares(record.dealer_self_net_shares, record.symbol),
        dealerHedgeNetLots=_lots_from_shares(record.dealer_hedge_net_shares, record.symbol),
    )


def _tw_institutional_schema(
    result: TaiwanInstitutionalInvestorsResult,
) -> TaiwanInstitutionalInvestorsResponse:
    return TaiwanInstitutionalInvestorsResponse(
        stockId=result.stock_id,
        symbol=result.symbol,
        market=result.market,
        latestDate=result.latest_date,
        summary=(
            _tw_institutional_summary_schema(result.summary)
            if result.summary is not None
            else None
        ),
        history=[_tw_institutional_history_point_schema(item) for item in result.history],
        sharesPerLot=taiwan_shares_per_lot(result.symbol),
        source=result.source,
    )


def _lots_from_shares(shares: int | None, symbol: str) -> float | None:
    return net_shares_to_lots(shares, symbol=symbol)
