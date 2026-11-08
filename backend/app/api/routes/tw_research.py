"""Authenticated Taiwan research contract, universe, and bounded scan APIs (#310–#312).

GET handlers are read-only over already computed in-memory scan state and never
trigger upstream history/quote fetches. POST /scans and /scans/batches may
compute and backfill within explicit budgets.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_effective_user_id
from app.schemas.tw_research import (
    CreateScanRequest,
    RejectedStockResponse,
    ResearchContractResponse,
    ResolvedUniverseResponse,
    RunBatchRequest,
    ScanSnapshotResponse,
    UniverseSelectorRequest,
)
from app.services.tw_research.contract import build_research_contract
from app.services.tw_research.preset import starter_preset_stock_ids, starter_preset_theme_keys
from app.services.tw_research.scan import tw_research_scan_service
from app.services.tw_research.universe import UniverseSelector, resolve_universe

router = APIRouter(
    prefix="/tw-research",
    tags=["tw-research"],
    dependencies=[Depends(get_effective_user_id)],
)


@router.get("/contract", response_model=ResearchContractResponse)
def get_research_contract() -> ResearchContractResponse:
    return build_research_contract()


@router.get("/universe/starter-preset")
def get_starter_preset() -> dict[str, object]:
    ids = starter_preset_stock_ids()
    return {
        "optional": True,
        "notAProductLimit": True,
        "themeKeys": list(starter_preset_theme_keys()),
        "stockIds": list(ids),
        "uniqueCount": len(ids),
        "notes": [
            "Pilot/seed configuration only.",
            "Any catalog TWSE/TPEx symbol can be scanned without code changes.",
        ],
    }


@router.post("/universe/resolve", response_model=ResolvedUniverseResponse)
def post_resolve_universe(body: UniverseSelectorRequest) -> ResolvedUniverseResponse:
    selector = _selector_from_request(body)
    try:
        resolved = resolve_universe(selector)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResolvedUniverseResponse(
        universeVersion=resolved.universe_version,
        mode=resolved.selector.mode,
        stockIds=list(resolved.stock_ids),
        uniqueCount=len(resolved.stock_ids),
        rejected=[
            RejectedStockResponse(stockId=item.stock_id, reason=item.reason)
            for item in resolved.rejected
        ],
        themeKeysApplied=list(resolved.theme_keys_applied),
        notes=list(resolved.notes),
    )


@router.post("/scans", response_model=ScanSnapshotResponse)
def create_scan(body: CreateScanRequest) -> ScanSnapshotResponse:
    selector = _selector_from_request(body.universe)
    try:
        state = tw_research_scan_service.create_scan(
            selector,
            target_count=body.targetCount,
            trading_day=body.tradingDay,
            max_upstream_month_fetches=body.maxUpstreamMonthFetches,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScanSnapshotResponse(scan=state.snapshot())


@router.post("/scans/batches", response_model=ScanSnapshotResponse)
def run_scan_batch(body: RunBatchRequest) -> ScanSnapshotResponse:
    try:
        state = tw_research_scan_service.run_batch(
            body.scanId,
            batch_size=body.batchSize,
            timeout_seconds=body.timeoutSeconds,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScanSnapshotResponse(scan=state.snapshot())


@router.get("/scans/{scan_id}", response_model=ScanSnapshotResponse)
def get_scan(scan_id: str) -> ScanSnapshotResponse:
    """Read-only snapshot. Does not trigger upstream fetches (#312)."""
    state = tw_research_scan_service.get_scan(scan_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Unknown scan_id: {scan_id}")
    return ScanSnapshotResponse(scan=state.snapshot())


def _selector_from_request(body: UniverseSelectorRequest) -> UniverseSelector:
    return UniverseSelector(
        mode=body.mode,
        theme_keys=tuple(body.themeKeys),
        stock_ids=tuple(body.stockIds),
    )
