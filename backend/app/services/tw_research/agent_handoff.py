"""Browser-only Agent JSON handoff helpers (#322).

Pure functions over already-fetched manifests/chunks. Never trigger upstream
provider calls. Stock Ticker never writes Notion; this module only helps an
external Agent decide which immutable chunks to copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.tw_research.constants import EXPORT_CHUNK_SIZE
from app.services.tw_research.export import (
    stable_chunk_checksum,
    stable_export_content_digest,
)

HandoffVerification = Literal[
    "stock_ticker_export_verified",
    "notion_published_verified",
    "notion_publish_not_performed",
    "auth_required",
    "incomplete_snapshot",
    "digest_mismatch",
    "unchanged_skip",
]

# Stock Ticker owns only the first status. Notion outcomes are external.
STOCK_TICKER_OWNED_STATUSES = frozenset(
    {
        "stock_ticker_export_verified",
        "auth_required",
        "incomplete_snapshot",
        "digest_mismatch",
        "unchanged_skip",
    }
)


@dataclass(frozen=True)
class ChunkTransferPlan:
    action: Literal["skip_all", "fetch_all", "fetch_changed", "abort"]
    reason: str
    snapshot_id: str | None
    content_digest: str | None
    chunk_indexes: tuple[int, ...]
    verification: HandoffVerification


def manifest_identity(manifest: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        _as_optional_str(manifest.get("snapshotId")),
        _as_optional_str(manifest.get("contentDigest")),
    )


def plan_chunk_transfer(
    *,
    previous_manifest: dict[str, Any] | None,
    current_manifest: dict[str, Any] | None,
    quality_status: str | None = None,
) -> ChunkTransferPlan:
    """Manifest-first transfer plan for a browser-only Agent.

    Unchanged ``contentDigest`` → skip all chunk GETs (zero Notion rewrites needed
    for the Stock Ticker side). Changed digests → fetch only differing chunk indexes
    when both manifests expose ``chunkDigests``; otherwise fetch all ledger chunks.
    """
    if current_manifest is None:
        return ChunkTransferPlan(
            action="abort",
            reason="no_verified_manifest",
            snapshot_id=None,
            content_digest=None,
            chunk_indexes=(),
            verification="incomplete_snapshot",
        )

    quality = quality_status or str(current_manifest.get("qualityStatus") or "")
    snapshot_id, content_digest_value = manifest_identity(current_manifest)
    if quality and quality != "verified_ready":
        return ChunkTransferPlan(
            action="abort",
            reason=f"quality_status={quality}",
            snapshot_id=snapshot_id,
            content_digest=content_digest_value,
            chunk_indexes=(),
            verification="incomplete_snapshot",
        )
    if not snapshot_id or not content_digest_value:
        return ChunkTransferPlan(
            action="abort",
            reason="manifest_missing_snapshot_or_digest",
            snapshot_id=snapshot_id,
            content_digest=content_digest_value,
            chunk_indexes=(),
            verification="incomplete_snapshot",
        )

    if previous_manifest is not None:
        _prev_id, prev_digest = manifest_identity(previous_manifest)
        if prev_digest and prev_digest == content_digest_value:
            # Digest match is authoritative for no-change skip (#322).
            return ChunkTransferPlan(
                action="skip_all",
                reason="content_digest_unchanged",
                snapshot_id=snapshot_id,
                content_digest=content_digest_value,
                chunk_indexes=(),
                verification="unchanged_skip",
            )

        changed = _changed_chunk_indexes(previous_manifest, current_manifest)
        if changed is not None:
            return ChunkTransferPlan(
                action="fetch_changed" if changed else "skip_all",
                reason="chunk_digests_diff" if changed else "chunk_digests_identical",
                snapshot_id=snapshot_id,
                content_digest=content_digest_value,
                chunk_indexes=tuple(changed),
                verification="stock_ticker_export_verified",
            )

    indexes = _all_chunk_indexes(current_manifest)
    return ChunkTransferPlan(
        action="fetch_all",
        reason="no_previous_manifest_or_chunk_map",
        snapshot_id=snapshot_id,
        content_digest=content_digest_value,
        chunk_indexes=indexes,
        verification="stock_ticker_export_verified",
    )


def verify_ledger_chunk(
    *,
    manifest: dict[str, Any],
    chunk: dict[str, Any],
) -> HandoffVerification:
    """Recalculate stable chunk checksum and compare to the immutable manifest map.

    Matches ``stable_chunk_checksum``: research rows + paging fields only
    (excludes ``scanId`` / ``generatedAt`` so Agent re-reads stay comparable).
    """
    index = chunk.get("chunkIndex")
    if index is None:
        return "digest_mismatch"
    rows = list(chunk.get("rows") or [])
    try:
        actual = stable_chunk_checksum(
            rows=rows,
            chunk_index=int(index),
            chunk_size=int(chunk.get("chunkSize") or EXPORT_CHUNK_SIZE),
            kind=str(chunk.get("kind") or "ledger"),
            trading_day=str(chunk.get("tradingDay") or manifest.get("tradingDay") or ""),
            total_rows=int(chunk.get("totalRows") or len(rows)),
        )
    except (TypeError, ValueError):
        return "digest_mismatch"
    expected_map = dict(manifest.get("chunkDigests") or {})
    expected = expected_map.get(str(index))
    # Manifest map is authoritative — missing index is a mismatch, not a soft pass.
    if expected is None or expected != actual:
        return "digest_mismatch"
    if chunk.get("checksumSha256") != actual:
        return "digest_mismatch"
    return "stock_ticker_export_verified"


def build_agent_transfer_example() -> dict[str, Any]:
    """Deterministic mock package for docs/tests (no live data, no Notion)."""
    frozen_at = "2024-06-28T09:00:00+00:00"
    rows = [
        {
            "stockId": "TW:2330",
            "stage": "B",
            "readiness": "verified_ready",
            "daily": {"passStatus": "pass", "asOf": "2024-06-28"},
            "monthly": {"passStatus": "pass", "asOf": "2024-05"},
            "themeKeys": ["tw-foundry"],
            "enrichment": {"qualified": True, "fields": {}},
        },
        {
            "stockId": "TW:2618",
            "stage": "B",
            "readiness": "verified_ready",
            "daily": {"passStatus": "pass", "asOf": "2024-06-28"},
            "monthly": {"passStatus": "pass", "asOf": "2024-05"},
            "themeKeys": [],
            "enrichment": None,
        },
        {
            "stockId": "TW:2727",
            "stage": "A",
            "readiness": "processed",
            "daily": {"passStatus": "fail", "asOf": "2024-06-28"},
            "monthly": None,
            "themeKeys": [],
            "enrichment": None,
        },
    ]
    from app.services.tw_research.export import build_export_chunk_from_rows

    chunk0 = build_export_chunk_from_rows(
        rows,
        chunk_index=0,
        chunk_size=25,
        kind="ledger",
        scan_id="example-scan",
        trading_day="2024-06-28",
        frozen_at=frozen_at,
    )
    chunk_digests = {"0": chunk0["checksumSha256"]}
    manifest = {
        "schemaVersion": "2026-11-09.1",
        "calculationVersion": "macd12-26-9_kd9-3-3_v1",
        "enrichmentVersion": "enrich-v1",
        "snapshotId": "example-snapshot",
        "scanId": "example-scan",
        "tradingDay": "2024-06-28",
        "lastCompleteMonth": "2024-05",
        "universeVersion": "explicit:3:example",
        "qualityStatus": "verified_ready",
        "finalizedAt": frozen_at,
        "generatedAt": frozen_at,
        "chunkDigests": chunk_digests,
        "chunkIds": ["0"],
        "counts": {
            "attempted": 3,
            "technicallyQualified": 2,
            "verifiedReady": 2,
            "enriched": 1,
        },
        "chunkPathTemplate": (
            "/api/tw-research/exports/snapshots/example-snapshot/chunk"
            "?chunkIndex={chunkIndex}&kind=ledger"
        ),
        "notes": [
            "Deterministic fixture for Agent transfer tests (#322).",
            "Stock Ticker never writes Notion.",
        ],
    }
    # Match production finalize digest shape (#319), not a nested-manifest hash.
    manifest["contentDigest"] = stable_export_content_digest(
        rows=rows,
        chunk_digests=chunk_digests,
        universe_version=str(manifest["universeVersion"]),
        trading_day=str(manifest["tradingDay"]),
        quality_status=str(manifest["qualityStatus"]),
    )
    plan = plan_chunk_transfer(previous_manifest=None, current_manifest=manifest)
    return {
        "contractVersion": "agent-handoff-v1",
        "stockTickerExportVerified": True,
        "notionPublishedVerified": False,
        "notionPublishNotPerformed": True,
        "statuses": {
            "stock_ticker_export_verified": "Owned by Stock Ticker after digest checks pass",
            "notion_published_verified": "Owned only by an external Agent after Notion read-back",
            "notion_publish_not_performed": "Default when Stock Ticker handoff completes",
        },
        "transferPlan": {
            "action": plan.action,
            "reason": plan.reason,
            "chunkIndexes": list(plan.chunk_indexes),
            "verification": plan.verification,
        },
        "manifest": manifest,
        "chunks": {"0": chunk0},
        "unchangedSecondRead": {
            "instruction": (
                "GET latest manifest again. If contentDigest matches, action=skip_all; "
                "do not re-GET chunks; do not rewrite Notion."
            ),
            "expectedAction": "skip_all",
            "expectedVerification": "unchanged_skip",
        },
        "retry": {
            "onDigestMismatch": "Re-GET the single chunkIndex; abort that chunk if still wrong",
            "onAuthRequired": "Re-authenticate the browser session; do not fall back to SSH/DB",
            "onIncompleteSnapshot": "Wait for verified_ready; never publish partial ledgers",
        },
    }


def classify_session_error(status_code: int) -> HandoffVerification:
    if status_code in {401, 403}:
        return "auth_required"
    if status_code == 404:
        return "incomplete_snapshot"
    if status_code == 409:
        return "digest_mismatch"
    return "incomplete_snapshot"


def _changed_chunk_indexes(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> list[int] | None:
    prev_map = previous.get("chunkDigests")
    curr_map = current.get("chunkDigests")
    if not isinstance(prev_map, dict) or not isinstance(curr_map, dict):
        return None
    keys = sorted({*prev_map.keys(), *curr_map.keys()}, key=lambda value: int(value))
    changed: list[int] = []
    for key in keys:
        if prev_map.get(key) != curr_map.get(key):
            changed.append(int(key))
    return changed


def _all_chunk_indexes(manifest: dict[str, Any]) -> tuple[int, ...]:
    digests = manifest.get("chunkDigests")
    if isinstance(digests, dict) and digests:
        return tuple(sorted(int(key) for key in digests.keys()))
    ids = manifest.get("chunkIds")
    if isinstance(ids, list) and ids:
        return tuple(sorted(int(item) for item in ids))
    return (0,)


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
