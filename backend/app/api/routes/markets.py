from datetime import datetime

from fastapi import APIRouter
from zoneinfo import ZoneInfo

from app.services.market_clock import (
    MARKET_TIMEZONES,
    is_market_open,
    market_label,
)
from app.services.fx_service import get_usd_twd_fx_snapshot
from app.services.market_index_service import market_index_service


router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("")
def get_markets() -> list[dict[str, str | bool]]:
    payload: list[dict[str, str | bool]] = []
    for market, timezone in MARKET_TIMEZONES.items():
        current = datetime.now(ZoneInfo(str(timezone)))
        payload.append(
            {
                "market": market,
                "label": market_label(market),
                "timezone": str(timezone),
                "isOpen": is_market_open(market, current),
                "localTime": current.isoformat(),
            }
        )
    return payload


@router.get("/indices")
def get_market_indices() -> list[dict[str, str | bool | float]]:
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
        }
        for item in market_index_service.get_latest()
    ]


@router.get("/fx/usd-twd")
def get_usd_twd_fx_rate() -> dict[str, str | float]:
    return get_usd_twd_fx_snapshot()
