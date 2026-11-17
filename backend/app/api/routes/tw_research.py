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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from app.api.deps import get_effective_user_id
from app.core.config import settings
from app.schemas.tw_research import (
    CandidateSetResponse,
    CandidateSetSubmitRequest,
    CandidateTechnicalScanRequest,
    CreateScanRequest,
    ExportChunkResponse,
    ExportManifestResponse,
    FirstPassBatchRequest,
    FirstPassBatchResponse,
    FirstPassDrainRequest,
    FirstPassDrainResponse,
    FirstPassJobBatchRequest,
    FirstPassJobCreateRequest,
    FirstPassJobListResponse,
    FirstPassJobProgressResponse,
    FirstPassJobResponse,
    FirstPassJobSymbolsResponse,
    ResearchRunAdoptFirstPassRequest,
    ResearchRunDrainControlRequest,
    RejectedStockResponse,
    RelativePriceBasisRequest,
    RelativePriceBasisResponse,
    ReportEvidenceRequest,
    ReportEvidenceResponse,
    ResearchContractResponse,
    ResearchMembershipPreviewRequest,
    ResearchMembershipResponse,
    ResearchMembershipSaveRequest,
    ResearchRunResponse,
    ResearchRunStartRequest,
    ResolvedUniverseResponse,
    TechnicalResultsResponse,
    ResumeBudgetWindowRequest,
    RunBatchRequest,
    RunEnrichmentBatchRequest,
    ScheduleRunRequest,
    ScheduleRunResponse,
    ScanListResponse,
    ScanProgressResponse,
    ScanSnapshotResponse,
    UniverseSelectorRequest,
)
from app.services.tw_research.contract import build_research_contract, load_feasibility_matrix
from app.services.tw_research.export import (
    build_browser_text_view,
    build_export_chunk,
    build_export_chunk_from_rows,
    build_export_csv,
    build_export_manifest,
)
from app.services.tw_research.first_pass import collect_first_pass_batch, dedupe_stock_ids
from app.services.tw_research.first_pass_durable import (
    CandidateSetConflict,
    CandidateSetValidationError,
    FirstPassDurableUnavailable,
    create_technical_scan_from_candidate_set,
    latest_candidate_set,
    persist_first_pass_batch,
    submit_candidate_set,
    submit_candidate_set_for_research_run,
)
from app.services.tw_research.first_pass_job import (
    FirstPassJobError,
    first_pass_job_service,
)
from app.services.tw_research.fundamentals_watchlist import (
    ResearchMembershipConflict,
    ResearchMembershipUnavailable,
    research_membership_store,
)
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.preset import starter_preset_stock_ids, starter_preset_theme_keys
from app.services.tw_research.price_basis import evaluate_relative_price_basis
from app.services.tw_research.scan import (
    ScanState,
    TwResearchAdmissionError,
    TwResearchConflictError,
    TwResearchDurableStoreError,
    tw_research_scan_service,
)
from app.services.tw_research.universe import UniverseSelector, resolve_universe
from app.services.daily_price_history_service import daily_price_history_service
from app.services.quote_service import quote_service
from app.services.tw_research.constants import (
    CALCULATION_VERSION_MONTHLY_FIRST,
    PREFERRED_DAILY_HISTORY_LIMIT,
)
from app.services.tw_research.export import _record_export
from app.services.tw_research.report_evidence import (
    build_report_evidence,
    load_zh_tw_example_fixture,
    normalize_first_pass_snapshot,
)
from app.services.tw_research.research_run import (
    ResearchRun,
    ResearchRunConflict,
    ResearchRunGateError,
    ResearchRunStoreUnavailable,
    research_run_store,
)

TAIPEI = ZoneInfo("Asia/Taipei")
T = TypeVar("T")

_PG_LOCK_CONTENTION_CODES = frozenset({"40P01", "55P03"})


def _is_pg_lock_contention(exc: BaseException) -> bool:
    """True for PostgreSQL deadlock (40P01) or lock_not_available (55P03)."""
    from sqlalchemy.exc import OperationalError

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OperationalError):
            orig = getattr(current, "orig", None)
            pgcode = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
            if pgcode in _PG_LOCK_CONTENTION_CODES:
                return True
            text = f"{current} {orig or ''}".lower()
            if "deadlock" in text or "lock not available" in text:
                return True
        current = current.__cause__ or current.__context__  # type: ignore[assignment]
    return False


def _http_concurrent_update_retry(exc: BaseException) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "concurrent_update_retry",
            "reason": "concurrent_update_retry",
        },
    )


router = APIRouter(
    prefix="/tw-research",
    tags=["tw-research"],
    dependencies=[Depends(get_effective_user_id)],
)
# Internal schedule tick uses capture-token auth only — must not inherit session
# dependencies or AUTH_ENABLED=true rejects CronJob calls before the token check (#330).
schedule_router = APIRouter(
    prefix="/tw-research",
    tags=["tw-research"],
)

_EXPORT_KIND_PATTERN = "^(ledger|qualified|enriched)$"


@router.get("/contract", response_model=ResearchContractResponse)
def get_research_contract() -> ResearchContractResponse:
    return build_research_contract()


@router.get("/feasibility-matrix")
def get_feasibility_matrix() -> dict[str, object]:
    """Repository-grounded field feasibility audit (#335). Read-only."""
    return {
        "contractVersion": build_research_contract().contractVersion,
        "matrix": load_feasibility_matrix(),
        "notes": [
            "implemented = wired and safe to consume with provenance",
            "partial = present but incomplete / nonofficial / often null",
            "not_implemented / unavailable = explicit gap; never fabricate",
            "deferred = exists on another path (e.g. technical second-pass)",
        ],
    }


@router.post("/first-pass/batch", response_model=FirstPassBatchResponse)
def post_first_pass_batch(
    body: FirstPassBatchRequest,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassBatchResponse:
    """Bounded first-pass evidence for configured universe symbols (#335/#336).

    Deduplicates **before** ``batchLimit`` and before collection. Does not invoke
    second-pass MACD/KD. Default ``persist=true`` writes an immutable Postgres
    snapshot (fail closed if DB unavailable).
    """
    if body.stockIds:
        ids = dedupe_stock_ids(body.stockIds)
        version = f"explicit:{len(ids)}"
    else:
        resolved = _call_or_http(lambda: resolve_universe(_selector_from_request(body.universe)))
        ids = dedupe_stock_ids(resolved.stock_ids)
        version = resolved.universe_version
    limited = ids[: body.batchLimit]
    result = _call_or_http(
        lambda: collect_first_pass_batch(
            limited,
            universe_version=version,
            include_nonofficial_margins=body.includeNonofficialMargins,
        )
    )
    payload = result.as_dict()
    payload["batchLimit"] = body.batchLimit
    payload["universeSize"] = len(ids)
    payload["truncated"] = len(ids) > len(limited)
    payload["durable"] = False
    if body.persist:
        try:
            snap = persist_first_pass_batch(result, user_id=user_id)
        except FirstPassDurableUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "durable_store_unavailable",
                    "retryable": True,
                    "operation": exc.operation,
                    "message": str(exc),
                },
            ) from exc
        payload["durable"] = True
        payload["snapshotId"] = snap.snapshot_id
        payload["contentDigest"] = snap.content_digest
        payload["persistedAt"] = snap.created_at
    return FirstPassBatchResponse(result=payload)


@router.get("/membership", response_model=ResearchMembershipResponse)
def get_research_membership(
    user_id: str = Depends(get_effective_user_id),
) -> ResearchMembershipResponse:
    try:
        membership = research_membership_store.get(user_id).as_dict()
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": exc.operation,
                "message": str(exc),
            },
        ) from exc
    return ResearchMembershipResponse(membership=membership)


@router.put("/membership", response_model=ResearchMembershipResponse)
def put_research_membership(
    body: ResearchMembershipSaveRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ResearchMembershipResponse:
    """Atomic version-checked membership / compute-subset save (#336). Fail closed."""
    try:
        snapshot = research_membership_store.save(
            user_id,
            expected_version=body.expectedVersion,
            theme_keys=body.themeKeys,
            disabled_theme_keys=body.disabledThemeKeys,
            sector_additions=body.sectorAdditions,
            sector_removals=body.sectorRemovals,
            custom_sectors=body.customSectors,
            extra_stock_ids=body.extraStockIds,
            compute_subset_ids=body.computeSubsetIds,
            reset_to_starter=body.resetToStarter,
        )
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": exc.operation,
                "message": str(exc),
            },
        ) from exc
    except ResearchMembershipConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "currentVersion": exc.current_version,
                "expectedVersion": exc.expected_version,
            },
        ) from exc
    return ResearchMembershipResponse(membership=snapshot.as_dict())


@router.post("/membership/preview", response_model=ResearchMembershipResponse)
def preview_research_membership(
    body: ResearchMembershipPreviewRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ResearchMembershipResponse:
    """Preview membership edits without persisting (#336)."""
    try:
        snapshot = research_membership_store.preview(
            user_id,
            theme_keys=body.themeKeys,
            disabled_theme_keys=body.disabledThemeKeys,
            sector_additions=body.sectorAdditions,
            sector_removals=body.sectorRemovals,
            custom_sectors=body.customSectors,
            extra_stock_ids=body.extraStockIds,
            compute_subset_ids=body.computeSubsetIds,
        )
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": exc.operation,
                "message": str(exc),
            },
        ) from exc
    return ResearchMembershipResponse(membership=snapshot.as_dict())


@router.get("/first-pass/jobs", response_model=FirstPassJobListResponse)
def list_first_pass_jobs(
    limit: int = Query(default=20, ge=1, le=50),
    include_archived: bool = Query(default=False, alias="includeArchived"),
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobListResponse:
    """Ownership-scoped recent first-pass jobs for UI restore after refresh (#335/#355)."""
    try:
        jobs = first_pass_job_service.list_jobs(
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
        )
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return FirstPassJobListResponse(jobs=[_camel_job(job) for job in jobs])


@router.post("/first-pass/jobs", response_model=FirstPassJobResponse)
def create_first_pass_job(
    body: FirstPassJobCreateRequest,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobResponse:
    """Start a resumable full-universe first-pass job (#335)."""
    try:
        job = first_pass_job_service.create_job(
            user_id=user_id,
            stock_ids=body.stockIds or None,
            use_membership=body.useMembership,
        )
    except FirstPassJobError as exc:
        code = 503 if "unavailable" in str(exc) else 400
        if str(exc) == "too_many_active_jobs":
            code = 409
        raise HTTPException(
            status_code=code,
            detail={"error": str(exc), "operation": exc.operation, "reason": str(exc)},
        ) from exc
    return FirstPassJobResponse(job=_camel_job(job))


@router.post("/first-pass/jobs/{job_id}/batches", response_model=FirstPassJobResponse)
def run_first_pass_job_batch(
    job_id: str,
    body: FirstPassJobBatchRequest,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobResponse:
    """Continue/resume a first-pass job with a bounded leased batch (#335)."""
    try:
        result = first_pass_job_service.run_batch(
            job_id,
            user_id=user_id,
            batch_size=body.batchSize,
            include_nonofficial_margins=body.includeNonofficialMargins,
            time_budget_seconds=body.timeBudgetSeconds,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    except FirstPassDurableUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "operation": exc.operation,
                "message": str(exc),
            },
        ) from exc
    if result.get("jobFailed"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "first_pass_job_failed",
                "lastError": result.get("lastError"),
            },
        )
    payload = _camel_job(result["job"])
    payload["batchIds"] = result["batchIds"]
    payload["processed"] = result["processed"]
    payload["leaseHeld"] = result["leaseHeld"]
    payload["alreadyComplete"] = result["alreadyComplete"]
    payload["waitingForRetry"] = bool(result.get("waitingForRetry"))
    payload["durationMs"] = result.get("durationMs", 0)
    payload["budgetStopped"] = bool(result.get("budgetStopped"))
    payload["releasedUnstarted"] = int(result.get("releasedUnstarted") or 0)
    payload["batchCounts"] = result.get("batchCounts") or {
        "ok": 0,
        "partial": 0,
        "failed": 0,
    }
    payload["leaseLost"] = bool(result.get("leaseLost"))
    return FirstPassJobResponse(job=payload)


@router.post("/first-pass/jobs/{job_id}/cancel", response_model=FirstPassJobResponse)
def cancel_first_pass_job(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobResponse:
    """Cancel a first-pass job and fence active leases (#355). Idempotent."""
    try:
        job = first_pass_job_service.cancel_job(job_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — map lock contention only
        if _is_pg_lock_contention(exc):
            raise _http_concurrent_update_retry(exc) from exc
        raise
    return FirstPassJobResponse(job=_camel_job(job))


@router.post("/first-pass/jobs/{job_id}/archive", response_model=FirstPassJobResponse)
def archive_first_pass_job(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobResponse:
    """Soft-hide a job from the default list (#355). Idempotent."""
    try:
        job = first_pass_job_service.archive_job(job_id, user_id=user_id, archived=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        status = 409 if "lease_active" in str(exc) else 503
        raise HTTPException(
            status_code=status,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return FirstPassJobResponse(job=_camel_job(job))


@router.post("/first-pass/jobs/{job_id}/unarchive", response_model=FirstPassJobResponse)
def unarchive_first_pass_job(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobResponse:
    """Restore an archived job to the default list (#355). Idempotent."""
    try:
        job = first_pass_job_service.archive_job(job_id, user_id=user_id, archived=False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return FirstPassJobResponse(job=_camel_job(job))


@router.get("/first-pass/jobs/{job_id}/deletion-preview")
def first_pass_job_deletion_preview(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> dict[str, Any]:
    """Read-only dependency inventory before hard delete (#355)."""
    try:
        preview = first_pass_job_service.deletion_preview(job_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return {
        "jobId": preview.get("job_id"),
        "category": preview.get("category"),
        "closure": preview.get("closure"),
        "retained": preview.get("retained"),
        "blockers": preview.get("blockers"),
        "requiresFullRunDelete": preview.get("requires_full_run_delete"),
        "previewToken": preview.get("preview_token"),
        "status": preview.get("status"),
        "leaseActive": preview.get("lease_active"),
        "deletionState": preview.get("deletion_state"),
        "archivedAt": preview.get("archived_at"),
        "symbolCount": preview.get("symbol_count"),
        "runOwned": preview.get("run_owned"),
        "activeSnapshotId": preview.get("active_snapshot_id"),
    }


@router.delete("/first-pass/jobs/{job_id}")
def delete_first_pass_job(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
    x_deletion_preview_token: str | None = Header(
        default=None, alias="X-Deletion-Preview-Token"
    ),
) -> Response:
    """Hard-delete an eligible job, or request deletion for a running job (#355)."""
    try:
        result = first_pass_job_service.delete_job(
            job_id,
            user_id=user_id,
            preview_token=x_deletion_preview_token or "",
        )
    except FirstPassJobError as exc:
        code = 400 if "preview_token" in str(exc) else 503
        raise HTTPException(
            status_code=code,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — map lock contention only
        if _is_pg_lock_contention(exc):
            raise _http_concurrent_update_retry(exc) from exc
        raise
    outcome = str(result.get("outcome") or "")
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="first_pass_job_not_found")
    if outcome == "conflict":
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(result.get("code") or "deletion_conflict"),
                "preview": result.get("preview"),
                "job": (
                    _camel_job(result["job"])
                    if isinstance(result.get("job"), dict)
                    else None
                ),
            },
        )
    if outcome == "requested":
        return JSONResponse(
            status_code=202,
            content={
                "deletionState": "requested",
                "job": _camel_job(result["job"])
                if isinstance(result.get("job"), dict)
                else None,
                "quiesceAfter": result.get("quiesce_after"),
                "auditId": result.get("audit_id"),
            },
        )
    # deleted
    return JSONResponse(
        status_code=200,
        content={
            "deleted": True,
            "idempotent": bool(result.get("idempotent")),
            "counts": result.get("counts") or {},
            "auditId": result.get("audit_id"),
        },
    )


@router.get("/first-pass/jobs/{job_id}/deletion")
def first_pass_job_deletion_status(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> Response:
    """Deletion lifecycle status; 410 + tombstone after hard delete (#355)."""
    try:
        status = first_pass_job_service.deletion_status(job_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    payload = {
        "jobId": status.get("job_id"),
        "gone": bool(status.get("gone")),
        "deletionState": status.get("deletion_state"),
        "deletionError": status.get("deletion_error"),
        "status": status.get("status"),
        "outcome": status.get("outcome"),
        "counts": status.get("counts"),
        "completedAt": status.get("completed_at"),
        "auditId": status.get("audit_id"),
    }
    if status.get("gone"):
        return JSONResponse(status_code=410, content=payload)
    return JSONResponse(status_code=200, content=payload)


@router.get("/first-pass/jobs/{job_id}", response_model=FirstPassJobResponse)
def get_first_pass_job(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobResponse:
    try:
        job = first_pass_job_service.get_job(job_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return FirstPassJobResponse(job=_camel_job(job))


@router.get(
    "/first-pass/jobs/{job_id}/progress",
    response_model=FirstPassJobProgressResponse,
)
def get_first_pass_job_progress(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> FirstPassJobProgressResponse:
    try:
        progress = first_pass_job_service.progress(job_id, user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return FirstPassJobProgressResponse(progress=progress)


@router.get(
    "/first-pass/jobs/{job_id}/symbols",
    response_model=FirstPassJobSymbolsResponse,
)
def get_first_pass_job_symbols(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    completeness: str | None = Query(default=None),
    stockIds: str | None = Query(
        default=None,
        description="Optional comma-separated stock ids (max 50); filter in SQL.",
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> FirstPassJobSymbolsResponse:
    stock_ids = _parse_stock_ids_query(stockIds, max_ids=50)
    # Present but empty (whitespace/commas only) → empty page, not unfiltered.
    if stockIds is not None and not stock_ids:
        try:
            first_pass_job_service.get_job(job_id, user_id=user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FirstPassJobError as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": str(exc), "operation": exc.operation},
            ) from exc
        return FirstPassJobSymbolsResponse(
            result={
                "jobId": job_id,
                "offset": offset,
                "limit": limit,
                "total": 0,
                "statusFilter": status,
                "q": q,
                "completenessFilter": completeness,
                "stockIdsFilter": [],
                "records": [],
            }
        )
    try:
        result = first_pass_job_service.list_symbols(
            job_id,
            user_id=user_id,
            status=status,
            q=q,
            completeness=completeness,
            stock_ids=stock_ids or None,
            offset=offset,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return FirstPassJobSymbolsResponse(result=result)


@router.get("/first-pass/snapshots/{snapshot_id}")
def get_first_pass_snapshot(
    snapshot_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> dict[str, object]:
    """Ownership-scoped immutable first-pass snapshot metadata + digests (#335/#336)."""
    if not tw_research_repository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "durable_store_unavailable"},
        )
    snap = tw_research_repository.get_first_pass_snapshot(snapshot_id, user_id=user_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="first_pass_snapshot_not_found")
    records = list((snap.get("payload") or {}).get("records") or [])
    return {
        "snapshotId": snap["snapshot_id"],
        "contentDigest": snap["content_digest"],
        "universeVersion": snap["universe_version"],
        "firstPassVersion": snap["first_pass_version"],
        "stockIds": list(snap["stock_ids"]),
        "recordCount": len(records),
        "createdAt": snap["created_at"],
        "notNotionReportList": True,
        "notes": [
            "Complete frozen-universe evidence for analyst review.",
            "Missing fundamentals remain visible with provenance.",
        ],
    }


@router.get("/first-pass/snapshots/{snapshot_id}/manifest")
def get_first_pass_snapshot_manifest(
    snapshot_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> dict[str, object]:
    if not tw_research_repository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "durable_store_unavailable"},
        )
    snap = tw_research_repository.get_first_pass_snapshot(snapshot_id, user_id=user_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="first_pass_snapshot_not_found")
    from app.core.endpoint_timing import endpoint_timing
    from app.services.tw_research.first_pass_chunks import build_first_pass_manifest

    payload = snap.get("payload") or {}
    records = list(payload.get("records") or [])
    with endpoint_timing(
        endpoint="GET /api/tw-research/first-pass/snapshots/{id}/manifest",
        snapshot_id=snap["snapshot_id"],
        record_count=len(records),
        calendar_preload_attempted=False,
    ) as timing:
        result = build_first_pass_manifest(
            snapshot_id=snap["snapshot_id"],
            content_digest=snap["content_digest"],
            universe_version=snap.get("universe_version"),
            first_pass_version=snap.get("first_pass_version"),
            records=records,
            payload=payload if isinstance(payload, dict) else {},
        )
        timing["rows_returned"] = int(result.get("chunkCount") or 0)
        timing["result"] = result
        return result


@router.get("/first-pass/snapshots/{snapshot_id}/chunk")
def get_first_pass_snapshot_chunk(
    snapshot_id: str,
    user_id: str = Depends(get_effective_user_id),
    chunkIndex: int = Query(default=0, ge=0),
) -> dict[str, object]:
    if not tw_research_repository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "durable_store_unavailable"},
        )
    snap = tw_research_repository.get_first_pass_snapshot(snapshot_id, user_id=user_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="first_pass_snapshot_not_found")
    from app.core.endpoint_timing import endpoint_timing
    from app.services.tw_research.first_pass_chunks import build_first_pass_chunk

    payload = snap.get("payload") or {}
    records = list(payload.get("records") or [])
    with endpoint_timing(
        endpoint="GET /api/tw-research/first-pass/snapshots/{id}/chunk",
        snapshot_id=snap["snapshot_id"],
        record_count=len(records),
        calendar_preload_attempted=False,
    ) as timing:
        try:
            result = build_first_pass_chunk(
                snapshot_id=snap["snapshot_id"],
                content_digest=snap["content_digest"],
                records=records,
                chunk_index=chunkIndex,
                payload=payload if isinstance(payload, dict) else {},
            )
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="chunk_out_of_range") from exc
        timing["rows_returned"] = len(result.get("records") or [])
        timing["result"] = result
        return result


@router.get("/first-pass/snapshots/{snapshot_id}/chip-evidence")
def get_first_pass_chip_evidence(
    snapshot_id: str,
    user_id: str = Depends(get_effective_user_id),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=50),
    q: str | None = Query(default=None),
    stockIds: str | None = Query(
        default=None,
        description="Optional comma-separated stock ids (max 50); filter after ordering.",
    ),
) -> dict[str, object]:
    """Paginated chip summaries from a frozen first-pass snapshot (#346/#347)."""
    if not tw_research_repository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "durable_store_unavailable"},
        )
    snap = tw_research_repository.get_first_pass_snapshot(snapshot_id, user_id=user_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="first_pass_snapshot_not_found")
    from app.core.endpoint_timing import endpoint_timing
    from app.services.tw_calendar_preload import ensure_tw_calendars_bounded
    from app.services.tw_research.chip_evidence import (
        format_chip_evidence,
        reference_day_from_snapshot,
        resolve_chip_capture_at,
    )
    from app.services.tw_research.first_pass_chunks import ordered_first_pass_records

    payload = snap.get("payload") or {}
    records = ordered_first_pass_records(list(payload.get("records") or []))
    query = (q or "").strip().upper()
    if query:
        filtered = []
        for row in records:
            stock_id = str(row.get("stockId") or "").upper()
            name = str(row.get("stockName") or "")
            if query in stock_id or query in name.upper():
                filtered.append(row)
        records = filtered
    # Filter by explicit stockIds after ordering (#346/#347).
    requested_ids: list[str] = []
    if stockIds:
        requested_ids = [
            item.strip()
            for item in str(stockIds).split(",")
            if item and item.strip()
        ][:50]
        if requested_ids:
            wanted = {item.upper() for item in requested_ids}
            records = [
                row
                for row in records
                if str(row.get("stockId") or row.get("stock_id") or "").upper() in wanted
            ]
    page = records[offset : offset + limit]
    # created_at → payload tradingDay@23:59 (no request tradingDay on this route).
    capture_at, capture_date_only = resolve_chip_capture_at(
        created_at=snap.get("created_at"),
        payload=payload if isinstance(payload, dict) else None,
    )
    reference_day = reference_day_from_snapshot(
        trading_day=payload.get("tradingDay") or payload.get("trading_day"),
        payload=payload,
        created_at=snap.get("created_at"),
    )
    if reference_day is None and capture_at is not None:
        reference_day = capture_at.date().isoformat()
    calendar_known: bool | None = None
    calendar_preload_attempted = False
    if reference_day:
        try:
            year = int(str(reference_day)[:4])
        except ValueError:
            year = None
        if year is not None:
            calendar_preload_attempted = True
            calendar_known = ensure_tw_calendars_bounded([year, year - 1])
    with endpoint_timing(
        endpoint="GET /api/tw-research/first-pass/snapshots/{id}/chip-evidence",
        snapshot_id=snap["snapshot_id"],
        record_count=len(records),
        calendar_preload_attempted=calendar_preload_attempted,
    ) as timing:
        result = {
            "snapshotId": snap["snapshot_id"],
            "contentDigest": snap["content_digest"],
            "offset": offset,
            "limit": limit,
            "total": len(records),
            "stockIdsFilter": requested_ids or None,
            "referenceDay": reference_day,
            "rows": [
                format_chip_evidence(
                    row,
                    reference_day=reference_day,
                    calendar_known=calendar_known,
                    capture_at=capture_at,
                    date_only=capture_date_only,
                )
                for row in page
            ],
            "notBuySignal": True,
            "notNotionReportList": True,
        }
        timing["rows_returned"] = len(result["rows"])
        timing["result"] = result
        return result


@router.post("/candidate-sets", response_model=CandidateSetResponse)
def post_candidate_set(
    body: CandidateSetSubmitRequest,
    user_id: str = Depends(get_effective_user_id),
) -> CandidateSetResponse:
    """Persist analyst/manual compute subset pinned to a first-pass digest (#336).

    Managed research runs use one PostgreSQL transaction for gate validation,
    candidate-set version append, and research-run approval (no TOCTOU gap).
    Legacy standalone flows (no research run) keep ``submit_candidate_set``.
    """
    try:
        managed_run = None
        try:
            managed_run = research_run_store.get(user_id)
        except ResearchRunStoreUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "research_run_store_unavailable",
                    "retryable": True,
                    "message": str(exc),
                },
            ) from exc

        if managed_run is not None and research_run_store.uses_durable_backend():
            try:
                saved = submit_candidate_set_for_research_run(
                    user_id=user_id,
                    first_pass_snapshot_id=body.firstPassSnapshotId,
                    first_pass_digest=body.firstPassDigest,
                    stock_ids=body.stockIds,
                    origin=body.origin,
                    expected_version=body.expectedVersion,
                    selection_reasons=body.selectionReasons,
                    daily_override_ids=body.dailyOverrideIds,
                    decision_date=body.decisionDate,
                    notes=body.notes,
                )
            except ResearchRunGateError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": str(exc)},
                ) from exc
            except KeyError as exc:
                # Run vanished between existence check and FOR UPDATE.
                raise HTTPException(
                    status_code=409,
                    detail={"error": "research_run_not_found_during_attach"},
                ) from exc
        elif managed_run is not None:
            # In-memory unit-test ledger only — production always uses the durable path.
            try:
                research_run_store.validate_candidate_attach(
                    user_id,
                    first_pass_snapshot_id=str(body.firstPassSnapshotId or ""),
                )
                saved = submit_candidate_set(
                    user_id=user_id,
                    first_pass_snapshot_id=body.firstPassSnapshotId,
                    first_pass_digest=body.firstPassDigest,
                    stock_ids=body.stockIds,
                    origin=body.origin,
                    expected_version=body.expectedVersion,
                    selection_reasons=body.selectionReasons,
                    daily_override_ids=body.dailyOverrideIds,
                    decision_date=body.decisionDate,
                    notes=body.notes,
                )
                research_run_store.attach_candidate_set(
                    user_id,
                    candidate_set_id=str(saved["candidateSetId"]),
                    candidate_set_version=int(saved["version"]),
                    first_pass_snapshot_id=str(saved.get("firstPassSnapshotId") or ""),
                )
            except ResearchRunGateError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": str(exc)},
                ) from exc
            except ResearchRunConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "research_run_revision_conflict",
                        "reason": exc.reason,
                    },
                ) from exc
            except KeyError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "research_run_not_found_during_attach"},
                ) from exc
        else:
            saved = submit_candidate_set(
                user_id=user_id,
                first_pass_snapshot_id=body.firstPassSnapshotId,
                first_pass_digest=body.firstPassDigest,
                stock_ids=body.stockIds,
                origin=body.origin,
                expected_version=body.expectedVersion,
                selection_reasons=body.selectionReasons,
                daily_override_ids=body.dailyOverrideIds,
                decision_date=body.decisionDate,
                notes=body.notes,
            )
        saved.pop("approvalOutcome", None)
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": getattr(exc, "operation", "candidate_set"),
                "message": str(exc),
            },
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CandidateSetConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "currentVersion": exc.current_version,
                "expectedVersion": exc.expected_version,
            },
        ) from exc
    except CandidateSetValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CandidateSetResponse(candidateSet=saved)


@router.get("/candidate-sets/latest", response_model=CandidateSetResponse)
def get_latest_candidate_set(
    user_id: str = Depends(get_effective_user_id),
) -> CandidateSetResponse:
    try:
        saved = latest_candidate_set(user_id=user_id)
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": getattr(exc, "operation", "candidate_set"),
                "message": str(exc),
            },
        ) from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="candidate_set_not_found")
    return CandidateSetResponse(candidateSet=saved)


@router.get(
    "/candidate-sets/{candidate_set_id}",
    response_model=CandidateSetResponse,
)
def get_candidate_set_version(
    candidate_set_id: str,
    version: int = Query(..., ge=0),
    user_id: str = Depends(get_effective_user_id),
) -> CandidateSetResponse:
    """Load an exact candidate-set version for historical scan watchlists."""
    from app.services.tw_research.first_pass_durable import get_candidate_set

    try:
        saved = get_candidate_set(
            user_id=user_id,
            candidate_set_id=candidate_set_id,
            version=version,
        )
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": getattr(exc, "operation", "candidate_set"),
                "message": str(exc),
            },
        ) from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="candidate_set_not_found")
    return CandidateSetResponse(candidateSet=saved)


@router.post("/candidate-sets/technical-scan", response_model=ScanSnapshotResponse)
def post_candidate_technical_scan(
    body: CandidateTechnicalScanRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ScanSnapshotResponse:
    """Create a technical MACD/KD scan from the approved candidate set (#336).

    When a research run exists, Phase-2 association is fenced: claim → create/reuse
    scan → atomic attach. Standalone (no run) keeps legacy create behaviour.
    """
    try:
        run_before = None
        try:
            run_before = research_run_store.get(user_id)
        except ResearchRunStoreUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "research_run_store_unavailable",
                    "retryable": True,
                    "message": str(exc),
                },
            ) from exc

        claim_token: str | None = None
        if run_before is not None:
            if run_before.gates.get("candidateSelection") != "approved":
                raise HTTPException(
                    status_code=409,
                    detail={"error": "candidate_selection_gate_not_approved"},
                )
            # Pin candidate-set identity against the managed run when present.
            if (
                body.candidateSetId
                and run_before.candidate_set_id
                and str(body.candidateSetId) != str(run_before.candidate_set_id)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"error": "candidate_set_id_mismatch_with_research_run"},
                )
            if (
                body.expectedVersion is not None
                and run_before.candidate_set_version is not None
                and int(body.expectedVersion) != int(run_before.candidate_set_version)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"error": "candidate_set_version_mismatch_with_research_run"},
                )
            from uuid import uuid4

            claim_token = f"p2_{uuid4().hex[:20]}"
            claim = research_run_store.claim_phase2_scan_slot(
                user_id, claim_token=claim_token
            )
            outcome = str(claim.get("outcome") or "")
            if outcome == "gate_not_approved":
                raise HTTPException(
                    status_code=409,
                    detail={"error": "candidate_selection_gate_not_approved"},
                )
            if outcome == "already_attached":
                existing_id = claim.get("technical_scan_id")
                state = tw_research_scan_service.get_scan(str(existing_id or ""))
                if state is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "research_run_technical_scan_missing",
                            "technicalScanId": existing_id,
                        },
                    )
                if getattr(state, "user_id", None) not in {None, user_id}:
                    raise HTTPException(status_code=404, detail="scan_not_found")
                stored_overrides = _pinned_candidate_daily_overrides_for_reuse(
                    user_id,
                    body=body,
                    state=state,
                    run=run_before,
                )
                mismatches = _phase2_scan_request_mismatches(
                    state,
                    body,
                    candidate_set_daily_override_ids=stored_overrides,
                    require_pinned_identity=True,
                )
                if mismatches:
                    _raise_phase2_scan_mismatch(
                        scan_id=str(existing_id),
                        mismatches=mismatches,
                    )
                return _snapshot(state)
            if outcome == "claim_held":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "phase2_claim_held",
                        "retryable": True,
                        "message": "Another worker is associating the technical scan.",
                    },
                )
            pending_scan_id = claim.get("pending_technical_scan_id")
            if pending_scan_id:
                state = tw_research_scan_service.get_scan(str(pending_scan_id))
                if state is not None and getattr(state, "user_id", None) in {
                    None,
                    user_id,
                }:
                    stored_overrides = _pinned_candidate_daily_overrides_for_reuse(
                        user_id,
                        body=body,
                        state=state,
                        run=run_before,
                    )
                    mismatches = _phase2_scan_request_mismatches(
                        state,
                        body,
                        candidate_set_daily_override_ids=stored_overrides,
                        require_pinned_identity=True,
                    )
                    if mismatches:
                        _raise_phase2_scan_mismatch(
                            scan_id=str(pending_scan_id),
                            mismatches=mismatches,
                        )
                    attached = research_run_store.attach_technical_scan_under_claim(
                        user_id,
                        claim_token=claim_token,
                        scan_id=str(pending_scan_id),
                    )
                    if attached.get("outcome") in {"attached", "already_attached"}:
                        return _snapshot(state)
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "phase2_attach_failed",
                            "retryable": True,
                            "scanId": pending_scan_id,
                        },
                    )

        result = create_technical_scan_from_candidate_set(
            user_id=user_id,
            expected_version=body.expectedVersion,
            candidate_set_id=body.candidateSetId,
            target_count=body.targetCount,
            trading_day=body.tradingDay,
            max_upstream_month_fetches=body.maxUpstreamMonthFetches,
            max_enrichments_per_run=body.maxEnrichmentsPerRun,
            daily_override_ids=body.dailyOverrideIds,
            research_run_id=(
                getattr(run_before, "research_run_id", None) if run_before is not None else None
            ),
        )
        scan_state = result.get("scan")
        scan_id = getattr(scan_state, "scan_id", None) or (
            scan_state.get("scanId") if isinstance(scan_state, dict) else None
        )
        if scan_id and run_before is not None and claim_token is not None:
            try:
                research_run_store.record_pending_technical_scan(
                    user_id, claim_token=claim_token, scan_id=str(scan_id)
                )
                attached = research_run_store.attach_technical_scan_under_claim(
                    user_id,
                    claim_token=claim_token,
                    scan_id=str(scan_id),
                )
            except ResearchRunStoreUnavailable as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "research_run_store_unavailable",
                        "retryable": True,
                        "scanId": scan_id,
                        "message": str(exc),
                    },
                ) from exc
            outcome = str(attached.get("outcome") or "")
            if outcome == "gate_not_approved":
                raise HTTPException(
                    status_code=409,
                    detail={"error": "candidate_selection_gate_not_approved"},
                )
            if outcome == "claim_lost":
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "phase2_claim_lost_after_scan_create",
                        "retryable": True,
                        "scanId": scan_id,
                    },
                )
            if outcome not in {"attached", "already_attached"}:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "phase2_attach_incomplete",
                        "retryable": True,
                        "scanId": scan_id,
                        "outcome": outcome,
                    },
                )
    except ResearchMembershipUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": getattr(exc, "operation", "candidate_set"),
                "message": str(exc),
            },
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CandidateSetConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "currentVersion": exc.current_version,
                "expectedVersion": exc.expected_version,
            },
        ) from exc
    except CandidateSetValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=getattr(exc, "detail", None) or {"error": str(exc)},
        ) from exc
    except TwResearchDurableStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "durable_store_unavailable",
                "retryable": True,
                "operation": getattr(exc, "operation", "create_scan"),
                "message": "Technical scan could not be persisted.",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _snapshot(result["scan"])


@router.post("/relative-price-basis", response_model=RelativePriceBasisResponse)
def post_relative_price_basis(
    body: RelativePriceBasisRequest,
    user_id: str = Depends(get_effective_user_id),
) -> RelativePriceBasisResponse:
    """Evaluate relative price-basis evidence from stored bars only (#337)."""
    del user_id
    stock = quote_service.get_catalog_stock(body.stockId)
    if stock is None:
        raise HTTPException(status_code=404, detail="stock_not_in_catalog")
    candles, _missing = daily_price_history_service.peek_daily_history(
        stock,
        "MAX",
        limit=PREFERRED_DAILY_HISTORY_LIMIT,
    )
    evidence = evaluate_relative_price_basis(
        list(candles),
        market="TW",
        official_pe=body.officialPe,
    )
    return RelativePriceBasisResponse(evidence=evidence.as_dict())


def _research_run_unavailable(exc: ResearchRunStoreUnavailable) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "research_run_store_unavailable",
            "retryable": True,
            "message": str(exc),
        },
    )


def _maybe_rearm_first_pass_on_start(user_id: str) -> ResearchRunResponse:
    """Re-arm drain on Start; concurrent armed run returns 200 instead of 409."""
    armed_before = False
    try:
        before = research_run_store.get(user_id)
        armed_before = bool(before.schedule_enabled) if before is not None else False
        run = research_run_store.maybe_rearm_first_pass_drain_on_start(user_id)
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResearchRunConflict as exc:
        try:
            current = research_run_store.get(user_id)
        except ResearchRunStoreUnavailable:
            current = None
        if current is not None and bool(current.schedule_enabled):
            return _research_run_response(current, start_outcome="job_resumed")
        raise HTTPException(
            status_code=409,
            detail={
                "error": "research_run_revision_conflict",
                "reason": exc.reason,
            },
        ) from exc
    outcome = (
        "drain_rearmed"
        if (not armed_before and bool(run.schedule_enabled))
        else "job_resumed"
    )
    return _research_run_response(run, start_outcome=outcome)


def _create_first_pass_job_for_start(*, user_id: str, job_id: str) -> str:
    try:
        job = first_pass_job_service.create_job(
            user_id=user_id,
            use_membership=True,
            job_id=job_id,
        )
        return str(job.get("job_id") or job.get("jobId") or job_id)
    except FirstPassJobError as exc:
        if "unavailable" in str(exc):
            raise HTTPException(
                status_code=503,
                detail={"error": str(exc), "operation": exc.operation},
            ) from exc
        raise HTTPException(
            status_code=400,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc


def _start_outcome_from_durable_run(
    run: ResearchRun,
    *,
    created_job_id: str | None = None,
    attach_outcome: str | None = None,
) -> str:
    """Map confirmed PostgreSQL/memory run state to Start outcome (#361/#362).

    Never report ``job_created`` unless this request's job id is the durable
    authoritative ``first_pass_job_id``. Claim races without an attached job are
    ``creation_in_progress``, not resumed.
    """
    authoritative = str(run.first_pass_job_id or "") or None
    if authoritative and created_job_id and authoritative == created_job_id:
        if attach_outcome in {None, "attached"}:
            return "job_created"
        return "job_resumed"
    if authoritative:
        return "job_resumed"
    if run.job_claim_token or run.pending_first_pass_job_id:
        return "creation_in_progress"
    return "creation_in_progress"


def _attach_created_first_pass_job(
    *,
    user_id: str,
    claim_token: str,
    job_id: str,
) -> ResearchRunResponse:
    try:
        attached = research_run_store.attach_first_pass_job_under_claim(
            user_id,
            claim_token=claim_token,
            job_id=job_id,
            notes=[f"Durable first-pass job {job_id} created with research run."],
        )
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    attach_outcome = str(attached.get("outcome") or "")
    if attach_outcome in {"already_attached", "claim_lost"}:
        orphan = attached.get("orphan_job_id")
        if orphan:
            research_run_store.mark_orphaned_first_pass_job(
                str(orphan),
                user_id=user_id,
                authoritative_job_id=str(attached.get("first_pass_job_id") or "")
                or None,
                reason=attach_outcome,
            )
    run = ResearchRun.from_persist_payload(attached["run"])
    # Another concurrent Start may already own the authoritative job. Re-arm
    # only when durable state already has a First-Pass Job.
    if attach_outcome in {"already_attached", "claim_lost"} and run.first_pass_job_id:
        return _maybe_rearm_first_pass_on_start(user_id)
    start_outcome = _start_outcome_from_durable_run(
        run,
        created_job_id=job_id,
        attach_outcome=attach_outcome,
    )
    return _research_run_response(run, start_outcome=start_outcome)


def _resume_existing_phase1_run(*, user_id: str, run: ResearchRun) -> ResearchRunResponse | None:
    """Return a response when the run already has work attached; else None."""
    if not (
        run.first_pass_job_id
        or (run.phase == "phase2_technical" and run.technical_scan_id)
    ):
        return None
    if run.first_pass_job_id and run.phase == "phase1_first_pass":
        return _maybe_rearm_first_pass_on_start(user_id)
    return _research_run_response(run, start_outcome="job_resumed")


def _claim_and_create_phase1_job(*, user_id: str) -> ResearchRunResponse:
    from uuid import uuid4

    claim_token = f"claim_{uuid4().hex[:20]}"
    try:
        claim = research_run_store.claim_first_pass_job_slot(
            user_id, claim_token=claim_token
        )
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    outcome = str(claim.get("outcome") or "")
    if outcome == "already_attached":
        return _maybe_rearm_first_pass_on_start(user_id)
    if outcome == "claim_held":
        held = ResearchRun.from_persist_payload(claim["run"])
        # Another request holds the create lease — do not claim job_resumed
        # until first_pass_job_id is durable.
        if held.first_pass_job_id:
            return _maybe_rearm_first_pass_on_start(user_id)
        return _research_run_response(held, start_outcome="creation_in_progress")

    pending_job_id = claim.get("pending_first_pass_job_id")
    if not pending_job_id:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "research_run_job_id_not_allocated",
                "retryable": True,
            },
        )
    job_id = _create_first_pass_job_for_start(
        user_id=user_id, job_id=str(pending_job_id)
    )
    return _attach_created_first_pass_job(
        user_id=user_id, claim_token=claim_token, job_id=job_id
    )


def _start_phase1_research_run(
    *,
    user_id: str,
    trading_day: str | None,
    notes: list[str],
) -> ResearchRunResponse:
    """Create or resume the managed Phase-1 run + First-Pass job (#366 re-arm)."""
    try:
        run = research_run_store.begin_phase1(
            user_id,
            trading_day=trading_day,
            notes=notes,
        )
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    except ResearchRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "research_run_revision_conflict", "reason": exc.reason},
        ) from exc

    resumed = _resume_existing_phase1_run(user_id=user_id, run=run)
    if resumed is not None:
        return resumed
    return _claim_and_create_phase1_job(user_id=user_id)


def _research_run_response(
    run: ResearchRun,
    *,
    start_outcome: str | None = None,
) -> ResearchRunResponse:
    """Attach honest execution readiness alongside the durable run (#359/#361)."""
    from app.services.tw_research.execution_status import (
        classify_first_pass_execution,
        classify_phase2_execution,
    )
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.scan import tw_research_scan_service

    job = None
    counts = None
    if run.first_pass_job_id and tw_research_repository.is_available():
        try:
            job = tw_research_repository.get_first_pass_job(
                run.first_pass_job_id, user_id=run.user_id
            )
            counts = tw_research_repository.count_first_pass_symbols(
                run.first_pass_job_id
            )
        except Exception:  # noqa: BLE001 — response must still return the run
            job = None
            counts = None
    scan_payload = None
    scan_state = None
    if run.technical_scan_id:
        try:
            state = tw_research_scan_service.get_scan(str(run.technical_scan_id))
            if state is not None and getattr(state, "user_id", None) in {
                None,
                run.user_id,
            }:
                scan_state = state
                scan_payload = state.snapshot()
                scan_payload["reserve_expires_at"] = getattr(
                    state, "reserve_expires_at", None
                )
                scan_payload["user_id"] = getattr(state, "user_id", None)
        except Exception:  # noqa: BLE001 — response must still return the run
            scan_payload = None
            scan_state = None
    return ResearchRunResponse(
        researchRun=run.as_dict(),
        execution=classify_first_pass_execution(
            run, job=job, symbol_counts=counts
        ),
        phase2Execution=classify_phase2_execution(
            run, scan=scan_payload, scan_state=scan_state
        ),
        startOutcome=start_outcome,
    )


def _phase2_effective_daily_overrides(
    *,
    candidate_set_daily_override_ids: list[str] | None,
    request_daily_override_ids: list[str] | None,
) -> list[str]:
    """Match create-path additive semantics: candidate-set ∪ request overrides."""
    return dedupe_stock_ids(
        [*(candidate_set_daily_override_ids or []), *(request_daily_override_ids or [])]
    )


def _pinned_candidate_identity_for_reuse(
    *,
    body: CandidateTechnicalScanRequest,
    state: ScanState,
    run: ResearchRun,
) -> tuple[str, int]:
    """Resolve exact candidate-set id/version from managed run + scan provenance.

    Omitted request fields inherit the pinned context. Explicit request fields and
    run/provenance pins must all agree. Never falls back to latest/unversioned sets.
    """
    provenance = dict(getattr(state, "candidate_provenance", None) or {})
    run_id = run.candidate_set_id
    run_version = run.candidate_set_version
    prov_id = provenance.get("candidateSetId")
    try:
        prov_version = (
            int(provenance["candidateSetVersion"])
            if provenance.get("candidateSetVersion") is not None
            else None
        )
    except (TypeError, ValueError):
        prov_version = None

    if not run_id or run_version is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pinned_candidate_set_missing_on_research_run",
                "candidateSetId": run_id,
                "candidateSetVersion": run_version,
            },
        )
    if not prov_id or prov_version is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pinned_candidate_set_missing_on_scan",
                "technicalScanId": getattr(state, "scan_id", None),
                "candidateSetId": prov_id,
                "candidateSetVersion": provenance.get("candidateSetVersion"),
            },
        )
    if str(run_id) != str(prov_id) or int(run_version) != int(prov_version):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "research_run_scan_candidate_set_mismatch",
                "researchRunCandidateSetId": run_id,
                "researchRunCandidateSetVersion": int(run_version),
                "scanCandidateSetId": prov_id,
                "scanCandidateSetVersion": int(prov_version),
            },
        )

    resolved_id = str(run_id)
    resolved_version = int(run_version)
    if body.candidateSetId and str(body.candidateSetId) != resolved_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_set_id_mismatch_with_research_run",
                "requestCandidateSetId": body.candidateSetId,
                "pinnedCandidateSetId": resolved_id,
            },
        )
    if body.expectedVersion is not None and int(body.expectedVersion) != resolved_version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_set_version_mismatch_with_research_run",
                "requestCandidateSetVersion": int(body.expectedVersion),
                "pinnedCandidateSetVersion": resolved_version,
            },
        )
    return resolved_id, resolved_version


def _pinned_candidate_daily_overrides_for_reuse(
    user_id: str,
    *,
    body: CandidateTechnicalScanRequest,
    state: ScanState,
    run: ResearchRun,
) -> list[str]:
    """Load exact pinned candidate-set overrides for managed Phase-2 reuse.

    Distinguishes a valid empty override list from missing/unreadable provenance.
    Never falls back to a newer or latest candidate set.
    """
    candidate_set_id, version = _pinned_candidate_identity_for_reuse(
        body=body, state=state, run=run
    )
    if not tw_research_repository.is_available():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "candidate_set_store_unavailable",
                "retryable": True,
                "candidateSetId": candidate_set_id,
                "candidateSetVersion": version,
            },
        )
    try:
        saved = tw_research_repository.get_candidate_set(
            user_id,
            candidate_set_id=candidate_set_id,
            version=version,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "candidate_set_store_error",
                "retryable": True,
                "candidateSetId": candidate_set_id,
                "candidateSetVersion": version,
                "message": str(exc.__class__.__name__),
            },
        ) from exc
    if saved is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pinned_candidate_set_not_found",
                "candidateSetId": candidate_set_id,
                "candidateSetVersion": version,
                "hint": (
                    "The exact pinned candidate-set version is required for Phase-2 "
                    "reuse; a newer latest set is not a substitute."
                ),
            },
        )
    # Empty list is a valid persisted override set — not the same as missing data.
    return list((saved.get("selection_reasons") or {}).get("dailyOverrideIds") or [])


def _phase2_scan_request_mismatches(
    state: ScanState,
    body: CandidateTechnicalScanRequest,
    *,
    candidate_set_daily_override_ids: list[str] | None = None,
    require_pinned_identity: bool = False,
) -> list[str]:
    """Return semantic mismatch fields when reusing an attached technical scan.

    Daily overrides use the same additive contract as
    ``create_technical_scan_from_candidate_set``:
    effective = persisted candidate-set overrides ∪ request overrides.
    The attached scan's provenance stores that effective set; compare identity,
    not raw request ⊆ attached.

    When ``require_pinned_identity`` is true (managed research-run reuse), omitted
    request id/version/day inherit the attached pinned context and approved
    membership is always checked.
    """
    provenance = dict(getattr(state, "candidate_provenance", None) or {})
    mismatches: list[str] = []
    if body.candidateSetId and str(provenance.get("candidateSetId") or "") != str(
        body.candidateSetId
    ):
        mismatches.append("candidateSetId")
    if body.expectedVersion is not None:
        try:
            attached_version = int(provenance.get("candidateSetVersion"))
        except (TypeError, ValueError):
            attached_version = -1
        if attached_version != int(body.expectedVersion):
            mismatches.append("candidateSetVersion")
    if body.tradingDay and str(getattr(state, "trading_day", "") or "") != str(
        body.tradingDay
    ):
        mismatches.append("tradingDay")
    attached_calc = str(
        getattr(state, "calculation_version", "")
        or provenance.get("calculationVersion")
        or ""
    )
    if attached_calc and attached_calc != CALCULATION_VERSION_MONTHLY_FIRST:
        mismatches.append("calculationVersion")
    effective_overrides = sorted(
        _phase2_effective_daily_overrides(
            candidate_set_daily_override_ids=candidate_set_daily_override_ids,
            request_daily_override_ids=body.dailyOverrideIds,
        )
    )
    attached_overrides = sorted(
        dedupe_stock_ids(
            [str(item) for item in (provenance.get("dailyOverrideIds") or []) if item]
        )
    )
    if effective_overrides != attached_overrides:
        mismatches.append("dailyOverrideIds")
    if require_pinned_identity or body.candidateSetId or body.expectedVersion is not None:
        approved = {
            str(item).upper()
            for item in (provenance.get("approvedStockIds") or list(state.stock_ids))
            if item
        }
        attached_ids = {str(item).upper() for item in state.stock_ids if item}
        if approved and attached_ids != approved:
            mismatches.append("approvedStockIds")
    return mismatches


def _raise_phase2_scan_mismatch(
    *,
    scan_id: str,
    mismatches: list[str],
) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "phase2_scan_request_mismatch",
            "technicalScanId": scan_id,
            "mismatches": mismatches,
            "hint": (
                "Retry with identical candidate-set/trading-day/overrides, or start a "
                "new research run after the current run reaches a terminal/gated phase. "
                "Existing immutable scans are preserved."
            ),
        },
    )


@router.get("/research-runs/current", response_model=ResearchRunResponse)
def get_current_research_run(
    user_id: str = Depends(get_effective_user_id),
) -> ResearchRunResponse:
    """Manual orchestration status with explicit analyst gates (#341)."""
    try:
        run = research_run_store.reconcile(user_id)
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    except ResearchRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "research_run_revision_conflict", "reason": exc.reason},
        ) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="research_run_not_found")
    return _research_run_response(run)


@router.get(
    "/research-runs/technical-results",
    response_model=TechnicalResultsResponse,
)
def get_technical_results(
    scan_id: str | None = Query(
        None,
        alias="scanId",
        description="Optional owned scan id for historical read-only results.",
    ),
    user_id: str = Depends(get_effective_user_id),
) -> TechnicalResultsResponse:
    """Stage-4 read model: persisted MACD/KD rows for an owned technical scan (#361).

    Read-only: uses ``research_run_store.get`` (no reconcile persist / revision bump).
    Never recomputes indicators or calls providers. Never attaches a historical
    scan to the active research run.

    - Without ``scanId``: current run's ``technical_scan_id``.
    - With ``scanId``: that scan when owned by the caller (historical view).
    """
    from app.services.tw_research.execution_status import classify_phase2_execution
    from app.services.tw_research.scan import tw_research_scan_service
    from app.services.tw_research.technical_results import build_technical_results_payload

    try:
        run = research_run_store.get(user_id)
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc

    requested = str(scan_id or "").strip() or None
    current_scan_id = (
        str(run.technical_scan_id or "").strip() if run is not None else ""
    ) or None

    if requested:
        resolved_scan_id = requested
    else:
        if run is None:
            raise HTTPException(status_code=404, detail="research_run_not_found")
        if not current_scan_id:
            raise HTTPException(status_code=404, detail="technical_scan_not_found")
        resolved_scan_id = current_scan_id

    try:
        state = tw_research_scan_service.get_scan(resolved_scan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="technical_scan_not_found") from exc
    except Exception as exc:  # noqa: BLE001 — unexpected lookup failures stay 503
        raise HTTPException(
            status_code=503,
            detail={"error": "technical_scan_lookup_failed", "reason": type(exc).__name__},
        ) from exc
    if state is None:
        raise HTTPException(status_code=404, detail="technical_scan_not_found")
    # Strict ownership: ownerless and foreign scans are not readable. Use the same
    # 404 as missing so scan existence is not leaked across users.
    owner = getattr(state, "user_id", None)
    owner_text = str(owner).strip() if owner is not None else ""
    if not owner_text or owner_text != str(user_id):
        raise HTTPException(status_code=404, detail="technical_scan_not_found")

    is_current = bool(current_scan_id) and resolved_scan_id == current_scan_id
    viewing_historical = bool(requested) and not is_current

    phase2 = None
    if run is not None and is_current:
        scan_payload = state.snapshot()
        scan_payload["reserve_expires_at"] = getattr(state, "reserve_expires_at", None)
        scan_payload["user_id"] = getattr(state, "user_id", None)
        phase2 = classify_phase2_execution(run, scan=scan_payload, scan_state=state)

    payload = build_technical_results_payload(
        run=run,
        state=state,
        phase2_execution=phase2,
        viewing_historical=viewing_historical,
        is_current_run_scan=is_current,
    )
    return TechnicalResultsResponse.model_validate(payload)


@router.get("/research-runs/current/deletion-preview")
def research_run_deletion_preview(
    user_id: str = Depends(get_effective_user_id),
) -> dict[str, Any]:
    """Read-only closure inventory before deleting the current research run (#355)."""
    try:
        preview = first_pass_job_service.research_run_deletion_preview(user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    return {
        "researchRunId": preview.get("research_run_id"),
        "category": preview.get("category"),
        "closure": preview.get("closure"),
        "retained": preview.get("retained"),
        "blockers": preview.get("blockers"),
        "requiresFullRunDelete": True,
        "previewToken": preview.get("preview_token"),
        "jobIds": preview.get("job_ids"),
        "needsQuiesce": preview.get("needs_quiesce"),
        "deletionRequested": preview.get("deletion_requested"),
    }


@router.get("/research-runs/current/deletion")
def research_run_deletion_status(
    user_id: str = Depends(get_effective_user_id),
) -> Response:
    """Full-run deletion lifecycle status; 410 + tombstone after hard delete (#355)."""
    try:
        status = first_pass_job_service.research_run_deletion_status(user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FirstPassJobError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    payload = {
        "researchRunId": status.get("research_run_id"),
        "gone": bool(status.get("gone")),
        "deletionState": status.get("deletion_state"),
        "deletionError": status.get("deletion_error"),
        "outcome": status.get("outcome"),
        "counts": status.get("counts"),
        "completedAt": status.get("completed_at"),
        "auditId": status.get("audit_id"),
    }
    if status.get("gone"):
        return JSONResponse(status_code=410, content=payload)
    return JSONResponse(status_code=200, content=payload)


@router.delete("/research-runs/current")
def delete_current_research_run(
    user_id: str = Depends(get_effective_user_id),
    x_deletion_preview_token: str | None = Header(
        default=None, alias="X-Deletion-Preview-Token"
    ),
) -> Response:
    """Hard-delete the current research run and uniquely owned dependencies (#355 PR-D)."""
    try:
        result = first_pass_job_service.delete_research_run(
            user_id=user_id,
            preview_token=x_deletion_preview_token or "",
        )
    except FirstPassJobError as exc:
        code = 400 if "preview_token" in str(exc) else 503
        raise HTTPException(
            status_code=code,
            detail={"error": str(exc), "operation": exc.operation},
        ) from exc
    outcome = str(result.get("outcome") or "")
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail="research_run_not_found")
    if outcome == "conflict":
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(result.get("code") or "deletion_conflict"),
                "preview": result.get("preview"),
            },
        )
    if outcome == "requested":
        return JSONResponse(
            status_code=202,
            content={
                "deletionState": "requested",
                "researchRunId": result.get("research_run_id"),
                "quiesceAfter": result.get("quiesce_after"),
                "auditId": result.get("audit_id"),
            },
        )
    return JSONResponse(
        status_code=200,
        content={
            "deleted": True,
            "idempotent": bool(result.get("idempotent")),
            "counts": result.get("counts") or {},
            "auditId": result.get("audit_id"),
            "researchRunId": result.get("research_run_id"),
        },
    )


@router.post("/research-runs/first-pass-drain", response_model=ResearchRunResponse)
def set_research_run_first_pass_drain(
    body: ResearchRunDrainControlRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ResearchRunResponse:
    """Pause or resume durable First-Pass / Phase-2 drain for the active run (#349/#361)."""
    try:
        run = research_run_store.set_first_pass_drain_enabled(
            user_id,
            enabled=body.enabled,
            notes=list(body.notes or []),
        )
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResearchRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "research_run_revision_conflict", "reason": exc.reason},
        ) from exc
    return _research_run_response(run)


@router.post("/research-runs/start", response_model=ResearchRunResponse)
def start_research_run(
    body: ResearchRunStartRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ResearchRunResponse:
    """Start Phase-1 by creating a durable first-pass job + research_run row.

    Never enables CronJob/Notion. Phase-2 starts via candidate-set technical-scan.
    Cross-pod idempotent via DB-fenced job claim (not process locks).
    """
    if body.phase == "phase1_first_pass":
        return _start_phase1_research_run(
            user_id=user_id,
            trading_day=body.tradingDay,
            notes=list(body.notes or []),
        )
    if body.phase == "phase2_technical":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "phase2_requires_candidate_technical_scan_endpoint",
                "hint": "POST /candidate-sets/technical-scan after candidate approval",
            },
        )
    raise HTTPException(status_code=400, detail="unsupported_phase")


@router.post(
    "/research-runs/adopt-first-pass-job",
    response_model=ResearchRunResponse,
)
def adopt_first_pass_job_into_research_run(
    body: ResearchRunAdoptFirstPassRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ResearchRunResponse:
    """Explicitly adopt an eligible standalone first-pass job (#359).

    Never silently auto-adopts historical jobs. Preserves persisted evidence.
    """
    try:
        run = research_run_store.adopt_standalone_first_pass_job(
            user_id,
            job_id=str(body.jobId).strip(),
            confirm_universe_digest=body.confirmUniverseDigest,
        )
    except ResearchRunStoreUnavailable as exc:
        if _is_pg_lock_contention(exc):
            raise _http_concurrent_update_retry(exc) from exc
        raise _research_run_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResearchRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": str(exc.reason or exc), "reason": exc.reason},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — map lock contention only
        if _is_pg_lock_contention(exc):
            raise _http_concurrent_update_retry(exc) from exc
        raise
    # Existing job attached under a new Research Run identity.
    return _research_run_response(run, start_outcome="job_resumed")


@router.post("/research-runs/gate", response_model=ResearchRunResponse)
def advance_research_run_gate(
    user_id: str = Depends(get_effective_user_id),
    firstPassJobId: str | None = Query(default=None),
    firstPassSnapshotId: str | None = Query(default=None),
    jobStatus: str | None = Query(default=None),
    scanStatus: str | None = Query(default=None),
) -> ResearchRunResponse:
    """Reconcile server-authoritative phase/status; client params are ignored."""
    del firstPassJobId, firstPassSnapshotId, jobStatus, scanStatus
    try:
        run = research_run_store.reconcile(user_id)
    except ResearchRunStoreUnavailable as exc:
        raise _research_run_unavailable(exc) from exc
    except ResearchRunConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "research_run_revision_conflict", "reason": exc.reason},
        ) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="research_run_not_found")
    return ResearchRunResponse(researchRun=run.as_dict())


@router.post("/report-evidence", response_model=ReportEvidenceResponse)
def post_report_evidence(
    body: ReportEvidenceRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ReportEvidenceResponse:
    """Compose analyst-ready evidence for external ChatGPT (#340). No Notion write.

    Modes:
    - first-pass-only: ``firstPassSnapshotId`` without ``scanId``
    - full-two-pass: ``scanId`` requires pinned first-pass + candidate-set provenance
    """
    technical = None
    ledger_rows: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    full_two_pass = bool(body.scanId)
    if body.scanId:
        state = _call_or_http(lambda: tw_research_scan_service.get_scan(body.scanId or ""))
        if state is None:
            raise HTTPException(status_code=404, detail="scan_not_found")
        if getattr(state, "user_id", None) not in {None, user_id}:
            raise HTTPException(status_code=404, detail="scan_not_found")
        technical = state.snapshot() if hasattr(state, "snapshot") else state
        if isinstance(technical, dict):
            provenance = dict(technical.get("candidateProvenance") or {})
        ledger_rows = sorted(
            (_record_export(record) for record in state.records.values()),
            key=lambda item: str(item.get("stockId") or ""),
        )

    first_pass_id = body.firstPassSnapshotId or provenance.get("firstPassSnapshotId")
    if full_two_pass and not first_pass_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "full_two_pass_requires_first_pass_snapshot"},
        )
    first_pass = None
    if first_pass_id:
        first_pass = tw_research_repository.get_first_pass_snapshot(
            str(first_pass_id), user_id=user_id
        )
        if first_pass is None:
            raise HTTPException(status_code=404, detail="first_pass_snapshot_not_found")

    candidate = None
    candidate_set_id = provenance.get("candidateSetId")
    candidate_set_version = provenance.get("candidateSetVersion")
    if full_two_pass:
        if candidate_set_id is None or candidate_set_version is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "full_two_pass_requires_candidate_set_provenance"},
            )
        try:
            from app.services.tw_research.first_pass_durable import get_candidate_set

            candidate = get_candidate_set(
                user_id=user_id,
                candidate_set_id=str(candidate_set_id),
                version=int(candidate_set_version),
            )
        except ResearchMembershipUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "durable_store_unavailable",
                    "retryable": True,
                    "operation": "candidate_set",
                    "message": str(exc),
                },
            ) from exc
        except KeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "pinned_candidate_set_not_found",
                    "candidateSetId": candidate_set_id,
                    "candidateSetVersion": candidate_set_version,
                },
            ) from exc
    else:
        # First-pass-only mode may optionally include latest candidate for context.
        try:
            candidate = latest_candidate_set(user_id=user_id)
        except ResearchMembershipUnavailable:
            candidate = None

    # Production snapshot dicts omit quality_status. Prefer owning job quality,
    # else content-based chip gating inside build_report_evidence (#347).
    if isinstance(first_pass, dict) and not (
        first_pass.get("quality_status") or first_pass.get("qualityStatus")
    ):
        job_id = None
        payload = first_pass.get("payload") if isinstance(first_pass.get("payload"), dict) else {}
        raw_job = payload.get("jobId") or payload.get("job_id")
        if isinstance(raw_job, str) and raw_job:
            job_id = raw_job
        if job_id:
            try:
                job = tw_research_repository.get_first_pass_job(job_id, user_id=user_id)
            except Exception:  # noqa: BLE001
                job = None
            if isinstance(job, dict) and job.get("quality_status"):
                first_pass = {**first_pass, "quality_status": job["quality_status"]}

    normalized_first = normalize_first_pass_snapshot(first_pass)
    if full_two_pass and isinstance(technical, dict):
        scan_fp_id = provenance.get("firstPassSnapshotId")
        scan_fp_digest = provenance.get("firstPassDigest")
        pack_fp_id = (normalized_first or {}).get("snapshotId")
        pack_fp_digest = (normalized_first or {}).get("contentDigest")
        cand_fp_id = (candidate or {}).get("firstPassSnapshotId")
        cand_fp_digest = (candidate or {}).get("firstPassDigest")
        if not scan_fp_id or not scan_fp_digest:
            raise HTTPException(
                status_code=409,
                detail={"error": "scan_missing_first_pass_pin"},
            )
        if not pack_fp_id or not pack_fp_digest:
            raise HTTPException(
                status_code=409,
                detail={"error": "first_pass_snapshot_missing_digest"},
            )
        if str(pack_fp_id) != str(scan_fp_id) or str(pack_fp_digest) != str(scan_fp_digest):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "evidence_snapshot_mismatch",
                    "firstPassSnapshotId": pack_fp_id,
                    "scanFirstPassSnapshotId": scan_fp_id,
                },
            )
        if str(cand_fp_id) != str(scan_fp_id) or str(cand_fp_digest) != str(scan_fp_digest):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "evidence_snapshot_mismatch",
                    "candidateFirstPassSnapshotId": cand_fp_id,
                    "scanFirstPassSnapshotId": scan_fp_id,
                },
            )
        if body.tradingDay:
            tech_day = technical.get("tradingDay") or provenance.get("tradingDay")
            if tech_day and str(tech_day) != str(body.tradingDay):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "evidence_trading_day_mismatch",
                        "requested": body.tradingDay,
                        "technical": tech_day,
                    },
                )
            fp_day = (normalized_first or {}).get("tradingDay")
            if fp_day and str(fp_day) != str(body.tradingDay):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "evidence_trading_day_mismatch",
                        "requested": body.tradingDay,
                        "firstPass": fp_day,
                    },
                )

    from app.core.endpoint_timing import endpoint_timing

    fp_records = []
    if isinstance(first_pass, dict):
        payload = first_pass.get("payload") if isinstance(first_pass.get("payload"), dict) else {}
        fp_records = list(payload.get("records") or [])
    with endpoint_timing(
        endpoint="POST /api/tw-research/report-evidence",
        snapshot_id=str(first_pass_id) if first_pass_id else None,
        record_count=len(fp_records),
        calendar_preload_attempted=True,
    ) as timing:
        pack = build_report_evidence(
            trading_day=body.tradingDay,
            first_pass_snapshot=first_pass,
            candidate_set=candidate,
            technical_scan=technical if isinstance(technical, dict) else None,
            ledger_rows=ledger_rows,
            mode="full_two_pass" if full_two_pass else "first_pass_only",
        )
        evidence = pack.as_dict()
        timing["rows_returned"] = len(evidence.get("symbols") or [])
        timing["result"] = evidence
        return ReportEvidenceResponse(evidence=evidence)


@router.get("/report-evidence/example-zh-tw")
def get_report_evidence_example_zh_tw(
    user_id: str = Depends(get_effective_user_id),
) -> dict[str, Any]:
    """Traditional Chinese fixture example — not a live recommendation (#340)."""
    del user_id
    return load_zh_tw_example_fixture()


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


@router.get("/scans", response_model=ScanListResponse)
def list_scans(
    limit: int = Query(default=20, ge=1, le=50),
    user_id: str = Depends(get_effective_user_id),
) -> ScanListResponse:
    """Recent persisted scans for this user. Read-only: no upstream fetches."""
    return ScanListResponse(
        scans=_call_or_http(
            lambda: tw_research_scan_service.list_scans(user_id, limit=limit)
        )
    )


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


@router.post("/scans/resume-budget", response_model=ScanSnapshotResponse)
def resume_budget_window(
    body: ResumeBudgetWindowRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ScanSnapshotResponse:
    """Open a bounded history-budget window and reopen unresolved symbols (#329)."""
    _require_owned_scan(body.scanId, user_id)
    state = _call_or_http(
        lambda: tw_research_scan_service.resume_budget_window(
            body.scanId,
            additional_month_fetches=body.additionalMonthFetches,
        )
    )
    return _snapshot(state)


@schedule_router.post("/schedule/run", response_model=ScheduleRunResponse)
def run_scheduled_research_tick(
    body: ScheduleRunRequest | None = None,
    x_tw_research_schedule_token: str | None = Header(default=None),
) -> ScheduleRunResponse:
    """Internal post-market schedule tick (#330). Token auth; disabled by default.

    Intentionally registered on ``schedule_router`` without session auth so a
    configured CronJob token works when ``AUTH_ENABLED=true``.
    """
    from app.api.capture_auth import require_capture_token
    from app.services.tw_research.scheduler import run_scheduled_research

    require_capture_token(
        x_tw_research_schedule_token,
        settings.tw_research_schedule_token,
        not_configured_detail="Taiwan research schedule endpoint is not configured",
    )
    payload = body or ScheduleRunRequest()
    owner = str(settings.tw_research_schedule_user_id or "").strip()
    if not owner:
        raise HTTPException(
            status_code=503,
            detail="TW_RESEARCH_SCHEDULE_USER_ID is not configured",
        )
    result = _call_or_http(
        lambda: run_scheduled_research(
            user_id=owner,
            enabled=bool(settings.tw_research_schedule_enabled),
            dry_run=payload.dryRun,
            max_batches=payload.maxBatches,
            target_count=payload.targetCount,
            trading_day=payload.tradingDay,
            resume_if_exhausted=payload.resumeIfExhausted,
        )
    )
    return ScheduleRunResponse(**result.as_dict())


@schedule_router.post(
    "/first-pass/drain",
    response_model=FirstPassDrainResponse,
)
def run_first_pass_drain_tick(
    body: FirstPassDrainRequest | None = None,
    x_tw_research_first_pass_drain_token: str | None = Header(default=None),
) -> FirstPassDrainResponse:
    """Internal durable first-pass drain tick (#349). Token auth; disabled by default.

    Drains only the research-run owned job when ``schedule_enabled`` is true.
    Does not enable the technical-scan CronJob, Phase-2, or Notion writes.
    """
    from app.api.capture_auth import require_capture_token
    from app.services.tw_research.first_pass_drain import drain_authorized_first_pass

    require_capture_token(
        x_tw_research_first_pass_drain_token,
        settings.tw_research_first_pass_drain_token,
        not_configured_detail="Taiwan research first-pass drain endpoint is not configured",
    )
    payload = body or FirstPassDrainRequest()
    owner = str(settings.tw_research_first_pass_drain_user_id or "").strip()
    if not owner:
        raise HTTPException(
            status_code=503,
            detail="TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID is not configured",
        )
    result = drain_authorized_first_pass(
        user_id=owner,
        enabled=bool(settings.tw_research_first_pass_drain_enabled),
        dry_run=payload.dryRun,
        max_batches=payload.maxBatches,
        batch_size=payload.batchSize,
        time_budget_seconds=payload.timeBudgetSeconds,
        job_id=payload.jobId,
    )
    return FirstPassDrainResponse(**result.as_dict())


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


@router.get("/scans/{scan_id}/progress", response_model=ScanProgressResponse)
def get_scan_progress(
    scan_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ScanProgressResponse:
    """Counters-only progress. No ledger payload and no upstream fetches (#327)."""
    progress = _call_or_http(
        lambda: tw_research_scan_service.get_scan_progress(scan_id, user_id=user_id)
    )
    if progress is None:
        raise HTTPException(status_code=404, detail=f"Unknown scan_id: {scan_id}")
    return ScanProgressResponse(progress=progress)


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


def _filter_export_snapshot_rows(rows: list[dict], *, kind: str) -> list[dict]:
    if kind == "qualified":
        return [
            row
            for row in rows
            if (row.get("daily") or {}).get("passStatus") == "pass"
            and (row.get("monthly") or {}).get("passStatus") == "pass"
        ]
    if kind == "enriched":
        return [row for row in rows if row.get("enrichment")]
    return rows


def _frozen_export_timestamp(snapshot: dict) -> str:
    manifest = dict(snapshot.get("manifest_json") or {})
    frozen = manifest.get("finalizedAt") or manifest.get("generatedAt")
    if frozen:
        return str(frozen)
    frozen_at = snapshot["finalized_at"]
    return frozen_at.isoformat() if hasattr(frozen_at, "isoformat") else str(frozen_at)


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
    rows = _filter_export_snapshot_rows(
        list(snapshot.get("ledger_json") or []), kind=kind
    )
    # Prefer the exact frozen string stored at finalize time so digests stay stable
    # across DB round-trips (SQLite/Postgres datetime serialization can differ).
    frozen = _frozen_export_timestamp(snapshot)
    # After scan retention, FK scan_id may be NULL; use the frozen manifest id.
    manifest = dict(snapshot.get("manifest_json") or {})
    frozen_scan_id = manifest.get("scanId") or snapshot.get("scan_id") or ""
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
    state = _call_or_http(lambda: tw_research_scan_service.get_scan(scan_id))
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
    except TwResearchConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": exc.code,
                "operation": exc.operation,
                "scanId": exc.scan_id,
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TwResearchAdmissionError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "scan_admission_limited",
                "retryable": True,
                "operation": exc.operation,
                "scanId": exc.scan_id,
                "inFlight": exc.in_flight,
                "limit": exc.limit,
                "message": str(exc),
            },
        ) from exc
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


def _parse_stock_ids_query(
    stock_ids: str | None,
    *,
    max_ids: int = 50,
) -> list[str]:
    """Parse comma-separated stock ids, dedupe (order-preserving), cap at max_ids."""
    if not stock_ids:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in str(stock_ids).split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        key = cleaned.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= max_ids:
            break
    return out


def _camel_job(job: dict[str, object]) -> dict[str, object]:
    return {
        "jobId": job.get("job_id"),
        "userId": job.get("user_id"),
        "universeVersion": job.get("universe_version"),
        "universeDigest": job.get("universe_digest"),
        "membershipVersion": job.get("membership_version"),
        "firstPassVersion": job.get("first_pass_version"),
        "stockIds": job.get("stock_ids"),
        "universeSize": job.get("universe_size"),
        "selector": job.get("selector"),
        "status": job.get("status"),
        "qualityStatus": job.get("quality_status"),
        "cursor": job.get("cursor"),
        "reserveCursor": job.get("reserve_cursor"),
        "leaseGeneration": job.get("lease_generation"),
        "activeSnapshotId": job.get("active_snapshot_id"),
        "notes": job.get("notes"),
        "archivedAt": job.get("archived_at"),
        "deletionState": job.get("deletion_state"),
        "deletionRequestedAt": job.get("deletion_requested_at"),
        "deletionError": job.get("deletion_error"),
        "createdAt": job.get("created_at"),
        "updatedAt": job.get("updated_at"),
    }


def _selector_from_request(body: UniverseSelectorRequest) -> UniverseSelector:
    return UniverseSelector(
        mode=body.mode,
        theme_keys=tuple(body.themeKeys),
        stock_ids=tuple(body.stockIds),
    )
