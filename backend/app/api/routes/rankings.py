from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_effective_user_id
from app.core.config import settings
from app.schemas.rankings import (
    RankingResponse,
    RankingType,
    RankingUniverse,
    RankingsHistoryDatesResponse,
    RankingsRefreshRequest,
    TwBoard,
    TwRankingBoard,
    coerce_ranking_universe,
)
from app.services.rankings_archive_service import (
    RankingsArchiveQueryError,
    RankingsArchiveUnavailableError,
    rankings_archive_service,
)
from app.services.rankings_snapshot_service import (
    RankingsRefreshError,
    rankings_snapshot_service,
)


router = APIRouter(prefix="/rankings", tags=["rankings"])

_ARCHIVE_UNAVAILABLE_DETAIL = "Rankings archive is unavailable"
_ARCHIVE_QUERY_FAILED_DETAIL = "Rankings archive query failed"
_ARCHIVE_RETRY_AFTER_SECONDS = "10"
_LIVE_ONLY_PRICE_LIMIT_DETAIL = (
    "Taiwan limit_up and limit_down rankings are live-only and are not archived"
)
_LIVE_ONLY_PRICE_LIMIT_TYPES = {RankingType.limit_up, RankingType.limit_down}


def _archive_unavailable_http() -> HTTPException:
    """503 for configured-but-unreachable Azure SQL archive (#177)."""
    return HTTPException(
        status_code=503,
        detail=_ARCHIVE_UNAVAILABLE_DETAIL,
        headers={"Retry-After": _ARCHIVE_RETRY_AFTER_SECONDS},
    )


def _archive_query_failed_http() -> HTTPException:
    """500 for SQL/app logic failures; do not invite AutoPause retries (#181)."""
    return HTTPException(
        status_code=500,
        detail=_ARCHIVE_QUERY_FAILED_DETAIL,
    )


def _reject_live_only_price_limit(ranking_type: RankingType) -> None:
    if ranking_type in _LIVE_ONLY_PRICE_LIMIT_TYPES:
        raise HTTPException(status_code=422, detail=_LIVE_ONLY_PRICE_LIMIT_DETAIL)


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
    tw_board: Annotated[TwRankingBoard | None, Query(alias="twBoard")] = None,
    universe: Annotated[RankingUniverse | None, Query()] = None,
    _user_id: str = Depends(get_effective_user_id),
) -> RankingResponse:
    if tw_board == "all":
        if market != "TW" or ranking_type not in {RankingType.limit_up, RankingType.limit_down}:
            raise HTTPException(
                status_code=422,
                detail=(
                    "twBoard=all is only supported for Taiwan limit_up and limit_down rankings"
                ),
            )
        return rankings_snapshot_service.get_rankings(
            market=market,
            ranking_type=ranking_type,
            limit=limit,
            tw_board="all",
            universe=None,
        )
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
        422: {"description": "Invalid universe, or a live-only Taiwan price-limit ranking"},
        500: {"description": "Rankings archive query failed"},
        503: {"description": "Rankings archive database unavailable"},
    },
)
def get_rankings_history_dates(
    market: Literal["US", "TW"] = Query(...),
    universe: RankingUniverse = Query(...),
    ranking_type: RankingType = Query(..., alias="type"),
    limit: int = Query(default=5, ge=1, le=30),
    _user_id: str = Depends(get_effective_user_id),
) -> RankingsHistoryDatesResponse:
    """List archived trading dates for a market/universe/type, newest first (#168)."""
    _reject_live_only_price_limit(ranking_type)
    resolved_universe = _validated_universe(market, None, universe)
    if not rankings_archive_service.is_available():
        if not rankings_archive_service.is_configured():
            return RankingsHistoryDatesResponse(
                market=market,
                universe=resolved_universe,
                dates=[],
            )
        raise _archive_unavailable_http()
    try:
        dates = rankings_archive_service.list_history_dates(
            market=market,
            universe=resolved_universe,
            ranking_type=ranking_type,
            limit=limit,
        )
    except RankingsArchiveUnavailableError as exc:
        raise _archive_unavailable_http() from exc
    except RankingsArchiveQueryError as exc:
        raise _archive_query_failed_http() from exc
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
        422: {"description": "Invalid universe or date, or a live-only Taiwan price-limit ranking"},
        500: {"description": "Rankings archive query failed"},
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
    _user_id: str = Depends(get_effective_user_id),
) -> RankingResponse:
    """Return one archived EOD ranking list. Never falls back to current rankings (#168)."""
    _reject_live_only_price_limit(ranking_type)
    resolved_universe = _validated_universe(market, tw_board, universe)
    if not rankings_archive_service.is_available():
        raise _archive_unavailable_http()
    try:
        response = rankings_archive_service.get_history_rankings(
            market=market,
            universe=resolved_universe,
            ranking_type=ranking_type,
            trading_date=trading_date,
            limit=limit,
        )
    except RankingsArchiveUnavailableError as exc:
        raise _archive_unavailable_http() from exc
    except RankingsArchiveQueryError as exc:
        raise _archive_query_failed_http() from exc
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
def refresh_rankings(
    payload: RankingsRefreshRequest,
    _user_id: str = Depends(get_effective_user_id),
) -> RankingResponse:
    try:
        tw_board: TwRankingBoard | None
        universe: RankingUniverse | None
        if payload.universe == "all":
            tw_board = "all"
            universe = None
        elif payload.market == "TW" and payload.universe in {"TWSE", "TPEx"}:
            tw_board = payload.universe
            universe = payload.universe
        else:
            tw_board = None
            universe = payload.universe if payload.universe in {"DJIA", "NASDAQ100"} else None
        return rankings_snapshot_service.refresh_rankings(
            market=payload.market,
            ranking_type=payload.rankingType,
            limit=payload.limit,
            tw_board=tw_board,
            universe=universe,
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
    from app.api.capture_auth import require_capture_token

    require_capture_token(
        x_rankings_capture_token,
        settings.rankings_capture_token,
        not_configured_detail="Rankings snapshot capture endpoint is not configured",
    )
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
    from app.api.capture_auth import require_capture_token

    require_capture_token(
        x_rankings_archive_capture_token,
        settings.rankings_archive_capture_token,
        not_configured_detail="Rankings archive capture endpoint is not configured",
    )
    request = payload or RankingsArchiveCaptureRequest()
    return rankings_archive_service.capture_eod_archives(
        trigger="archive_capture_endpoint",
        market=request.market,
        trading_date=request.tradingDate,
        force=request.force,
    )
