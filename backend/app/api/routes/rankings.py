from typing import Literal

from fastapi import APIRouter, Query

from app.schemas.rankings import RankingResponse, RankingType
from app.services.rankings_service import rankings_service


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
