from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.services.taiwan_market_data_refresh_service import (
    taiwan_market_data_refresh_service,
)


router = APIRouter(prefix="/taiwan-market-data", tags=["taiwan-market-data"])


@router.post("/capture")
def capture_taiwan_market_data(
    days: int | None = Query(default=None, ge=1, le=360),
    x_taiwan_market_data_token: str | None = Header(default=None),
) -> dict[str, object]:
    expected = settings.taiwan_market_data_capture_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Taiwan market data capture endpoint is not configured",
        )
    if x_taiwan_market_data_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    requested_days = days or settings.taiwan_market_data_refresh_days
    summary = taiwan_market_data_refresh_service.refresh(
        days=requested_days,
        trigger="capture_endpoint",
    )
    return {
        "days": summary.days,
        "rowsUpserted": summary.rows_upserted,
        "errorCount": summary.error_count,
        "dayResults": len(summary.results),
        "startedAt": summary.started_at,
        "finishedAt": summary.finished_at,
    }
