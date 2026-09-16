from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from app.schemas.tw_ownership_concentration import (
    TaiwanOwnershipConcentrationPointResponse,
    TaiwanOwnershipConcentrationResponse,
)
from app.services.tw_ownership_concentration_service import (
    TaiwanOwnershipConcentrationPoint,
    TaiwanOwnershipConcentrationResult,
    tw_ownership_concentration_service,
)

router = APIRouter(prefix="/stocks", tags=["tw-ownership-concentration"])

OwnershipRangeQuery = Literal["3M", "6M", "1Y"]


@router.get(
    "/{stock_id}/tw-ownership-concentration",
    responses={
        404: {"description": "Stock not found or not a Taiwan stock"},
        400: {"description": "Invalid ownership concentration range"},
        502: {"description": "Official upstream temporarily unavailable"},
    },
)
def get_tw_ownership_concentration(
    stock_id: str,
    range: Annotated[OwnershipRangeQuery, Query()] = "1Y",
) -> TaiwanOwnershipConcentrationResponse:
    try:
        result = tw_ownership_concentration_service.get_ownership_concentration(
            stock_id,
            range_name=range,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail in {
            "Stock not found",
            "Taiwan ownership concentration is only available for TW stocks",
        }:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _tw_ownership_concentration_schema(result)


def _tw_ownership_concentration_schema(
    result: TaiwanOwnershipConcentrationResult,
) -> TaiwanOwnershipConcentrationResponse:
    return TaiwanOwnershipConcentrationResponse(
        stockId=result.stock_id,
        range=result.range,
        asOf=result.as_of,
        source=result.source,
        stale=result.stale,
        points=[_point_schema(point) for point in result.points],
    )


def _point_schema(
    point: TaiwanOwnershipConcentrationPoint,
) -> TaiwanOwnershipConcentrationPointResponse:
    return TaiwanOwnershipConcentrationPointResponse(
        date=point.date,
        holders400LotsOrMorePercent=point.holders_400_lots_or_more_percent,
        foreignOwnershipPercent=point.foreign_ownership_percent,
        investmentTrustOwnershipPercent=point.investment_trust_ownership_percent,
        dealerOwnershipPercent=point.dealer_ownership_percent,
        directorOwnershipPercent=point.director_ownership_percent,
        weeklyClose=point.weekly_close,
    )
