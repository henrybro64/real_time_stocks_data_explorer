from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.services.taiwan_market_data_refresh_service import (
    taiwan_market_data_refresh_service,
)


router = APIRouter(prefix="/taiwan-market-data", tags=["taiwan-market-data"])


@router.post(
    "/capture",
    responses={
        403: {"description": "Forbidden"},
        503: {"description": "Taiwan market data capture endpoint is not configured"},
    },
)
def capture_taiwan_market_data(
    days: Annotated[int | None, Query(ge=1, le=360)] = None,
    x_taiwan_market_data_token: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    from app.api.capture_auth import require_capture_token

    require_capture_token(
        x_taiwan_market_data_token,
        settings.taiwan_market_data_capture_token,
        not_configured_detail="Taiwan market data capture endpoint is not configured",
    )

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
