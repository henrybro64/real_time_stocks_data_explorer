from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query

from app.core.config import settings
from app.schemas.rankings import (
    RankingResponse,
    RankingType,
    RankingUniverse,
    RankingsRefreshRequest,
    TwBoard,
    coerce_ranking_universe,
)
from app.services.rankings_snapshot_service import (
    RankingsRefreshError,
    rankings_snapshot_service,
)


router = APIRouter(prefix="/rankings", tags=["rankings"])


def _validated_universe(
    market: Literal["US", "TW"],
    tw_board: TwBoard | None,
    universe: RankingUniverse | None,
) -> RankingUniverse:
    try:
        return coerce_ranking_universe(market, universe, tw_board)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=RankingResponse)
def get_rankings(
    market: Literal["US", "TW"] = Query(...),
    ranking_type: RankingType = Query(..., alias="type"),
    limit: int = Query(default=50, ge=1, le=50),
    tw_board: Annotated[TwBoard | None, Query(alias="twBoard")] = None,
    universe: Annotated[RankingUniverse | None, Query()] = None,
) -> RankingResponse:
    resolved_universe = _validated_universe(market, tw_board, universe)
    return rankings_snapshot_service.get_rankings(
        market=market,
        ranking_type=ranking_type,
        limit=limit,
        tw_board=tw_board if market == "TW" else None,
        universe=resolved_universe,
    )


@router.post("/refresh", response_model=RankingResponse)
def refresh_rankings(payload: RankingsRefreshRequest) -> RankingResponse:
    try:
        return rankings_snapshot_service.refresh_rankings(
            market=payload.market,
            ranking_type=payload.rankingType,
            limit=payload.limit,
            tw_board=payload.universe if payload.market == "TW" else None,
            universe=payload.universe,
        )
    except RankingsRefreshError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=(
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds is not None
                else None
            ),
        ) from exc


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
