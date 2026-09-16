from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.tw_profitability import (
    TaiwanProfitabilityPointResponse,
    TaiwanProfitabilityResponse,
)
from app.services.tw_profitability_service import (
    TaiwanProfitabilityPoint,
    TaiwanProfitabilityResult,
    tw_profitability_service,
)

router = APIRouter(prefix="/stocks", tags=["tw-profitability"])

# Query params arrive as text; parse as int first, then allow-list (#230).
_ALLOWED_QUARTERS = frozenset({8, 12, 20})
_INVALID_QUARTERS_DETAIL = "quarters must be 8, 12, or 20"


@router.get(
    "/{stock_id}/tw-profitability",
    responses={
        404: {"description": "Stock not found or not a Taiwan stock"},
        400: {"description": "Invalid quarters parameter"},
        422: {"description": "Quarters query could not be parsed as an integer"},
        502: {"description": "Upstream temporarily unavailable"},
    },
)
def get_tw_profitability(
    stock_id: str,
    quarters: Annotated[int, Query()] = 12,
) -> TaiwanProfitabilityResponse:
    if quarters not in _ALLOWED_QUARTERS:
        raise HTTPException(status_code=400, detail=_INVALID_QUARTERS_DETAIL)
    try:
        result = tw_profitability_service.get_profitability(
            stock_id,
            quarters=quarters,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {
            "Stock not found",
            "Taiwan profitability margins are only available for TW stocks",
        }:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _tw_profitability_schema(result)


def _tw_profitability_schema(result: TaiwanProfitabilityResult) -> TaiwanProfitabilityResponse:
    return TaiwanProfitabilityResponse(
        stockId=result.stock_id,
        quarters=result.quarters,
        asOf=result.as_of,
        source=result.source,
        stale=result.stale,
        points=[_point_schema(point) for point in result.points],
    )


def _point_schema(point: TaiwanProfitabilityPoint) -> TaiwanProfitabilityPointResponse:
    return TaiwanProfitabilityPointResponse(
        fiscalQuarter=point.fiscal_quarter,
        periodEnd=point.period_end,
        grossMarginPercent=point.gross_margin_percent,
        operatingMarginPercent=point.operating_margin_percent,
        pretaxMarginPercent=point.pretax_margin_percent,
        netMarginPercent=point.net_margin_percent,
        quarterEndClose=point.quarter_end_close,
    )
