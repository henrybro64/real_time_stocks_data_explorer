from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.schemas.rankings import RankingResponse, RankingType
from app.services.rankings_service import rankings_service
from app.services.rankings_snapshot_service import (
    SnapshotMarket,
    rankings_snapshot_service,
)


router = APIRouter(prefix="/rankings", tags=["rankings"])


@router.get("", response_model=RankingResponse)
def get_rankings(
    market: Literal["US", "TW"] = Query(...),
    ranking_type: RankingType = Query(..., alias="type"),
    limit: int = Query(default=50, ge=1, le=50),
) -> RankingResponse:
    return rankings_service.get_rankings(
        market=market,
        ranking_type=ranking_type,
        limit=limit,
    )


@router.post("/snapshot/refresh")
def refresh_rankings_snapshot(
    market: SnapshotMarket = Query(...),
    x_rankings_snapshot_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Internal endpoint hit by the rankings snapshot CronJob.

    Refreshes the Redis-backed quote snapshot for the given market so that the
    public ``GET /rankings`` endpoint can rank a wider universe without doing
    live quote fan-out inside the request.

    Authenticated via the ``RANKINGS_SNAPSHOT_REFRESH_TOKEN`` header secret. If
    the token setting is empty, the endpoint is disabled (503) so a misconfigured
    deployment cannot be used as a free CPU sink.
    """
    expected = settings.rankings_snapshot_refresh_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Rankings snapshot refresh endpoint is not configured",
        )
    if x_rankings_snapshot_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

    result = rankings_snapshot_service.refresh(market)
    return {
        "market": result.market,
        "fetchedAt": result.fetched_at.isoformat(),
        "marketOpen": result.market_open,
        "universeSize": result.universe_size,
        "quoteCount": result.quote_count,
        "skippedCount": result.skipped_count,
    }
