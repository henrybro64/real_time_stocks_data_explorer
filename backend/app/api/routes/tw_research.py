"""Authenticated Taiwan research contract, universe, scan, enrichment, export APIs.

#310–#314. GET handlers are read-only over already computed in-memory scan state
and never trigger upstream history/quote fetches. POST /scans, /scans/batches,
and /scans/enrichment-batches may compute within explicit budgets.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import get_effective_user_id
from app.schemas.tw_research import (
    CreateScanRequest,
    ExportChunkResponse,
    ExportManifestResponse,
    RejectedStockResponse,
    ResearchContractResponse,
    ResolvedUniverseResponse,
    RunBatchRequest,
    RunEnrichmentBatchRequest,
    ScanSnapshotResponse,
    UniverseSelectorRequest,
)
from app.services.tw_research.contract import build_research_contract
from app.services.tw_research.export import (
    build_browser_text_view,
    build_export_chunk,
    build_export_csv,
    build_export_manifest,
)
from app.services.tw_research.preset import starter_preset_stock_ids, starter_preset_theme_keys
from app.services.tw_research.scan import ScanState, tw_research_scan_service
from app.services.tw_research.universe import UniverseSelector, resolve_universe

TAIPEI = ZoneInfo("Asia/Taipei")
T = TypeVar("T")

router = APIRouter(
    prefix="/tw-research",
    tags=["tw-research"],
    dependencies=[Depends(get_effective_user_id)],
)

_EXPORT_KIND_PATTERN = "^(ledger|qualified|enriched)$"


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
    resolved = _call_or_http(lambda: resolve_universe(_selector_from_request(body)))
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
    state = _call_or_http(
        lambda: tw_research_scan_service.create_scan(
            _selector_from_request(body.universe),
            target_count=body.targetCount,
            trading_day=body.tradingDay,
            max_upstream_month_fetches=body.maxUpstreamMonthFetches,
            max_enrichments_per_run=body.maxEnrichmentsPerRun,
        )
    )
    return _snapshot(state)


@router.post("/scans/batches", response_model=ScanSnapshotResponse)
def run_scan_batch(body: RunBatchRequest) -> ScanSnapshotResponse:
    state = _call_or_http(
        lambda: tw_research_scan_service.run_batch(
            body.scanId,
            batch_size=body.batchSize,
            timeout_seconds=body.timeoutSeconds,
        )
    )
    return _snapshot(state)


@router.post("/scans/enrichment-batches", response_model=ScanSnapshotResponse)
def run_enrichment_batch(body: RunEnrichmentBatchRequest) -> ScanSnapshotResponse:
    """Bounded enrichment for dual-pass symbols only (#313)."""
    state = _call_or_http(
        lambda: tw_research_scan_service.run_enrichment_batch(
            body.scanId,
            batch_size=body.batchSize,
        )
    )
    return _snapshot(state)


@router.get("/scans/{scan_id}", response_model=ScanSnapshotResponse)
def get_scan(scan_id: str) -> ScanSnapshotResponse:
    """Read-only snapshot. Does not trigger upstream fetches (#312)."""
    return _snapshot(_require_scan(scan_id))


@router.get("/scans/{scan_id}/export/manifest", response_model=ExportManifestResponse)
def get_export_manifest(scan_id: str) -> ExportManifestResponse:
    state, generated_at = _export_context(scan_id)
    return ExportManifestResponse(
        manifest=build_export_manifest(state, generated_at=generated_at)
    )


@router.get("/scans/{scan_id}/export/chunk", response_model=ExportChunkResponse)
def get_export_chunk(
    scan_id: str,
    chunkIndex: int = Query(default=0, ge=0),
    kind: str = Query(default="ledger", pattern=_EXPORT_KIND_PATTERN),
) -> ExportChunkResponse:
    state, generated_at = _export_context(scan_id)
    chunk = _call_or_http(
        lambda: build_export_chunk(
            state,
            chunk_index=chunkIndex,
            kind=kind,
            generated_at=generated_at,
        )
    )
    return ExportChunkResponse(chunk=chunk)


@router.get("/scans/{scan_id}/export/csv")
def get_export_csv(
    scan_id: str,
    kind: str = Query(default="ledger", pattern=_EXPORT_KIND_PATTERN),
) -> Response:
    state = _require_scan(scan_id)
    body = build_export_csv(state, kind=kind)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tw-research-{scan_id[:8]}.csv"'
        },
    )


@router.get("/scans/{scan_id}/export/browser-text")
def get_export_browser_text(
    scan_id: str,
    chunkIndex: int = Query(default=0, ge=0),
    kind: str = Query(default="ledger", pattern=_EXPORT_KIND_PATTERN),
) -> Response:
    """Authenticated copyable JSON text for a browser-only Agent (#314/#315)."""
    state, generated_at = _export_context(scan_id)
    body = _call_or_http(
        lambda: build_browser_text_view(
            state,
            generated_at=generated_at,
            chunk_index=chunkIndex,
            kind=kind,
        )
    )
    return Response(content=body, media_type="application/json; charset=utf-8")


def _snapshot(state: ScanState) -> ScanSnapshotResponse:
    return ScanSnapshotResponse(scan=state.snapshot())


def _require_scan(scan_id: str) -> ScanState:
    state = tw_research_scan_service.get_scan(scan_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Unknown scan_id: {scan_id}")
    return state


def _export_context(scan_id: str) -> tuple[ScanState, str]:
    return _require_scan(scan_id), datetime.now(TAIPEI).isoformat()


def _call_or_http(action: Callable[[], T]) -> T:
    try:
        return action()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _selector_from_request(body: UniverseSelectorRequest) -> UniverseSelector:
    return UniverseSelector(
        mode=body.mode,
        theme_keys=tuple(body.themeKeys),
        stock_ids=tuple(body.stockIds),
    )
