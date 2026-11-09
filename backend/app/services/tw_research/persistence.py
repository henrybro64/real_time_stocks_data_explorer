"""Durable scan/export helpers on top of TwResearchRepository (#318/#319)."""

from __future__ import annotations

import hashlib
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
    canonical_json_dumps,
)
from app.services.tw_research.scan import ScanState, SymbolScanRecord
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
) -> bool:
    repo = repository or tw_research_repository
    if not repo.is_available():
        return True
    try:
        repo.upsert_symbol_result(symbol_row_payload(state, record))
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


def load_scan_state(
    scan_id: str,
    *,
    repository: TwResearchRepository | None = None,
) -> ScanState | None:
    repo = repository or tw_research_repository
    if not repo.is_available():
        return None
    try:
        row = repo.get_scan(scan_id)
    except Exception:  # noqa: BLE001
        logger.warning("tw_research load_scan_state failed for %s", scan_id, exc_info=True)
        return None
    if row is None:
        return None
    selector_payload = row.get("selector_json") or {}
    created = row["created_at"]
    updated = row["updated_at"]
    state = ScanState(
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
        created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
        updated_at=updated.isoformat() if hasattr(updated, "isoformat") else str(updated),
        notes=list(row.get("notes_json") or []),
        user_id=row.get("user_id"),
        active_snapshot_id=row.get("active_snapshot_id"),
    )
    try:
        symbol_rows = repo.list_symbol_results(scan_id)
    except Exception:  # noqa: BLE001
        logger.warning("tw_research list_symbol_results failed for %s", scan_id, exc_info=True)
        return state
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

    if state.status not in {"completed_processing", "verified_ready"}:
        return None
    if getattr(state, "quality_status", None) not in {
        "technically_qualified",
        "verified_ready",
    }:
        return None
    verified_ids = [
        stock_id
        for stock_id, record in state.records.items()
        if record.readiness == "verified_ready"
    ]
    if not verified_ids:
        return None
    incomplete = any(
        record.readiness == "source_incomplete" for record in state.records.values()
    )
    if incomplete:
        # Dual-pass symbols with coverage gaps must not publish as verified.
        return None

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

    manifest = build_immutable_manifest(
        state,
        rows=rows,
        chunk_digests=chunk_digests,
        frozen_at=stamp.isoformat(),
        quality_status="verified_ready",
    )
    content = {
        "manifest": manifest,
        "ledger": rows,
        "chunkDigests": chunk_digests,
    }
    digest = hashlib.sha256(canonical_json_dumps(content).encode("utf-8")).hexdigest()
    snapshot_id = str(uuid4())
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
    state.active_snapshot_id = stored["snapshot_id"]
    state.quality_status = "verified_ready"
    state.status = "verified_ready"
    persist_scan(state, repository=repo, user_id=payload["user_id"])
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


def _mark_persist_failure(state: ScanState, reason: str) -> None:
    note = f"persist_failed:{reason}"
    if note not in state.notes:
        state.notes.append(note)
    # Do not clobber an already-finalized verified scan.
    if state.status != "verified_ready":
        state.status = "failed"


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
