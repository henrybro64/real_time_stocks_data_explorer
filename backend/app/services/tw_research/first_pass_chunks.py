"""Deterministic first-pass snapshot chunking + checksums (#347).

Keeps chunks bounded (25 records). Digests hash research rows only — no
timestamps — so an external analyst can verify every chip field was retrieved.
"""

from __future__ import annotations

from typing import Any

from app.services.tw_research.constants import EXPORT_CHUNK_SIZE, FIRST_PASS_VERSION
from app.services.tw_research.chip_evidence import CHIP_FIELD_KEYS, summarize_chip_coverage
from app.services.tw_research.export import stable_chunk_checksum


def ordered_first_pass_records(records: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [row for row in (records or []) if isinstance(row, dict)]
    return sorted(
        rows,
        key=lambda row: str(row.get("stockId") or row.get("stock_id") or ""),
    )


def build_first_pass_manifest(
    *,
    snapshot_id: str,
    content_digest: str,
    universe_version: str | None,
    first_pass_version: str | None,
    records: list[dict[str, Any]],
    chunk_size: int = EXPORT_CHUNK_SIZE,
) -> dict[str, Any]:
    ordered = ordered_first_pass_records(records)
    chunk_digests = first_pass_chunk_digests(
        ordered,
        trading_day=_trading_day_hint(ordered),
        chunk_size=chunk_size,
    )
    coverage = summarize_chip_coverage(ordered)
    chunk_count = len(chunk_digests)
    return {
        "snapshotId": snapshot_id,
        "contentDigest": content_digest,
        "universeVersion": universe_version,
        "firstPassVersion": first_pass_version or FIRST_PASS_VERSION,
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
            "Chip fields must remain present even when status=unavailable/partial.",
            "A link-only evidence pack is not proof the analyst consumed all chunks.",
        ],
    }


def build_first_pass_chunk(
    *,
    snapshot_id: str,
    content_digest: str,
    records: list[dict[str, Any]],
    chunk_index: int,
    chunk_size: int = EXPORT_CHUNK_SIZE,
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
    trading_day = _trading_day_hint(ordered)
    checksum = stable_chunk_checksum(
        rows=rows,
        chunk_index=chunk_index,
        chunk_size=chunk_size,
        kind="first_pass_ledger",
        trading_day=trading_day,
        total_rows=total,
    )
    return {
        "snapshotId": snapshot_id,
        "contentDigest": content_digest,
        "chunkIndex": chunk_index,
        "chunkSize": chunk_size,
        "totalRows": total,
        "totalChunks": (total + chunk_size - 1) // chunk_size if total else 0,
        "records": rows,
        "stockIds": [
            str(row.get("stockId") or row.get("stock_id")) for row in rows
        ],
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
) -> dict[str, Any]:
    """Simulate external consumer verifying all first-pass chunks (#347)."""
    expected = dict(manifest.get("chunkDigests") or {})
    record_count = int(manifest.get("recordCount") or 0)
    seen_ids: list[str] = []
    errors: list[str] = []
    verified = 0
    for chunk in chunks:
        index = str(chunk.get("chunkIndex"))
        expected_digest = expected.get(index)
        actual = chunk.get("checksumSha256")
        if expected_digest is None:
            errors.append(f"unexpected_chunk_index:{index}")
            continue
        if actual != expected_digest:
            errors.append(f"digest_mismatch:{index}")
            continue
        verified += 1
        for stock_id in chunk.get("stockIds") or []:
            seen_ids.append(str(stock_id))
        for record in chunk.get("records") or []:
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
        set(expected) - {str(chunk.get("chunkIndex")) for chunk in chunks},
        key=lambda value: int(value),
    )
    if missing_indexes:
        errors.append(f"chunks_not_consumed:{','.join(missing_indexes)}")
    if len(seen_ids) != record_count:
        errors.append(
            f"record_count_mismatch:expected={record_count}:got={len(seen_ids)}"
        )
    return {
        "ok": not errors,
        "verifiedChunkCount": verified,
        "expectedChunkCount": len(expected),
        "uniqueStockIds": len(set(seen_ids)),
        "errors": errors,
        "status": "stock_ticker_first_pass_export_verified"
        if not errors
        else "verification_failed",
    }


def _trading_day_hint(records: list[dict[str, Any]]) -> str:
    for record in records:
        for key in ("tradingDay", "trading_day", "asOf", "as_of"):
            value = record.get(key)
            if isinstance(value, str) and value:
                return value[:10]
        fields = record.get("fields")
        if isinstance(fields, dict):
            close = fields.get("latest_completed_close")
            if isinstance(close, dict) and close.get("source_as_of"):
                return str(close["source_as_of"])[:10]
    return "unknown"
