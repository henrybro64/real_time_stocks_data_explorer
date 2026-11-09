"""Browser-readable research export payloads (#314/#319).

Reads precomputed scan/enrichment state or immutable verified snapshots only.
Does not trigger upstream fetches. Exports never include portfolio holdings,
API keys, or session secrets.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    CONTRACT_VERSION,
    ENRICHMENT_VERSION,
    EXPORT_CHUNK_SIZE,
    RESEARCH_SCHEMA_VERSION,
)
from app.services.tw_research.enrichment import is_technically_qualified
from app.services.tw_research.preset import starter_preset_theme_keys
from app.services.tw_research.scan import ScanState
from app.services.tw_research.universe import UniverseSelector


def canonical_json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def content_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()


def _publication_statuses(state: ScanState, ledger: list[Any]) -> dict[str, str]:
    """Derive consumer-facing completeness labels (independent of any Agent)."""
    total = len(state.stock_ids)
    processed = min(int(state.cursor), total) if total else 0
    incomplete = sum(
        1
        for record in ledger
        if getattr(record, "readiness", None) == "source_incomplete"
    )
    qualified = [
        record
        for record in ledger
        if is_technically_qualified(record)
        or getattr(record, "readiness", None) in {"technically_qualified", "verified_ready"}
    ]
    enriched = [
        record for record in qualified if getattr(record, "enrichment", None) is not None
    ]

    if state.status == "failed":
        technical = "unavailable"
    elif state.status in {"completed_processing", "verified_ready", "budget_exhausted"}:
        technical = "complete"
    elif state.status in {"running", "partial"} and processed > 0:
        technical = "in_progress" if processed < total else "partial"
    elif processed == 0:
        technical = "not_started"
    else:
        technical = "partial"

    if not qualified:
        enrichment = "unavailable" if technical == "complete" else "not_started"
    elif len(enriched) == 0:
        enrichment = "not_started" if state.enrichments_run == 0 else "in_progress"
    elif len(enriched) < len(qualified):
        enrichment = "partial"
    else:
        enrichment = "complete"

    if incomplete:
        source = "partial"
    elif technical == "complete":
        source = "complete"
    elif technical == "unavailable":
        source = "unavailable"
    else:
        source = "partial"

    return {
        "technicalStatus": technical,
        "enrichmentStatus": enrichment,
        "sourceCompleteness": source,
    }


def _manifest_counts(state: ScanState, ledger: list[Any]) -> dict[str, int]:
    qualified = [record for record in ledger if is_technically_qualified(record)]
    verified = [
        record
        for record in ledger
        if getattr(record, "readiness", None) == "verified_ready"
    ]
    enriched = [
        record
        for record in qualified
        if getattr(record, "enrichment", None) is not None
    ]
    insufficient = 0
    for record in ledger:
        coverage = getattr(record, "coverage", None) or {}
        daily = coverage.get("daily") or {}
        monthly = coverage.get("monthly") or {}
        reasons = {daily.get("reason"), monthly.get("reason")}
        if "insufficient_history" in reasons or "insufficient_monthly_history" in reasons:
            insufficient += 1
    rejected = sum(1 for record in ledger if record.stage == "rejected")
    fully_evaluated = sum(
        1
        for record in ledger
        if getattr(record, "readiness", None)
        in {"processed", "technically_qualified", "verified_ready", "source_incomplete", "rejected"}
    )
    return {
        "requested": len(state.stock_ids) + rejected,
        "accepted": len(state.stock_ids),
        "processed": min(int(state.cursor), len(state.stock_ids)),
        "fullyEvaluated": fully_evaluated,
        "attempted": len(ledger),
        "dayPass": len(state.day_pass_ids),
        "technicallyQualified": len(qualified),
        "qualifiedCount": len(qualified),
        "verifiedReady": len(verified),
        "sourceIncomplete": sum(
            1
            for record in ledger
            if getattr(record, "readiness", None) == "source_incomplete"
        ),
        "insufficientHistory": insufficient,
        "enriched": len(enriched),
        "enrichedCount": len(enriched),
        "rejected": rejected,
        "unknownDaily": sum(
            1
            for record in ledger
            if (record.daily or {}).get("passStatus") == "unknown"
        ),
    }


def build_export_manifest(state: ScanState, *, generated_at: str) -> dict[str, Any]:
    ledger = list(state.records.values())
    theme_summary = _theme_summary(state.selector, ledger)
    quality = getattr(state, "quality_status", state.status)
    publication = _publication_statuses(state, ledger)
    counts = _manifest_counts(state, ledger)
    return {
        "schemaVersion": RESEARCH_SCHEMA_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "calculationVersion": CALCULATION_VERSION,
        "enrichmentVersion": ENRICHMENT_VERSION,
        "generatedAt": generated_at,
        "scanId": state.scan_id,
        "snapshotId": getattr(state, "active_snapshot_id", None),
        "tradingDay": state.trading_day,
        "lastCompleteMonth": _last_complete_month(state.trading_day),
        "status": state.status,
        "qualityStatus": quality,
        "technicalStatus": publication["technicalStatus"],
        "enrichmentStatus": publication["enrichmentStatus"],
        "sourceCompleteness": publication["sourceCompleteness"],
        "qualifiedCount": counts["qualifiedCount"],
        "enrichedCount": counts["enrichedCount"],
        "universe": {
            "mode": state.selector.mode,
            "universeVersion": state.universe_version,
            "themeKeys": list(state.selector.theme_keys),
            "explicitStockIdsCount": len(state.selector.stock_ids),
            "size": len(state.stock_ids),
            "processedCount": state.cursor,
            "remainingCount": max(0, len(state.stock_ids) - state.cursor),
            "continuationCursor": state.cursor,
        },
        "targetCount": state.target_count,
        "counts": counts,
        "themeSummary": theme_summary,
        "notes": [
            "target_count is an output selection parameter only; actual pass count is not padded.",
            "Starter 14-sector labels are optional filters, not an allowlist.",
            "technicalStatus describes indicator/coverage evaluation; enrichmentStatus is independent.",
            "Missing chip/valuation fields never convert a technical pass into a fail.",
            "completed_processing means batches finished; quality verified_ready means an immutable snapshot was published.",
            "GET/export routes are read-only over persisted state and never fetch market data.",
            "Stock Ticker does not track consumer acknowledgment and does not write to Notion.",
        ],
        "checkpointsDurable": True,
        "checkpointsEphemeral": False,
        "checkpointContract": {
            "key": [
                "trading_day",
                "universe_version",
                "calculation_version",
                "symbol",
                "scan_id",
            ],
            "durable": True,
        },
        "exportChunkSize": EXPORT_CHUNK_SIZE,
    }


def stable_export_content_digest(
    *,
    rows: list[dict[str, Any]],
    chunk_digests: dict[str, str],
    universe_version: str,
    trading_day: str,
    quality_status: str,
) -> str:
    """Hash research content only — no timestamps or scan/snapshot UUIDs (#319)."""
    return content_digest(
        {
            "schemaVersion": RESEARCH_SCHEMA_VERSION,
            "calculationVersion": CALCULATION_VERSION,
            "enrichmentVersion": ENRICHMENT_VERSION,
            "universeVersion": universe_version,
            "tradingDay": trading_day,
            "qualityStatus": quality_status,
            "rows": rows,
            "chunkDigests": chunk_digests,
        }
    )


def stable_chunk_checksum(
    *,
    rows: list[dict[str, Any]],
    chunk_index: int,
    chunk_size: int,
    kind: str,
    trading_day: str,
    total_rows: int,
) -> str:
    """Checksum over chunk research payload; excludes scanId / generatedAt."""
    total_chunks = (total_rows + chunk_size - 1) // chunk_size if total_rows else 0
    return content_digest(
        {
            "schemaVersion": RESEARCH_SCHEMA_VERSION,
            "tradingDay": trading_day,
            "kind": kind,
            "chunkIndex": chunk_index,
            "chunkSize": chunk_size,
            "totalRows": total_rows,
            "totalChunks": total_chunks,
            "rows": rows,
        }
    )


def build_immutable_manifest(
    state: ScanState,
    *,
    rows: list[dict[str, Any]],
    chunk_digests: dict[str, str],
    frozen_at: str,
    quality_status: str,
) -> dict[str, Any]:
    """Manifest frozen at finalize time; content digests stay UUID/time-stable (#319)."""
    base = build_export_manifest(state, generated_at=frozen_at)
    base["qualityStatus"] = quality_status
    base["finalizedAt"] = frozen_at
    base["generatedAt"] = frozen_at
    base["chunkDigests"] = chunk_digests
    base["chunkIds"] = sorted(chunk_digests.keys(), key=lambda value: int(value))
    base["ledgerRowCount"] = len(rows)
    base["contentDigest"] = stable_export_content_digest(
        rows=rows,
        chunk_digests=chunk_digests,
        universe_version=state.universe_version,
        trading_day=state.trading_day,
        quality_status=quality_status,
    )
    return base


def build_export_chunk_from_rows(
    rows: list[dict[str, Any]],
    *,
    chunk_index: int,
    chunk_size: int,
    kind: str,
    scan_id: str,
    trading_day: str,
    frozen_at: str,
) -> dict[str, Any]:
    total = len(rows)
    start = chunk_index * chunk_size
    slice_rows = rows[start : start + chunk_size]
    payload = {
        "schemaVersion": RESEARCH_SCHEMA_VERSION,
        "generatedAt": frozen_at,
        "scanId": scan_id,
        "tradingDay": trading_day,
        "kind": kind,
        "chunkIndex": chunk_index,
        "chunkSize": chunk_size,
        "totalRows": total,
        "totalChunks": (total + chunk_size - 1) // chunk_size if total else 0,
        "rows": slice_rows,
    }
    payload["checksumSha256"] = stable_chunk_checksum(
        rows=slice_rows,
        chunk_index=chunk_index,
        chunk_size=chunk_size,
        kind=kind,
        trading_day=trading_day,
        total_rows=total,
    )
    return payload


def build_export_chunk(
    state: ScanState,
    *,
    chunk_index: int,
    chunk_size: int = EXPORT_CHUNK_SIZE,
    kind: str = "ledger",
    generated_at: str,
) -> dict[str, Any]:
    if chunk_index < 0:
        raise ValueError("chunk_index must be >= 0")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be >= 1")

    if kind == "qualified":
        rows = [
            _record_export(record)
            for record in state.records.values()
            if is_technically_qualified(record)
        ]
    elif kind == "enriched":
        rows = [
            _record_export(record)
            for record in state.records.values()
            if is_technically_qualified(record) and getattr(record, "enrichment", None)
        ]
    else:
        rows = [_record_export(record) for record in state.records.values()]

    rows.sort(key=lambda item: item["stockId"])
    return build_export_chunk_from_rows(
        rows,
        chunk_index=chunk_index,
        chunk_size=chunk_size,
        kind=kind,
        scan_id=state.scan_id,
        trading_day=state.trading_day,
        frozen_at=generated_at,
    )


def build_export_csv(state: ScanState, *, kind: str = "ledger") -> str:
    chunk = build_export_chunk(
        state,
        chunk_index=0,
        chunk_size=max(len(state.records), 1),
        kind=kind,
        generated_at=state.updated_at or state.created_at,
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "stockId",
            "stage",
            "rejectionReason",
            "dailyPass",
            "monthlyPass",
            "dailyAsOf",
            "monthlyAsOf",
            "enriched",
            "themeKeys",
        ],
    )
    writer.writeheader()
    for row in chunk["rows"]:
        writer.writerow(
            {
                "stockId": row["stockId"],
                "stage": row.get("stage"),
                "rejectionReason": row.get("rejectionReason") or "",
                "dailyPass": (row.get("daily") or {}).get("passStatus"),
                "monthlyPass": (row.get("monthly") or {}).get("passStatus"),
                "dailyAsOf": (row.get("daily") or {}).get("asOf") or "",
                "monthlyAsOf": (row.get("monthly") or {}).get("asOf") or "",
                "enriched": bool(row.get("enrichment")),
                "themeKeys": "|".join(row.get("themeKeys") or []),
            }
        )
    return buffer.getvalue()


def build_browser_text_view(
    state: ScanState,
    *,
    generated_at: str,
    chunk_index: int = 0,
    kind: str = "ledger",
) -> str:
    """Compact authenticated JSON for a browser client to copy.

    Returns one export chunk plus continuation metadata so any authorized
    consumer can walk the ledger without loading every row at once.
    """
    manifest = build_export_manifest(state, generated_at=generated_at)
    chunk = build_export_chunk(
        state,
        chunk_index=chunk_index,
        kind=kind,
        generated_at=generated_at,
    )
    total_chunks = int(chunk.get("totalChunks") or 0)
    next_index = chunk_index + 1 if chunk_index + 1 < total_chunks else None
    package = {
        "manifest": manifest,
        "chunk": chunk,
        "continuation": {
            "kind": kind,
            "chunkIndex": chunk_index,
            "nextChunkIndex": next_index,
            "totalChunks": total_chunks,
            "chunkIndexes": list(range(total_chunks)),
            "chunkPathTemplate": (
                f"/api/tw-research/scans/{state.scan_id}/export/chunk"
                "?chunkIndex={chunkIndex}&kind={kind}"
            ),
            "browserTextPathTemplate": (
                f"/api/tw-research/scans/{state.scan_id}/export/browser-text"
                "?chunkIndex={chunkIndex}&kind={kind}"
            ),
            "note": (
                "Copy each chunkIndex from 0..totalChunks-1. "
                "Rows are sorted by stockId; enrichment is included in ledger rows."
            ),
        },
        "producer": {
            "frontendExportReady": True,
            "independentPublisher": True,
            "note": (
                "Stock Ticker publishes immutable research snapshots independently. "
                "It does not track consumer acknowledgment and never writes to Notion."
            ),
        },
    }
    return json.dumps(package, ensure_ascii=False, indent=2, default=str)


def _record_export(record: Any) -> dict[str, Any]:
    enrichment = getattr(record, "enrichment", None)
    return {
        "stockId": record.stock_id,
        "stage": record.stage,
        "readiness": getattr(record, "readiness", "processed"),
        "rejectionReason": record.rejection_reason,
        "daily": record.daily,
        "monthly": record.monthly,
        "sma20": record.sma20,
        "sma60": record.sma60,
        "sma120": record.sma120,
        "sma240": record.sma240,
        "week52Position": record.week52_position,
        "enrichment": enrichment,
        "themeKeys": list(getattr(record, "theme_keys", ()) or ()),
        "coverage": getattr(record, "coverage", None),
    }


def _theme_summary(selector: UniverseSelector, ledger: list[Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    unclassified = 0
    for record in ledger:
        keys = list(getattr(record, "theme_keys", ()) or ())
        if not keys:
            unclassified += 1
            continue
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    return {
        "dynamic": True,
        "optionalStarterThemeKeys": list(starter_preset_theme_keys()),
        "selectedThemeKeys": list(selector.theme_keys),
        "countsByTheme": counts,
        "unclassifiedCount": unclassified,
    }


def _last_complete_month(trading_day: str) -> str:
    year, month, _day = (int(part) for part in trading_day.split("-"))
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"
