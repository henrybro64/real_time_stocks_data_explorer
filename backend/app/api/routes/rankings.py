from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.rankings import (
    RankingResponse,
    RankingType,
    RankingUniverse,
    RankingsHistoryDatesResponse,
    RankingsRefreshRequest,
    TwBoard,
    coerce_ranking_universe,
)
from app.services.rankings_archive_service import (
    RankingsArchiveUnavailableError,
    rankings_archive_service,
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


@router.get(
    "",
    response_model=RankingResponse,
    responses={422: {"description": "Invalid universe or twBoard for the selected market"}},
)
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


@router.get(
    "/history/dates",
    response_model=RankingsHistoryDatesResponse,
    responses={
        422: {"description": "Invalid universe for the selected market"},
        503: {"description": "Rankings archive database unavailable"},
    },
)
def get_rankings_history_dates(
    market: Literal["US", "TW"] = Query(...),
    universe: RankingUniverse = Query(...),
    ranking_type: RankingType = Query(..., alias="type"),
    limit: int = Query(default=5, ge=1, le=30),
) -> RankingsHistoryDatesResponse:
    """List archived trading dates for a market/universe/type, newest first (#168)."""
    resolved_universe = _validated_universe(market, None, universe)
    if not rankings_archive_service.is_available():
        if not rankings_archive_service.is_configured():
            return RankingsHistoryDatesResponse(
                market=market,
                universe=resolved_universe,
                dates=[],
            )
        raise HTTPException(
            status_code=503,
            detail="Rankings archive is unavailable",
            headers={"Retry-After": "10"},
        )
    try:
        dates = rankings_archive_service.list_history_dates(
            market=market,
            universe=resolved_universe,
            ranking_type=ranking_type,
            limit=limit,
        )
    except RankingsArchiveUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Rankings archive is unavailable",
            headers={"Retry-After": "10"},
        ) from exc
    return RankingsHistoryDatesResponse(
        market=market,
        universe=resolved_universe,
        dates=[d.isoformat() for d in dates],
    )


@router.get(
    "/history",
    response_model=RankingResponse,
    responses={
        404: {"description": "No archive for the requested trading date"},
        422: {"description": "Invalid universe or date for the selected market"},
        503: {"description": "Rankings archive database unavailable"},
    },
)
def get_rankings_history(
    market: Literal["US", "TW"] = Query(...),
    ranking_type: RankingType = Query(..., alias="type"),
    trading_date: date = Query(..., alias="date"),
    limit: int = Query(default=50, ge=1, le=50),
    tw_board: Annotated[TwBoard | None, Query(alias="twBoard")] = None,
    universe: Annotated[RankingUniverse | None, Query()] = None,
) -> RankingResponse:
    """Return one archived EOD ranking list. Never falls back to current rankings (#168)."""
    resolved_universe = _validated_universe(market, tw_board, universe)
    if not rankings_archive_service.is_available():
        raise HTTPException(
            status_code=503,
            detail="Rankings archive is unavailable",
            headers={"Retry-After": "10"},
        )
    try:
        response = rankings_archive_service.get_history_rankings(
            market=market,
            universe=resolved_universe,
            ranking_type=ranking_type,
            trading_date=trading_date,
            limit=limit,
        )
    except RankingsArchiveUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Rankings archive is unavailable",
            headers={"Retry-After": "10"},
        ) from exc
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No archived rankings for {market}/{resolved_universe}/"
                f"{ranking_type.value} on {trading_date.isoformat()}"
            ),
        )
    return response


@router.post("/refresh")
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


class RankingsArchiveCaptureRequest(BaseModel):
    market: Literal["US", "TW"] | None = None
    tradingDate: date | None = None
    force: bool = Field(
        default=False,
        description=(
            "When true, skip settle-time gate. Still skips non-trading dates and "
            "never bypasses provider trading-date validation."
        ),
    )


@router.post(
    "/archive/capture",
    responses={
        403: {"description": "Capture token missing or invalid"},
        503: {"description": "Capture endpoint not configured"},
    },
)
def capture_rankings_archive(
    payload: RankingsArchiveCaptureRequest | None = None,
    x_rankings_archive_capture_token: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Token-gated manual EOD archive capture into Azure SQL (#167)."""
    expected = settings.rankings_archive_capture_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Rankings archive capture endpoint is not configured",
        )
    if x_rankings_archive_capture_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    request = payload or RankingsArchiveCaptureRequest()
    return rankings_archive_service.capture_eod_archives(
        trigger="archive_capture_endpoint",
        market=request.market,
        trading_date=request.tradingDate,
        force=request.force,
    )
