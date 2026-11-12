"""Deterministic first-pass snapshot chunking + checksums (#347).

Keeps chunks bounded (25 records). Digests hash the checksum envelope + rows so
an external analyst can independently recompute SHA-256 without trusting the
server-supplied ``checksumSha256`` alone.
"""

from __future__ import annotations

from typing import Any

from app.services.tw_research.constants import (
    EXPORT_CHUNK_SIZE,
    FIRST_PASS_VERSION,
    RESEARCH_SCHEMA_VERSION,
)
from app.services.tw_research.chip_evidence import CHIP_FIELD_KEYS, summarize_chip_coverage
from app.services.tw_research.export import canonical_json_dumps, content_digest, stable_chunk_checksum

FIRST_PASS_TRADING_DAY_FALLBACK = "first_pass_snapshot"


def ordered_first_pass_records(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [row for row in (records or []) if isinstance(row, dict)]
    return sorted(
        rows,
        key=lambda row: str(row.get("stockId") or row.get("stock_id") or ""),
    )


def resolve_first_pass_trading_day(
    *,
    payload: dict[str, Any] | None = None,
    trading_day: str | None = None,
) -> str:
    """Stable snapshot-level trading day for checksums (not first-record hint)."""
    if isinstance(trading_day, str) and trading_day.strip():
        return trading_day.strip()[:10]
    payload = payload or {}
    for key in ("tradingDay", "trading_day"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:10]
    return FIRST_PASS_TRADING_DAY_FALLBACK


def checksum_envelope(
    *,
    trading_day: str,
    chunk_index: int,
    chunk_size: int,
    total_rows: int,
) -> dict[str, Any]:
    total_chunks = (total_rows + chunk_size - 1) // chunk_size if total_rows else 0
    return {
        "schemaVersion": RESEARCH_SCHEMA_VERSION,
        "kind": "first_pass_ledger",
        "tradingDay": trading_day,
        "chunkIndex": chunk_index,
        "chunkSize": chunk_size,
        "totalRows": total_rows,
        "totalChunks": total_chunks,
    }


def recompute_chunk_digest(
    *,
    envelope: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    """Independent verifier: sha256(canonical_json({...envelope, rows}))."""
    return content_digest({**envelope, "rows": rows})


def build_first_pass_manifest(
    *,
    snapshot_id: str,
    content_digest: str,
    universe_version: str | None,
    first_pass_version: str | None,
    records: list[dict[str, Any]],
    chunk_size: int = EXPORT_CHUNK_SIZE,
    trading_day: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = ordered_first_pass_records(records)
    day = resolve_first_pass_trading_day(payload=payload, trading_day=trading_day)
    chunk_digests = first_pass_chunk_digests(
        ordered,
        trading_day=day,
        chunk_size=chunk_size,
    )
    coverage = summarize_chip_coverage(ordered)
    chunk_count = len(chunk_digests)
    return {
        "snapshotId": snapshot_id,
        "contentDigest": content_digest,
        "universeVersion": universe_version,
        "firstPassVersion": first_pass_version or FIRST_PASS_VERSION,
        "tradingDay": day,
        "recordCount": len(ordered),
        "chunkSize": chunk_size,
        "chunkCount": chunk_count if ordered else 0,
        "chunkDigests": chunk_digests,
        "chunkPathTemplate": (
            f"/api/tw-research/first-pass/snapshots/{snapshot_id}/chunk"
            "?chunkIndex={chunkIndex}"
        ),
        "requiredChipFields": list(CHIP_FIELD_KEYS),
        "chipCoverage": coverage,
        "stableOrdering": "stockId_asc",
        "notNotionReportList": True,
        "notes": [
            "Retrieve and checksum-verify every chunk before Decision 1 or 2.",
            "Recompute sha256(canonical_json({checksumEnvelope..., rows})) — do not trust checksumSha256 alone.",
            "Chip fields must remain present even when status=unavailable/partial.",
            "A link-only evidence pack is not proof the analyst consumed all chunks.",
            "Empty snapshot ⇒ chunkCount=0 (not 1). Rows are sorted by stockId.",
        ],
    }


def build_first_pass_chunk(
    *,
    snapshot_id: str,
    content_digest: str,
    records: list[dict[str, Any]],
    chunk_index: int,
    chunk_size: int = EXPORT_CHUNK_SIZE,
    trading_day: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if chunk_index < 0:
        raise ValueError("chunk_index must be >= 0")
    ordered = ordered_first_pass_records(records)
    total = len(ordered)
    start = chunk_index * chunk_size
    if start >= total and chunk_index > 0:
        raise IndexError("chunk_out_of_range")
    end = start + chunk_size
    rows = ordered[start:end]
    day = resolve_first_pass_trading_day(payload=payload, trading_day=trading_day)
    envelope = checksum_envelope(
        trading_day=day,
        chunk_index=chunk_index,
        chunk_size=chunk_size,
        total_rows=total,
    )
    checksum = stable_chunk_checksum(
        rows=rows,
        chunk_index=chunk_index,
        chunk_size=chunk_size,
        kind="first_pass_ledger",
        trading_day=day,
        total_rows=total,
    )
    # Guard: stable_chunk_checksum must equal independent envelope recompute.
    assert checksum == recompute_chunk_digest(envelope=envelope, rows=rows)
    return {
        "snapshotId": snapshot_id,
        "contentDigest": content_digest,
        "tradingDay": day,
        "chunkIndex": chunk_index,
        "chunkSize": chunk_size,
        "totalRows": total,
        "totalChunks": envelope["totalChunks"],
        "records": rows,
        "stockIds": [
            str(row.get("stockId") or row.get("stock_id")) for row in rows
        ],
        "checksumEnvelope": envelope,
        "checksumSha256": checksum,
        "truncated": end < total,
        "requiredChipFields": list(CHIP_FIELD_KEYS),
        "chipCoverage": summarize_chip_coverage(rows),
        "notNotionReportList": True,
    }


def first_pass_chunk_digests(
    records: list[dict[str, Any]],
    *,
    trading_day: str,
    chunk_size: int = EXPORT_CHUNK_SIZE,
) -> dict[str, str]:
    ordered = ordered_first_pass_records(records)
    total = len(ordered)
    if total == 0:
        return {}
    digests: dict[str, str] = {}
    chunk_count = (total + chunk_size - 1) // chunk_size
    for index in range(chunk_count):
        start = index * chunk_size
        rows = ordered[start : start + chunk_size]
        digests[str(index)] = stable_chunk_checksum(
            rows=rows,
            chunk_index=index,
            chunk_size=chunk_size,
            kind="first_pass_ledger",
            trading_day=trading_day,
            total_rows=total,
        )
    return digests


def verify_first_pass_chunks_consumed(
    *,
    manifest: dict[str, Any],
    chunks: list[dict[str, Any]],
    expected_stock_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Simulate external consumer verifying all first-pass chunks (#347).

    Recomputes ``sha256(canonical_json({...envelope, rows}))`` and compares to
    ``manifest.chunkDigests[i]``. Also rejects duplicate chunk indexes / stockIds
    and checks the union of stockIds against the manifest/snapshot when provided.
    """
    expected = dict(manifest.get("chunkDigests") or {})
    record_count = int(manifest.get("recordCount") or 0)
    trading_day = str(
        manifest.get("tradingDay") or FIRST_PASS_TRADING_DAY_FALLBACK
    )
    chunk_size = int(manifest.get("chunkSize") or EXPORT_CHUNK_SIZE)
    seen_indexes: set[str] = set()
    seen_ids: list[str] = []
    errors: list[str] = []
    verified = 0

    for chunk in chunks:
        index = str(chunk.get("chunkIndex"))
        if index in seen_indexes:
            errors.append(f"duplicate_chunk_index:{index}")
            continue
        seen_indexes.add(index)
        expected_digest = expected.get(index)
        if expected_digest is None:
            errors.append(f"unexpected_chunk_index:{index}")
            continue

        rows = list(chunk.get("records") or [])
        envelope = chunk.get("checksumEnvelope")
        if not isinstance(envelope, dict):
            envelope = checksum_envelope(
                trading_day=trading_day,
                chunk_index=int(chunk.get("chunkIndex") or 0),
                chunk_size=int(chunk.get("chunkSize") or chunk_size),
                total_rows=int(chunk.get("totalRows") or record_count),
            )
        recomputed = recompute_chunk_digest(envelope=envelope, rows=rows)
        declared = chunk.get("checksumSha256")
        if recomputed != expected_digest:
            errors.append(f"digest_mismatch:{index}")
            continue
        if declared is not None and declared != expected_digest:
            errors.append(f"digest_mismatch:{index}")
            continue
        verified += 1

        for stock_id in chunk.get("stockIds") or []:
            sid = str(stock_id)
            if sid in seen_ids:
                errors.append(f"duplicate_stock_id:{sid}")
            seen_ids.append(sid)
        for record in rows:
            fields = record.get("fields") if isinstance(record, dict) else None
            if not isinstance(fields, dict):
                errors.append(
                    f"chip_fields_missing:{record.get('stockId') if isinstance(record, dict) else '?'}"
                )
                continue
            for key in CHIP_FIELD_KEYS:
                if key not in fields:
                    errors.append(
                        f"chip_field_absent:{record.get('stockId')}:{key}"
                    )

    missing_indexes = sorted(
        set(expected) - seen_indexes,
        key=lambda value: int(value),
    )
    if missing_indexes:
        errors.append(f"chunks_not_consumed:{','.join(missing_indexes)}")
    if len(seen_ids) != record_count:
        errors.append(
            f"record_count_mismatch:expected={record_count}:got={len(seen_ids)}"
        )

    expected_ids = expected_stock_ids
    if expected_ids is None and manifest.get("stockIds"):
        expected_ids = [str(item) for item in manifest.get("stockIds") or []]
    if expected_ids is not None:
        if set(seen_ids) != set(expected_ids):
            errors.append("stock_id_union_mismatch")

    return {
        "ok": not errors,
        "verifiedChunkCount": verified,
        "expectedChunkCount": len(expected),
        "uniqueStockIds": len(set(seen_ids)),
        "errors": errors,
        "canonicalization": {
            "dumps": 'json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)',
            "hash": "sha256 hex digest",
            "envelopeKeys": list(checksum_envelope(
                trading_day=trading_day,
                chunk_index=0,
                chunk_size=chunk_size,
                total_rows=record_count,
            ).keys()),
            "example": canonical_json_dumps({"note": "see docs/tw-research-agent-handoff.md"}),
        },
        "status": "stock_ticker_first_pass_export_verified"
        if not errors
        else "verification_failed",
    }
