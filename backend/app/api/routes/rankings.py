from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.schemas.rankings import RankingResponse, RankingType, TwBoard
from app.services.rankings_snapshot_service import rankings_snapshot_service


router = APIRouter(prefix="/rankings", tags=["rankings"])


@router.get("", response_model=RankingResponse)
def get_rankings(
    market: Literal["US", "TW"] = Query(...),
    ranking_type: RankingType = Query(..., alias="type"),
    limit: int = Query(default=50, ge=1, le=50),
    tw_board: TwBoard | None = Query(default=None, alias="twBoard"),
) -> RankingResponse:
    return rankings_snapshot_service.get_rankings(
        market=market,
        ranking_type=ranking_type,
        limit=limit,
        tw_board=tw_board,
    )


@router.post("/snapshots/capture")
def capture_rankings_snapshots(
    x_rankings_capture_token: str | None = Header(default=None),
) -> dict[str, int]:
    expected = settings.rankings_capture_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Rankings snapshot capture endpoint is not configured",
        )
    if x_rankings_capture_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    captured = rankings_snapshot_service.capture_scheduled_snapshots(
        trigger="capture_endpoint",
    )
    return {"captured": captured}
