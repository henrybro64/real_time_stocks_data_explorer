"""Authenticated Taiwan research contract, universe, scan, enrichment, export APIs.

#310–#320. GET handlers are read-only over persisted/in-memory scan state or
immutable verified snapshots and never trigger upstream history/quote fetches.
POST /scans, /scans/batches, and /scans/enrichment-batches may compute within
explicit budgets.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar
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
    build_export_chunk_from_rows,
    build_export_csv,
    build_export_manifest,
)
from app.services.tw_research.preset import starter_preset_stock_ids, starter_preset_theme_keys
from app.services.tw_research.scan import (
    ScanState,
    TwResearchDurableStoreError,
    tw_research_scan_service,
)
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
def create_scan(
    body: CreateScanRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ScanSnapshotResponse:
    state = _call_or_http(
        lambda: tw_research_scan_service.create_scan(
            _selector_from_request(body.universe),
            target_count=body.targetCount,
            trading_day=body.tradingDay,
            max_upstream_month_fetches=body.maxUpstreamMonthFetches,
            max_enrichments_per_run=body.maxEnrichmentsPerRun,
            user_id=user_id,
        )
    )
    return _snapshot(state)


@router.post("/scans/batches", response_model=ScanSnapshotResponse)
def run_scan_batch(
    body: RunBatchRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ScanSnapshotResponse:
    _require_owned_scan(body.scanId, user_id)
    state = _call_or_http(
        lambda: tw_research_scan_service.run_batch(
            body.scanId,
            batch_size=body.batchSize,
            timeout_seconds=body.timeoutSeconds,
        )
    )
    return _snapshot(state)


@router.post("/scans/enrichment-batches", response_model=ScanSnapshotResponse)
def run_enrichment_batch(
    body: RunEnrichmentBatchRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ScanSnapshotResponse:
    """Bounded enrichment for dual-pass symbols only (#313)."""
    _require_owned_scan(body.scanId, user_id)
    state = _call_or_http(
        lambda: tw_research_scan_service.run_enrichment_batch(
            body.scanId,
            batch_size=body.batchSize,
        )
    )
    return _snapshot(state)


@router.get("/scans/{scan_id}", response_model=ScanSnapshotResponse)
def get_scan(
    scan_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ScanSnapshotResponse:
    """Read-only snapshot. Does not trigger upstream fetches (#312/#318)."""
    return _snapshot(_require_owned_scan(scan_id, user_id))


@router.get("/exports/latest/manifest", response_model=ExportManifestResponse)
def get_latest_verified_manifest(
    universeVersion: str | None = Query(default=None),
    tradingDay: str | None = Query(default=None),
    user_id: str = Depends(get_effective_user_id),
) -> ExportManifestResponse:
    """Authenticated latest verified snapshot manifest for this user (#319).

    Selection is explicit by universeVersion and/or tradingDay. Results are
    always user-scoped — there is no global fallback across owners.
    """
    snapshot = _latest_verified_snapshot(
        universe_version=universeVersion,
        trading_day=tradingDay,
        user_id=user_id,
    )
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No verified_ready export snapshot matches the selection.",
        )
    manifest = dict(snapshot["manifest_json"] or {})
    # Explicit assign: first finalize freezes snapshotId=null before insert.
    manifest["snapshotId"] = snapshot["snapshot_id"]
    manifest["contentDigest"] = snapshot["content_digest"]
    manifest.setdefault(
        "chunkPathTemplate",
        f"/api/tw-research/exports/snapshots/{snapshot['snapshot_id']}/chunk"
        "?chunkIndex={{chunkIndex}}&kind=ledger",
    )
    return ExportManifestResponse(manifest=manifest)


@router.get(
    "/exports/snapshots/{snapshot_id}/chunk",
    response_model=ExportChunkResponse,
)
def get_verified_snapshot_chunk(
    snapshot_id: str,
    chunkIndex: int = Query(default=0, ge=0),
    kind: str = Query(default="ledger", pattern=_EXPORT_KIND_PATTERN),
    user_id: str = Depends(get_effective_user_id),
) -> ExportChunkResponse:
    """Read-only chunk from an immutable verified snapshot. No upstream fetches."""
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.constants import EXPORT_CHUNK_SIZE

    if not tw_research_repository.is_available():
        raise HTTPException(status_code=404, detail=f"Unknown snapshot_id: {snapshot_id}")
    snapshot = tw_research_repository.get_export_snapshot(snapshot_id)
    if (
        snapshot is None
        or snapshot.get("quality_status") != "verified_ready"
        or snapshot.get("user_id") != user_id
    ):
        raise HTTPException(status_code=404, detail=f"Unknown snapshot_id: {snapshot_id}")
    rows = list(snapshot.get("ledger_json") or [])
    if kind == "qualified":
        rows = [
            row
            for row in rows
            if (row.get("daily") or {}).get("passStatus") == "pass"
            and (row.get("monthly") or {}).get("passStatus") == "pass"
        ]
    elif kind == "enriched":
        rows = [row for row in rows if row.get("enrichment")]
    # Prefer the exact frozen string stored at finalize time so digests stay stable
    # across DB round-trips (SQLite/Postgres datetime serialization can differ).
    manifest = dict(snapshot.get("manifest_json") or {})
    frozen = manifest.get("finalizedAt") or manifest.get("generatedAt")
    if not frozen:
        frozen_at = snapshot["finalized_at"]
        frozen = (
            frozen_at.isoformat() if hasattr(frozen_at, "isoformat") else str(frozen_at)
        )
    # After scan retention, FK scan_id may be NULL; use the frozen manifest id.
    frozen_scan_id = (
        manifest.get("scanId") or snapshot.get("scan_id") or ""
    )
    chunk = _call_or_http(
        lambda: build_export_chunk_from_rows(
            rows,
            chunk_index=chunkIndex,
            chunk_size=EXPORT_CHUNK_SIZE,
            kind=kind,
            scan_id=str(frozen_scan_id),
            trading_day=snapshot["trading_day"],
            frozen_at=str(frozen),
        )
    )
    expected = (snapshot.get("chunk_digests_json") or {}).get(str(chunkIndex))
    if kind == "ledger" and expected and chunk.get("checksumSha256") != expected:
        raise HTTPException(
            status_code=409,
            detail="Chunk digest mismatch against immutable snapshot.",
        )
    return ExportChunkResponse(chunk=chunk)


@router.get("/scans/{scan_id}/export/manifest", response_model=ExportManifestResponse)
def get_export_manifest(
    scan_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ExportManifestResponse:
    state, generated_at = _export_context(scan_id, user_id)
    if state.active_snapshot_id:
        from app.repositories.tw_research_repository import tw_research_repository

        if tw_research_repository.is_available():
            snapshot = tw_research_repository.get_export_snapshot(state.active_snapshot_id)
            if (
                snapshot
                and snapshot.get("quality_status") == "verified_ready"
                and snapshot.get("user_id") == user_id
            ):
                return ExportManifestResponse(manifest=dict(snapshot["manifest_json"] or {}))
    return ExportManifestResponse(
        manifest=build_export_manifest(state, generated_at=generated_at)
    )


@router.get("/scans/{scan_id}/export/chunk", response_model=ExportChunkResponse)
def get_export_chunk(
    scan_id: str,
    chunkIndex: int = Query(default=0, ge=0),
    kind: str = Query(default="ledger", pattern=_EXPORT_KIND_PATTERN),
    user_id: str = Depends(get_effective_user_id),
) -> ExportChunkResponse:
    state, generated_at = _export_context(scan_id, user_id)
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
    user_id: str = Depends(get_effective_user_id),
) -> Response:
    state = _require_owned_scan(scan_id, user_id)
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
    user_id: str = Depends(get_effective_user_id),
) -> Response:
    """Authenticated copyable JSON text for a browser-only Agent (#314/#315)."""
    state, generated_at = _export_context(scan_id, user_id)
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


def _require_owned_scan(scan_id: str, user_id: str) -> ScanState:
    """Return the scan only when it belongs to ``user_id`` (404 otherwise)."""
    state = tw_research_scan_service.get_scan(scan_id)
    if state is None or state.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Unknown scan_id: {scan_id}")
    return state


def _export_context(scan_id: str, user_id: str) -> tuple[ScanState, str]:
    state = _require_owned_scan(scan_id, user_id)
    # Prefer frozen finalize time so Agent re-reads stay byte-stable (#319).
    if state.active_snapshot_id:
        from app.repositories.tw_research_repository import tw_research_repository

        if tw_research_repository.is_available():
            snapshot = tw_research_repository.get_export_snapshot(state.active_snapshot_id)
            if (
                snapshot
                and snapshot.get("user_id") == user_id
                and snapshot.get("finalized_at") is not None
            ):
                frozen = snapshot["finalized_at"]
                generated = (
                    frozen.isoformat() if hasattr(frozen, "isoformat") else str(frozen)
                )
                return state, generated
    return state, state.updated_at or state.created_at or datetime.now(TAIPEI).isoformat()


def _latest_verified_snapshot(
    *,
    universe_version: str | None,
    trading_day: str | None,
    user_id: str,
) -> dict[str, Any] | None:
    from app.repositories.tw_research_repository import tw_research_repository

    if not tw_research_repository.is_available():
        return None
    try:
        return tw_research_repository.latest_verified_snapshot(
            universe_version=universe_version,
            trading_day=trading_day,
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001 — missing migration / misconfigured DB → no snapshot
        return None


def _call_or_http(action: Callable[[], T]) -> T:
    try:
        return action()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TwResearchDurableStoreError as exc:
        # Durable PostgreSQL path failed; do not pretend the batch succeeded.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": exc.operation,
                "scanId": exc.scan_id,
                "message": str(exc),
            },
        ) from exc


def _selector_from_request(body: UniverseSelectorRequest) -> UniverseSelector:
    return UniverseSelector(
        mode=body.mode,
        theme_keys=tuple(body.themeKeys),
        stock_ids=tuple(body.stockIds),
    )
