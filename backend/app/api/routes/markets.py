from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.market_clock import (
    MARKET_TIMEZONES,
    is_market_open,
    is_within_hour_before_market_open,
    market_label,
    market_local_time,
)
from app.services.commodity_service import (
    CommodityUnavailableError,
    commodity_quote_to_api,
    get_wti_crude_oil_quote,
)
from app.services.fx_service import get_usd_twd_fx_snapshot
from app.services.market_index_service import market_index_service


router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("")
def get_markets() -> list[dict[str, str | bool]]:
    now = datetime.now(timezone.utc)
    payload: list[dict[str, str | bool]] = []
    for market, tz in MARKET_TIMEZONES.items():
        payload.append(
            {
                "market": market,
                "label": market_label(market),
                "timezone": str(tz),
                "isOpen": is_market_open(market, now),
                "localTime": market_local_time(market, now).isoformat(),
                "withinHourBeforeOpen": is_within_hour_before_market_open(market, now),
            }
        )
    return payload


@router.get("/indices")
def get_market_indices() -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "market": item.market,
            "label": item.label,
            "value": item.value,
            "change": item.change,
            "changePercent": item.change_percent,
            "timestamp": item.timestamp,
            "isMarketOpen": item.is_market_open,
            "status": item.status,
            "unavailableReason": item.unavailable_reason,
        }
        for item in market_index_service.get_latest()
    ]


@router.get("/fx/usd-twd")
def get_usd_twd_fx_rate() -> dict[str, Any]:
    return get_usd_twd_fx_snapshot()


@router.get(
    "/commodities/wti-crude-oil",
    responses={502: {"description": "Commodity provider unavailable"}},
)
def get_wti_crude_oil() -> dict[str, Any]:
    """WTI front-month futures (Yahoo ``CL=F``); informational only."""
    try:
        quote = get_wti_crude_oil_quote()
    except CommodityUnavailableError as exc:
        raise HTTPException(
            status_code=502,
            detail="WTI crude oil quote provider unavailable",
        ) from exc
    return commodity_quote_to_api(quote)
