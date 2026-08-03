from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.tw_margin_short import (
    TaiwanMarginShortHistoryPointResponse,
    TaiwanMarginShortResponse,
    TaiwanMarginShortSummaryResponse,
)
from app.services.taiwan_market_data import taiwan_shares_per_lot
from app.services.tw_margin_short_service import (
    TaiwanMarginShortRecord,
    TaiwanMarginShortResult,
    tw_margin_short_service,
)


router = APIRouter(prefix="/stocks", tags=["tw-margin-short"])


@router.get(
    "/{stock_id}/margin-short",
    responses={
        404: {"description": "Stock not found or not a Taiwan stock"},
        400: {"description": "Taiwan margin-short market is unavailable for this stock"},
        502: {"description": "Official upstream unavailable, access denied, or schema failure"},
        503: {"description": "Official upstream rate-limited"},
    },
)
def get_tw_margin_short(
    stock_id: str,
    days: Annotated[int, Query(ge=1, le=180)] = 90,
) -> TaiwanMarginShortResponse:
    try:
        result = tw_margin_short_service.get_margin_short(stock_id, days=days)
    except ValueError as exc:
        detail = str(exc)
        if detail in {
            "Stock not found",
            "Taiwan margin-short data is only available for TW stocks",
        }:
            raise HTTPException(status_code=404, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc
    except RuntimeError as exc:
        detail = str(exc)
        status_code = 503 if "rate-limited" in detail else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return _tw_margin_short_schema(result)


def _tw_margin_short_summary_schema(
    record: TaiwanMarginShortRecord,
) -> TaiwanMarginShortSummaryResponse:
    return TaiwanMarginShortSummaryResponse(**_lot_fields(record))


def _tw_margin_short_history_point_schema(
    record: TaiwanMarginShortRecord,
) -> TaiwanMarginShortHistoryPointResponse:
    return TaiwanMarginShortHistoryPointResponse(
        date=record.date,
        closePrice=record.close_price,
        **_lot_fields(record),
    )


def _lot_fields(record: TaiwanMarginShortRecord) -> dict[str, float | int | None]:
    return {
        "marginBalanceLots": record.margin_balance_lots,
        "shortMarginBalanceLots": record.short_margin_balance_lots,
        "borrowedShortBalanceLots": record.borrowed_short_balance_lots,
        "borrowedShortBalanceChangeLots": record.borrowed_short_balance_change_lots,
        "borrowedShortBalanceChangeAmount10k": record.borrowed_short_balance_change_amount_10k,
    }


def _tw_margin_short_schema(
    result: TaiwanMarginShortResult,
) -> TaiwanMarginShortResponse:
    return TaiwanMarginShortResponse(
        stockId=result.stock_id,
        symbol=result.symbol,
        market=result.market,
        latestDate=result.latest_date,
        summary=(
            _tw_margin_short_summary_schema(result.summary)
            if result.summary is not None
            else None
        ),
        history=[_tw_margin_short_history_point_schema(item) for item in result.history],
        sharesPerLot=taiwan_shares_per_lot(result.symbol),
        source=result.source,
        emptyReason=result.empty_reason,
    )
