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
    "verification_required",
    "stock_ticker_export_verified",
    "notion_published_verified",
    "notion_publish_not_performed",
    "auth_required",
    "incomplete_snapshot",
    "digest_mismatch",
    "unchanged_skip",
]

# Planner and checksum helpers. Notion outcomes are external and are not listed.
STOCK_TICKER_OWNED_STATUSES = frozenset(
    {
        "verification_required",
        "stock_ticker_export_verified",
        "auth_required",
        "incomplete_snapshot",
        "digest_mismatch",
        "unchanged_skip",
    }
)

_VERIFIED_QUALITY = "verified_ready"


@dataclass(frozen=True)
class ChunkTransferPlan:
    """Pure description of what an external consumer should transfer.

    ``removed_chunk_indexes`` are absent from the current snapshot. The consumer
    drops them locally. Stock Ticker must not be asked to serve them, and this
    plan does not persist the consumer's checkpoint.

    ``verification`` is the plan-time status. Pending transfers use
    ``verification_required``. ``stock_ticker_export_verified`` is not a plan
    result; the consumer records it only after ``verify_ledger_chunk`` or
    ``verify_cached_chunk_digests`` succeeds.
    """

    action: Literal["skip_all", "manifest_only", "fetch_all", "fetch_changed", "abort"]
    reason: str
    snapshot_id: str | None
    content_digest: str | None
    chunk_indexes: tuple[int, ...]
    removed_chunk_indexes: tuple[int, ...]
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

    The result describes work. It does not mean the consumer has finished it.
    ``fetch_all``, ``fetch_changed``, and ``manifest_only`` use
    ``verification_required``. ``stock_ticker_export_verified`` is returned only
    by ``verify_ledger_chunk`` or ``verify_cached_chunk_digests``.

    * Unchanged ``contentDigest`` → ``skip_all``. This assumes the consumer
      still holds the previous snapshot from a successful verification. It does
      not re-check chunks.
    * Changed digest with an identical ``chunkDigests`` map → ``manifest_only``.
      Before advancing its snapshot pointer, the consumer must confirm every
      retained chunk checksum still matches that map.
    * Added or modified chunk digests → ``fetch_changed`` for those indexes only.
      Indexes that exist only in the previous map are ``removed_chunk_indexes``
      and are never requested.
    * No usable previous manifest or chunk map → ``fetch_all``.

    ``quality_status`` is optional caller context, such as the snapshot row
    column. A blank value is omitted. It does not replace ``qualityStatus`` on
    the manifest. When both are present they must be identical, or the plan
    aborts with ``quality_status_conflict``. A snapshot is accepted only when
    the effective status is exactly ``verified_ready``. Ids and digests do not
    imply that status. A verified snapshot with an empty chunk map is valid.

    The function is pure: it does not record whether any Agent has read the
    snapshot, and it does not store Notion or checkpoint state.
    """
    if current_manifest is None:
        return ChunkTransferPlan(
            action="abort",
            reason="no_verified_manifest",
            snapshot_id=None,
            content_digest=None,
            chunk_indexes=(),
            removed_chunk_indexes=(),
            verification="incomplete_snapshot",
        )

    quality_reason = _verified_quality_reason(current_manifest, quality_status)
    snapshot_id, content_digest_value = manifest_identity(current_manifest)
    if quality_reason is not None:
        return ChunkTransferPlan(
            action="abort",
            reason=quality_reason,
            snapshot_id=snapshot_id,
            content_digest=content_digest_value,
            chunk_indexes=(),
            removed_chunk_indexes=(),
            verification="incomplete_snapshot",
        )
    if not snapshot_id or not content_digest_value:
        return ChunkTransferPlan(
            action="abort",
            reason="manifest_missing_snapshot_or_digest",
            snapshot_id=snapshot_id,
            content_digest=content_digest_value,
            chunk_indexes=(),
            removed_chunk_indexes=(),
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
                removed_chunk_indexes=(),
                verification="unchanged_skip",
            )

        delta = _chunk_digest_delta(previous_manifest, current_manifest)
        if delta is not None:
            fetch_indexes, removed_indexes = delta
            if not fetch_indexes and not removed_indexes:
                return ChunkTransferPlan(
                    action="manifest_only",
                    reason="content_digest_changed_chunks_unchanged",
                    snapshot_id=snapshot_id,
                    content_digest=content_digest_value,
                    chunk_indexes=(),
                    removed_chunk_indexes=(),
                    verification="verification_required",
                )
            return ChunkTransferPlan(
                action="fetch_changed",
                reason="chunk_digests_diff" if fetch_indexes else "chunks_removed",
                snapshot_id=snapshot_id,
                content_digest=content_digest_value,
                chunk_indexes=tuple(fetch_indexes),
                removed_chunk_indexes=tuple(removed_indexes),
                verification="verification_required",
            )

    indexes = _all_chunk_indexes(current_manifest)
    return ChunkTransferPlan(
        action="fetch_all",
        reason="no_previous_manifest_or_chunk_map",
        snapshot_id=snapshot_id,
        content_digest=content_digest_value,
        chunk_indexes=indexes,
        removed_chunk_indexes=(),
        verification="verification_required",
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
    if _verified_quality_reason(manifest, None) is not None:
        return "incomplete_snapshot"
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


def verify_cached_chunk_digests(
    *,
    manifest: dict[str, Any],
    cached_checksums: dict[Any, Any],
) -> HandoffVerification:
    """Confirm retained chunk checksums match an unchanged digest map.

    ``manifest_only`` stays ``verification_required`` until this returns
    ``stock_ticker_export_verified``. The consumer may then advance its snapshot
    pointer. This does not fetch chunks and does not store consumer state.
    """
    if _verified_quality_reason(manifest, None) is not None:
        return "incomplete_snapshot"
    snapshot_id, content_digest_value = manifest_identity(manifest)
    if not snapshot_id or not content_digest_value:
        return "incomplete_snapshot"
    expected = _checksum_map(manifest.get("chunkDigests"))
    cached = _checksum_map(cached_checksums)
    if expected is None or cached is None or expected != cached:
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
        "stockTickerExportVerified": False,
        "notionPublishedVerified": False,
        "notionPublishNotPerformed": True,
        "statuses": {
            "verification_required": (
                "Transfer plan only. The consumer has not finished checksum checks"
            ),
            "stock_ticker_export_verified": (
                "Recorded by the consumer after manifest and chunk checksum checks pass"
            ),
            "notion_published_verified": "Owned only by an external Agent after Notion read-back",
            "notion_publish_not_performed": "Default when Stock Ticker handoff completes",
        },
        "transferPlan": {
            "action": plan.action,
            "reason": plan.reason,
            "chunkIndexes": list(plan.chunk_indexes),
            "removedChunkIndexes": list(plan.removed_chunk_indexes),
            "verification": plan.verification,
            "completedVerification": None,
        },
        "manifest": manifest,
        "chunks": {"0": chunk0},
        "unchangedSecondRead": {
            "instruction": (
                "GET latest manifest again. If contentDigest matches, action=skip_all. "
                "This assumes the consumer retained the previous snapshot after a "
                "successful verification. Do not re-GET chunks. Do not rewrite Notion."
            ),
            "expectedAction": "skip_all",
            "expectedVerification": "unchanged_skip",
        },
        "manifestOnlyRead": {
            "instruction": (
                "If contentDigest changes while every chunkDigests entry is unchanged, "
                "action=manifest_only and verification=verification_required. Confirm "
                "retained chunk checksums match chunkDigests, then update the snapshot "
                "pointer. Do not re-GET chunks. Drop removedChunkIndexes locally."
            ),
            "expectedAction": "manifest_only",
            "expectedChunkIndexes": [],
            "expectedRemovedChunkIndexes": [],
        },
        "retry": {
            "onDigestMismatch": "Re-GET the single chunkIndex; abort that chunk if still wrong",
            "onAuthRequired": "Re-authenticate the browser session; do not fall back to SSH/DB",
            "onIncompleteSnapshot": "Wait for verified_ready; never publish partial ledgers",
            "onTemporaryReadFailure": (
                "Retry that chunk GET. Do not replace a previously verified chunk "
                "until the new checksum matches."
            ),
        },
    }


def _verified_quality_reason(
    manifest: dict[str, Any],
    quality_status: str | None,
) -> str | None:
    """Abort reason when the snapshot is not explicitly ``verified_ready``.

    Returns None only when the effective status is exactly ``verified_ready``.
    """
    manifest_quality = _as_optional_str(manifest.get("qualityStatus"))
    argument_quality = _as_optional_str(quality_status)
    if (
        manifest_quality is not None
        and argument_quality is not None
        and manifest_quality != argument_quality
    ):
        return "quality_status_conflict"
    effective = argument_quality if argument_quality is not None else manifest_quality
    if effective != _VERIFIED_QUALITY:
        return f"quality_status={effective or 'missing'}"
    return None


def _checksum_map(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    mapped: dict[str, str] = {}
    for key, digest in value.items():
        index = _as_optional_str(key)
        checksum = _as_optional_str(digest)
        if index is None or checksum is None:
            return None
        mapped[index] = checksum
    return mapped


def classify_session_error(status_code: int) -> HandoffVerification:
    if status_code in {401, 403}:
        return "auth_required"
    if status_code == 404:
        return "incomplete_snapshot"
    if status_code == 409:
        return "digest_mismatch"
    return "incomplete_snapshot"


def _chunk_digest_delta(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[list[int], list[int]] | None:
    """Return ``(fetch_indexes, removed_indexes)``.

    ``None`` means either manifest has no usable ``chunkDigests`` map, so the
    caller must fetch every current chunk. Removed indexes are previous keys
    that the current snapshot no longer publishes.
    """
    prev_map = previous.get("chunkDigests")
    curr_map = current.get("chunkDigests")
    if not isinstance(prev_map, dict) or not isinstance(curr_map, dict):
        return None
    try:
        current_keys = sorted(curr_map.keys(), key=lambda value: int(value))
        previous_keys = sorted(prev_map.keys(), key=lambda value: int(value))
    except (TypeError, ValueError):
        return None
    fetch_indexes = [
        int(key) for key in current_keys if prev_map.get(key) != curr_map.get(key)
    ]
    removed_indexes = [int(key) for key in previous_keys if key not in curr_map]
    return fetch_indexes, removed_indexes


def _all_chunk_indexes(manifest: dict[str, Any]) -> tuple[int, ...]:
    digests = manifest.get("chunkDigests")
    if isinstance(digests, dict):
        try:
            return tuple(sorted(int(key) for key in digests.keys()))
        except (TypeError, ValueError):
            return ()
    ids = manifest.get("chunkIds")
    if isinstance(ids, list) and ids:
        try:
            return tuple(sorted(int(item) for item in ids))
        except (TypeError, ValueError):
            return (0,)
    return (0,)


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
