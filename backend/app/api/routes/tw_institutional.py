from fastapi import APIRouter, HTTPException, Query

from app.schemas.tw_institutional import (
    TaiwanInstitutionalInvestorHistoryPointResponse,
    TaiwanInstitutionalInvestorsResponse,
    TaiwanInstitutionalInvestorSummaryResponse,
)
from app.services.tw_institutional_service import (
    TaiwanInstitutionalInvestorRecord,
    TaiwanInstitutionalInvestorsResult,
    tw_institutional_service,
)


router = APIRouter(prefix="/stocks", tags=["history"])


@router.get(
    "/{stock_id}/institutional-investors",
    response_model=TaiwanInstitutionalInvestorsResponse,
)
def get_tw_institutional_investors(
    stock_id: str,
    days: int = Query(default=90, ge=1, le=180),
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
        foreignNetLots=_lots_from_shares(record.foreign_net_shares) or 0.0,
        investmentTrustNetLots=_lots_from_shares(record.investment_trust_net_shares) or 0.0,
        dealerNetLots=_lots_from_shares(record.dealer_net_shares) or 0.0,
        totalNetLots=_lots_from_shares(record.total_net_shares) or 0.0,
        dealerSelfNetLots=_lots_from_shares(record.dealer_self_net_shares),
        dealerHedgeNetLots=_lots_from_shares(record.dealer_hedge_net_shares),
    )


def _tw_institutional_history_point_schema(
    record: TaiwanInstitutionalInvestorRecord,
) -> TaiwanInstitutionalInvestorHistoryPointResponse:
    return TaiwanInstitutionalInvestorHistoryPointResponse(
        date=record.date,
        foreignNetLots=_lots_from_shares(record.foreign_net_shares) or 0.0,
        investmentTrustNetLots=_lots_from_shares(record.investment_trust_net_shares) or 0.0,
        dealerNetLots=_lots_from_shares(record.dealer_net_shares) or 0.0,
        totalNetLots=_lots_from_shares(record.total_net_shares) or 0.0,
        dealerSelfNetLots=_lots_from_shares(record.dealer_self_net_shares),
        dealerHedgeNetLots=_lots_from_shares(record.dealer_hedge_net_shares),
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
        source=result.source,
    )


def _lots_from_shares(shares: int | None) -> float | None:
    if shares is None:
        return None
    return round(shares / 1000.0, 3)
