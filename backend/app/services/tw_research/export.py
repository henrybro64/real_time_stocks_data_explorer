"""Browser-readable research export payloads (#314).

Reads precomputed scan/enrichment state only. Does not trigger upstream fetches.
Exports never include portfolio holdings, API keys, or session secrets.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

from app.services.tw_research.constants import (
    CONTRACT_VERSION,
    ENRICHMENT_VERSION,
    EXPORT_CHUNK_SIZE,
    RESEARCH_SCHEMA_VERSION,
)
from app.services.tw_research.enrichment import is_technically_qualified
from app.services.tw_research.preset import starter_preset_theme_keys
from app.services.tw_research.scan import ScanState
from app.services.tw_research.universe import UniverseSelector


def build_export_manifest(state: ScanState, *, generated_at: str) -> dict[str, Any]:
    ledger = list(state.records.values())
    qualified = [record for record in ledger if is_technically_qualified(record)]
    enriched = [
        record
        for record in qualified
        if getattr(record, "enrichment", None) is not None
    ]
    theme_summary = _theme_summary(state.selector, ledger)
    return {
        "schemaVersion": RESEARCH_SCHEMA_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "enrichmentVersion": ENRICHMENT_VERSION,
        "generatedAt": generated_at,
        "scanId": state.scan_id,
        "tradingDay": state.trading_day,
        "lastCompleteMonth": _last_complete_month(state.trading_day),
        "status": state.status,
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
        "counts": {
            "attempted": len(ledger),
            "dayPass": len(state.day_pass_ids),
            "technicallyQualified": len(qualified),
            "enriched": len(enriched),
            "rejected": sum(1 for record in ledger if record.stage == "rejected"),
            "unknownDaily": sum(
                1
                for record in ledger
                if (record.daily or {}).get("passStatus") == "unknown"
            ),
        },
        "themeSummary": theme_summary,
        "notes": [
            "target_count is an output selection parameter only; actual pass count is not padded.",
            "Starter 14-sector labels are optional filters, not an allowlist.",
            "frontend_export_ready; external_notion_published is owned by an outside Agent (#315).",
            "No Notion SDK or credentials in Stock Ticker.",
        ],
        "checkpointsEphemeral": True,
        "exportChunkSize": EXPORT_CHUNK_SIZE,
    }


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
    total = len(rows)
    start = chunk_index * chunk_size
    slice_rows = rows[start : start + chunk_size]
    payload = {
        "schemaVersion": RESEARCH_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "scanId": state.scan_id,
        "tradingDay": state.trading_day,
        "kind": kind,
        "chunkIndex": chunk_index,
        "chunkSize": chunk_size,
        "totalRows": total,
        "totalChunks": (total + chunk_size - 1) // chunk_size if total else 0,
        "rows": slice_rows,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["checksumSha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return payload


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
    """Compact authenticated JSON text for a browser-only Agent to copy.

    Returns one export chunk plus continuation metadata so Agents can walk the
    full ledger without loading every row in a single payload.
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
        "handoff": {
            "frontendExportReady": True,
            "externalNotionPublished": "not_performed_in_stock_ticker",
            "instruction": (
                "External Agent may copy this JSON (and remaining chunks) and "
                "optionally publish to Notion. Stock Ticker never writes Notion."
            ),
        },
    }
    return json.dumps(package, ensure_ascii=False, indent=2, default=str)


def _record_export(record: Any) -> dict[str, Any]:
    enrichment = getattr(record, "enrichment", None)
    return {
        "stockId": record.stock_id,
        "stage": record.stage,
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
