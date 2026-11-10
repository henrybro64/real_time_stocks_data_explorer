"""Durable scan/export helpers on top of TwResearchRepository (#318/#319)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.repositories.tw_research_repository import (
    TwResearchRepository,
    tw_research_repository,
)

logger = logging.getLogger(__name__)
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    ENRICHMENT_VERSION,
    EXPORT_CHUNK_SIZE,
    RESEARCH_SCHEMA_VERSION,
)
from app.services.tw_research.export import (
    build_export_chunk_from_rows,
    build_immutable_manifest,
    stable_export_content_digest,
)
from app.services.tw_research.scan import (
    ScanState,
    SymbolScanRecord,
    TwResearchDurableStoreError,
    describe_worker_activity,
)
from app.services.tw_research.universe import UniverseSelector

# Keep verified snapshots and recent scans for ~30 days by default.
DEFAULT_RETENTION_DAYS = 30


def scan_row_payload(state: ScanState, *, user_id: str | None = None) -> dict[str, Any]:
    return {
        "scan_id": state.scan_id,
        "user_id": user_id if user_id is not None else getattr(state, "user_id", None),
        "trading_day": state.trading_day,
        "universe_version": state.universe_version,
        "calculation_version": state.calculation_version,
        "enrichment_version": ENRICHMENT_VERSION,
        "selector_json": {
            "mode": state.selector.mode,
            "themeKeys": list(state.selector.theme_keys),
            "stockIds": list(state.selector.stock_ids),
        },
        "stock_ids_json": list(state.stock_ids),
        "status": state.status,
        "quality_status": getattr(state, "quality_status", "processed"),
        "cursor": state.cursor,
        "enrich_cursor": state.enrich_cursor,
        "enrich_reserve_ids_json": [],
        "target_count": state.target_count,
        "upstream_month_fetches": state.upstream_month_fetches,
        "max_upstream_month_fetches": state.max_upstream_month_fetches,
        "enrichments_run": state.enrichments_run,
        "max_enrichments_per_run": state.max_enrichments_per_run,
        "day_pass_ids_json": list(state.day_pass_ids),
        "qualified_ids_json": list(state.qualified_ids),
        "notes_json": list(state.notes),
        "active_snapshot_id": getattr(state, "active_snapshot_id", None),
    }


def symbol_row_payload(state: ScanState, record: SymbolScanRecord) -> dict[str, Any]:
    from dataclasses import asdict

    return {
        "scan_id": state.scan_id,
        "stock_id": record.stock_id,
        "trading_day": state.trading_day,
        "universe_version": state.universe_version,
        "calculation_version": state.calculation_version,
        "stage": record.stage,
        "readiness": record.readiness,
        "payload_json": asdict(record),
    }


def persist_scan(
    state: ScanState,
    *,
    repository: TwResearchRepository | None = None,
    user_id: str | None = None,
) -> bool:
    """Persist scan metadata. Returns False when a configured DB write fails."""
    repo = repository or tw_research_repository
    if not repo.is_available():
        return True
    try:
        repo.upsert_scan(scan_row_payload(state, user_id=user_id))
        return True
    except Exception as exc:  # noqa: BLE001 — surface without crashing the batch loop
        logger.warning("tw_research persist_scan failed for %s", state.scan_id, exc_info=True)
        _mark_persist_failure(state, f"persist_scan:{type(exc).__name__}")
        return False


def persist_symbol(
    state: ScanState,
    record: SymbolScanRecord,
    *,
    repository: TwResearchRepository | None = None,
    enrich_lease_generation: int | None = None,
) -> bool:
    repo = repository or tw_research_repository
    if not repo.is_available():
        return True
    try:
        result = repo.upsert_symbol_result(
            symbol_row_payload(state, record),
            enrich_lease_generation=enrich_lease_generation,
        )
        if result.get("enrichment_rejected"):
            # Keep in-memory enrichment for this worker, but durable store refused
            # a stale lease write. Drop local enrichment so consumers hydrate truth.
            record.enrichment = None
            note = "enrichment_lease_stale_write_rejected"
            if note not in state.notes:
                state.notes.append(note)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tw_research persist_symbol failed for %s/%s",
            state.scan_id,
            record.stock_id,
            exc_info=True,
        )
        _mark_persist_failure(state, f"persist_symbol:{type(exc).__name__}")
        return False


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def load_scan_state(
    scan_id: str,
    *,
    repository: TwResearchRepository | None = None,
) -> ScanState | None:
    """Load an authoritative scan snapshot from PostgreSQL.

    Returns ``None`` only when the scan ID is missing. Database failures raise
    ``TwResearchDurableStoreError`` so callers never treat a failed read as an
    empty or incomplete ledger (#327).
    """
    repo = repository or tw_research_repository
    if not repo.is_available():
        raise TwResearchDurableStoreError(
            "Durable research store is unavailable",
            scan_id=scan_id,
            operation="load_scan",
        )
    try:
        row = repo.get_scan(scan_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tw_research load_scan_state failed for %s", scan_id, exc_info=True)
        raise TwResearchDurableStoreError(
            f"Failed to load scan {scan_id}",
            scan_id=scan_id,
            operation="load_scan",
        ) from exc
    if row is None:
        return None
    state = _state_from_scan_row(row)
    try:
        symbol_rows = repo.list_symbol_results(scan_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tw_research list_symbol_results failed for %s", scan_id, exc_info=True)
        raise TwResearchDurableStoreError(
            f"Failed to load symbol results for {scan_id}",
            scan_id=scan_id,
            operation="load_symbol_results",
        ) from exc
    for symbol_row in symbol_rows:
        payload = dict(symbol_row.get("payload_json") or {})
        record = SymbolScanRecord(
            stock_id=symbol_row["stock_id"],
            stage=payload.get("stage", symbol_row["stage"]),
            readiness=payload.get("readiness", symbol_row.get("readiness", "processed")),
            daily=payload.get("daily"),
            monthly=payload.get("monthly"),
            sma20=payload.get("sma20"),
            sma60=payload.get("sma60"),
            sma120=payload.get("sma120"),
            sma240=payload.get("sma240"),
            week52_position=payload.get("week52_position"),
            rejection_reason=payload.get("rejection_reason"),
            checkpoint=tuple(payload["checkpoint"]) if payload.get("checkpoint") else None,
            theme_keys=tuple(payload.get("theme_keys") or ()),
            enrichment=payload.get("enrichment"),
            coverage=payload.get("coverage"),
        )
        state.records[record.stock_id] = record
    return state


def _state_from_scan_row(row: dict[str, Any]) -> ScanState:
    selector_payload = row.get("selector_json") or {}
    created = row["created_at"]
    updated = row["updated_at"]
    return ScanState(
        scan_id=row["scan_id"],
        trading_day=row["trading_day"],
        universe_version=row["universe_version"],
        calculation_version=row["calculation_version"],
        selector=UniverseSelector(
            mode=selector_payload.get("mode", "starter_preset"),
            theme_keys=tuple(selector_payload.get("themeKeys") or ()),
            stock_ids=tuple(selector_payload.get("stockIds") or ()),
        ),
        stock_ids=tuple(row.get("stock_ids_json") or ()),
        cursor=int(row["cursor"]),
        enrich_cursor=int(row["enrich_cursor"]),
        target_count=int(row["target_count"]),
        status=row["status"],  # type: ignore[arg-type]
        quality_status=row.get("quality_status", "processed"),  # type: ignore[arg-type]
        day_pass_ids=list(row.get("day_pass_ids_json") or []),
        qualified_ids=list(row.get("qualified_ids_json") or []),
        upstream_month_fetches=int(row["upstream_month_fetches"]),
        max_upstream_month_fetches=int(row["max_upstream_month_fetches"]),
        enrichments_run=int(row["enrichments_run"]),
        max_enrichments_per_run=int(row["max_enrichments_per_run"]),
        enrich_lease_generation=int(row.get("enrich_lease_generation") or 0),
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
        updated_at=updated.isoformat() if hasattr(updated, "isoformat") else str(updated),
        notes=list(row.get("notes_json") or []),
        user_id=row.get("user_id"),
        active_snapshot_id=row.get("active_snapshot_id"),
        reserve_expires_at=_iso_or_none(row.get("reserve_expires_at")),
        enrich_reserve_expires_at=_iso_or_none(row.get("enrich_reserve_expires_at")),
    )


def progress_revision(
    *,
    updated_at: str,
    status: str,
    quality_status: str,
    cursor: int,
    enrich_cursor: int,
    processed_count: int,
    upstream_month_fetches: int,
    enrichments_run: int,
    worker_activity: str,
    active_snapshot_id: str | None,
) -> str:
    """Compact change token so clients can skip full ledger reloads."""
    return "|".join(
        [
            updated_at,
            status,
            quality_status,
            str(cursor),
            str(enrich_cursor),
            str(processed_count),
            str(upstream_month_fetches),
            str(enrichments_run),
            worker_activity,
            active_snapshot_id or "",
        ]
    )


def load_scan_progress(
    scan_id: str,
    *,
    user_id: str | None = None,
    repository: TwResearchRepository | None = None,
) -> dict[str, Any] | None:
    """Counters-only progress from committed scan metadata + symbol count.

    Does not load symbol payloads or trigger upstream market-data fetches.
    Returns ``None`` for a missing scan or a scan owned by another user.
    """
    repo = repository or tw_research_repository
    if not repo.is_available():
        raise TwResearchDurableStoreError(
            "Durable research store is unavailable",
            scan_id=scan_id,
            operation="load_scan_progress",
        )
    try:
        row = repo.get_scan(scan_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tw_research load_scan_progress failed for %s", scan_id, exc_info=True)
        raise TwResearchDurableStoreError(
            f"Failed to load scan progress {scan_id}",
            scan_id=scan_id,
            operation="load_scan_progress",
        ) from exc
    if row is None:
        return None
    if user_id is not None and row.get("user_id") != user_id:
        return None
    try:
        processed_count = repo.count_symbol_results(scan_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tw_research count_symbol_results failed for %s",
            scan_id,
            exc_info=True,
        )
        raise TwResearchDurableStoreError(
            f"Failed to count symbol results for {scan_id}",
            scan_id=scan_id,
            operation="count_symbol_results",
        ) from exc
    stock_ids = list(row.get("stock_ids_json") or [])
    cursor = int(row.get("cursor") or 0)
    enrich_cursor = int(row.get("enrich_cursor") or 0)
    updated = row.get("updated_at")
    updated_at = updated.isoformat() if hasattr(updated, "isoformat") else str(updated or "")
    created = row.get("created_at")
    created_at = created.isoformat() if hasattr(created, "isoformat") else str(created or "")
    status = str(row.get("status") or "")
    quality_status = str(row.get("quality_status") or "processed")
    worker_activity = describe_worker_activity(
        status,
        row.get("reserve_expires_at"),
        row.get("enrich_reserve_expires_at"),
    )
    day_pass_ids = list(row.get("day_pass_ids_json") or [])
    qualified_ids = list(row.get("qualified_ids_json") or [])
    active_snapshot_id = row.get("active_snapshot_id")
    revision = progress_revision(
        updated_at=updated_at,
        status=status,
        quality_status=quality_status,
        cursor=cursor,
        enrich_cursor=enrich_cursor,
        processed_count=processed_count,
        upstream_month_fetches=int(row.get("upstream_month_fetches") or 0),
        enrichments_run=int(row.get("enrichments_run") or 0),
        worker_activity=worker_activity,
        active_snapshot_id=active_snapshot_id,
    )
    return {
        "scanId": row["scan_id"],
        "tradingDay": row["trading_day"],
        "universeVersion": row["universe_version"],
        "status": status,
        "qualityStatus": quality_status,
        "cursor": cursor,
        "enrichCursor": enrich_cursor,
        "universeSize": len(stock_ids),
        "processedCount": processed_count,
        "remainingCount": max(0, len(stock_ids) - cursor),
        "targetCount": int(row.get("target_count") or 0),
        "dayPassCount": len(day_pass_ids),
        "qualifiedCount": len(qualified_ids),
        "enrichmentsRun": int(row.get("enrichments_run") or 0),
        "maxEnrichmentsPerRun": int(row.get("max_enrichments_per_run") or 0),
        "upstreamMonthFetches": int(row.get("upstream_month_fetches") or 0),
        "maxUpstreamMonthFetches": int(row.get("max_upstream_month_fetches") or 0),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "activeSnapshotId": active_snapshot_id,
        "reserveExpiresAt": _iso_or_none(row.get("reserve_expires_at")),
        "enrichReserveExpiresAt": _iso_or_none(row.get("enrich_reserve_expires_at")),
        "workerActivity": worker_activity,
        "ledgerRevision": revision,
        "includesLedger": False,
    }


def finalize_verified_snapshot(
    state: ScanState,
    *,
    repository: TwResearchRepository | None = None,
    user_id: str | None = None,
    finalized_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Atomically store an immutable verified export snapshot (#318/#319).

    Prior verified snapshots remain readable; the scan's active_snapshot_id
    points at the newest successful finalize. Incomplete ledgers never get
    quality_status=verified_ready.
    """
    repo = repository or tw_research_repository
    if not repo.is_available():
        return None

    if state.status not in {
        "completed_processing",
        "verified_ready",
        "budget_exhausted",
    }:
        return None
    if getattr(state, "quality_status", None) not in {
        "technically_qualified",
        "verified_ready",
        "source_incomplete",
        "processed",
    }:
        return None
    # Partial ledgers (some source_incomplete / insufficient history) may publish
    # when labeled via sourceCompleteness; zero qualifiers are also valid.

    stamp = finalized_at or datetime.now(timezone.utc)
    rows = [_export_row(record) for record in state.records.values()]
    rows.sort(key=lambda item: item["stockId"])
    chunk_digests: dict[str, str] = {}
    total_chunks = (
        max(1, (len(rows) + EXPORT_CHUNK_SIZE - 1) // EXPORT_CHUNK_SIZE) if rows else 0
    )
    for index in range(total_chunks):
        chunk = build_export_chunk_from_rows(
            rows,
            chunk_index=index,
            chunk_size=EXPORT_CHUNK_SIZE,
            kind="ledger",
            scan_id=state.scan_id,
            trading_day=state.trading_day,
            frozen_at=stamp.isoformat(),
        )
        chunk_digests[str(index)] = chunk["checksumSha256"]

    digest = stable_export_content_digest(
        rows=rows,
        chunk_digests=chunk_digests,
        universe_version=state.universe_version,
        trading_day=state.trading_day,
        quality_status="verified_ready",
    )
    snapshot_id = str(uuid4())
    manifest = build_immutable_manifest(
        state,
        rows=rows,
        chunk_digests=chunk_digests,
        frozen_at=stamp.isoformat(),
        quality_status="verified_ready",
    )
    # Populate before freeze/store so latest-manifest clients see a real id.
    # snapshotId is metadata only; content_digest stays UUID-independent.
    manifest["snapshotId"] = snapshot_id
    manifest["contentDigest"] = digest
    payload = {
        "snapshot_id": snapshot_id,
        "scan_id": state.scan_id,
        "user_id": user_id if user_id is not None else getattr(state, "user_id", None),
        "universe_version": state.universe_version,
        "trading_day": state.trading_day,
        "schema_version": RESEARCH_SCHEMA_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "enrichment_version": ENRICHMENT_VERSION,
        "quality_status": "verified_ready",
        "content_digest": digest,
        "manifest_json": manifest,
        "ledger_json": rows,
        "chunk_digests_json": chunk_digests,
        "finalized_at": stamp,
    }
    try:
        stored = repo.insert_export_snapshot(payload)
    except Exception:  # noqa: BLE001
        logger.warning(
            "tw_research finalize_verified_snapshot failed for %s",
            state.scan_id,
            exc_info=True,
        )
        return None

    # Snapshot row is already immutable. Only advertise verified_ready after the
    # scan pointer/status write succeeds; prior published snapshots stay readable.
    previous_snapshot_id = state.active_snapshot_id
    previous_quality = state.quality_status
    previous_status = state.status
    state.active_snapshot_id = stored["snapshot_id"]
    state.quality_status = "verified_ready"
    state.status = "verified_ready"
    if not persist_scan(state, repository=repo, user_id=payload["user_id"]):
        state.active_snapshot_id = previous_snapshot_id
        state.quality_status = previous_quality
        state.status = previous_status
        logger.warning(
            "tw_research finalize snapshot stored but scan pointer persist failed for %s",
            state.scan_id,
        )
        return None
    return {
        "snapshotId": stored["snapshot_id"],
        "contentDigest": stored["content_digest"],
        "manifest": stored["manifest_json"],
        "finalizedAt": stored["finalized_at"].isoformat()
        if hasattr(stored["finalized_at"], "isoformat")
        else str(stored["finalized_at"]),
    }


def retention_cleanup(
    *,
    repository: TwResearchRepository | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, int]:
    """Drop aged snapshots first, then aged scans (snapshots survive via SET NULL)."""
    repo = repository or tw_research_repository
    if not repo.is_available():
        return {"snapshotsDeleted": 0, "scansDeleted": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    snapshots = repo.delete_snapshots_older_than(cutoff)
    scans = repo.delete_scans_older_than(cutoff)
    return {"snapshotsDeleted": snapshots, "scansDeleted": scans}


def mark_persist_failure(state: ScanState, reason: str) -> None:
    """Mark a durable write/complete failure without mutating prior verified snapshots."""
    note = f"persist_failed:{reason}"
    if note not in state.notes:
        state.notes.append(note)
    # Do not clobber an already-finalized verified scan.
    if state.status != "verified_ready":
        state.status = "failed"


# Backward-compatible private alias.
_mark_persist_failure = mark_persist_failure


def _export_row(record: SymbolScanRecord) -> dict[str, Any]:
    return {
        "stockId": record.stock_id,
        "stage": record.stage,
        "readiness": record.readiness,
        "rejectionReason": record.rejection_reason,
        "daily": record.daily,
        "monthly": record.monthly,
        "sma20": record.sma20,
        "sma60": record.sma60,
        "sma120": record.sma120,
        "sma240": record.sma240,
        "week52Position": record.week52_position,
        "enrichment": record.enrichment,
        "themeKeys": list(record.theme_keys or ()),
        "coverage": record.coverage,
    }
