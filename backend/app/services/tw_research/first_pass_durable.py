"""Durable first-pass snapshots and analyst candidate sets (#335/#336).

First-pass evidence is persisted as an immutable Postgres row with a content
digest. Analysts submit a versioned compute subset pinned to that digest.
Backend never treats the subset as a Notion report list.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.first_pass import FirstPassBatchResult, dedupe_stock_ids
from app.services.tw_research.fundamentals_watchlist import ResearchMembershipUnavailable


CandidateOrigin = Literal["manual", "analyst"]


class FirstPassDurableUnavailable(RuntimeError):
    def __init__(self, message: str = "first_pass_durable_store_unavailable") -> None:
        super().__init__(message)
        self.operation = "first_pass_snapshot"


class CandidateSetConflict(Exception):
    def __init__(
        self,
        *,
        reason: str,
        current_version: int,
        expected_version: int,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_version = current_version
        self.expected_version = expected_version


class CandidateSetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class DurableFirstPassSnapshot:
    snapshot_id: str
    content_digest: str
    universe_version: str
    first_pass_version: str
    stock_ids: tuple[str, ...]
    created_at: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "contentDigest": self.content_digest,
            "universeVersion": self.universe_version,
            "firstPassVersion": self.first_pass_version,
            "stockIds": list(self.stock_ids),
            "createdAt": self.created_at,
            "durable": True,
        }


def content_digest_for_first_pass(result: FirstPassBatchResult) -> str:
    """Stable digest over first-pass records (excludes wall-clock notes)."""
    body = {
        "firstPassVersion": result.first_pass_version,
        "universeVersion": result.universe_version,
        "records": [record.as_dict() for record in result.records],
        "coverageSummary": result.coverage_summary,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def first_pass_snapshot_id(*, user_id: str, content_digest: str) -> str:
    """Globally unique, deterministic id scoped by owner + content digest (#335).

    Deduplication remains ``(user_id, content_digest)``. Using only the digest
    as the PK collided when two users persisted identical evidence.
    """
    material = f"{user_id}\0{content_digest}".encode("utf-8")
    return f"fp_{hashlib.sha256(material).hexdigest()[:40]}"


def persist_first_pass_batch(
    result: FirstPassBatchResult,
    *,
    user_id: str,
    repository: Any | None = None,
) -> DurableFirstPassSnapshot:
    repo = repository or tw_research_repository
    if not repo.is_available():
        raise FirstPassDurableUnavailable()
    digest = content_digest_for_first_pass(result)
    snapshot_id = first_pass_snapshot_id(user_id=user_id, content_digest=digest)
    stock_ids = [record.stock_id for record in result.records]
    payload = result.as_dict()
    payload["contentDigest"] = digest
    payload["persistedAt"] = datetime.now(UTC).isoformat()
    saved = repo.save_first_pass_snapshot(
        snapshot_id=snapshot_id,
        user_id=user_id,
        universe_version=result.universe_version,
        first_pass_version=result.first_pass_version,
        content_digest=digest,
        payload=payload,
        stock_ids=stock_ids,
    )
    return DurableFirstPassSnapshot(
        snapshot_id=saved["snapshot_id"],
        content_digest=saved["content_digest"],
        universe_version=saved["universe_version"],
        first_pass_version=saved["first_pass_version"],
        stock_ids=tuple(saved["stock_ids"]),
        created_at=str(saved["created_at"]),
        payload=dict(saved["payload"]),
    )


def submit_candidate_set(
    *,
    user_id: str,
    first_pass_snapshot_id: str,
    first_pass_digest: str,
    stock_ids: list[str],
    origin: CandidateOrigin,
    expected_version: int,
    selection_reasons: dict[str, Any] | None = None,
    decision_date: str | None = None,
    notes: list[str] | None = None,
    repository: Any | None = None,
) -> dict[str, Any]:
    """Persist analyst/manual compute subset pinned to a first-pass digest.

    Rejects IDs outside the first-pass snapshot universe. Never creates a
    Notion report list or ranking.
    """
    repo = repository or tw_research_repository
    if not repo.is_available():
        raise ResearchMembershipUnavailable("candidate_set_store_unavailable")

    snap = repo.get_first_pass_snapshot(first_pass_snapshot_id, user_id=user_id)
    if snap is None:
        raise KeyError("first_pass_snapshot_not_found")
    if snap["content_digest"] != first_pass_digest:
        raise CandidateSetValidationError("first_pass_digest_mismatch")

    allowed = set(snap["stock_ids"])
    deduped = dedupe_stock_ids(stock_ids)
    rejected = [sid for sid in deduped if sid not in allowed]
    if rejected:
        raise CandidateSetValidationError(
            f"stock_ids_outside_first_pass_universe:{','.join(rejected[:8])}"
        )

    candidate_set_id = f"cs_{uuid.uuid4().hex[:24]}"
    try:
        saved = repo.save_candidate_set(
            candidate_set_id=candidate_set_id,
            user_id=user_id,
            expected_version=expected_version,
            first_pass_snapshot_id=first_pass_snapshot_id,
            first_pass_digest=first_pass_digest,
            stock_ids=deduped,
            origin=origin,
            selection_reasons=dict(selection_reasons or {}),
            decision_date=decision_date,
            notes=list(notes or [])
            + [
                "Analyst/manual compute subset only — not a Notion report list.",
                "Changing selection creates a new version; historical sets remain.",
            ],
        )
    except ValueError as exc:
        if "version_mismatch" in str(exc):
            latest = repo.latest_candidate_set(user_id)
            raise CandidateSetConflict(
                reason="version_mismatch",
                current_version=int(latest["version"]) if latest else 0,
                expected_version=expected_version,
            ) from exc
        raise CandidateSetValidationError(str(exc)) from exc

    return {
        "candidateSetId": saved["candidate_set_id"],
        "version": saved["version"],
        "firstPassSnapshotId": saved["first_pass_snapshot_id"],
        "firstPassDigest": saved["first_pass_digest"],
        "stockIds": list(saved["stock_ids"]),
        "origin": saved["origin"],
        "selectionReasons": dict(saved["selection_reasons"]),
        "decisionDate": saved["decision_date"],
        "notes": list(saved["notes"]),
        "createdAt": saved["created_at"],
        "durable": True,
        "notNotionReportList": True,
    }


def latest_candidate_set(*, user_id: str, repository: Any | None = None) -> dict[str, Any] | None:
    repo = repository or tw_research_repository
    if not repo.is_available():
        raise ResearchMembershipUnavailable("candidate_set_store_unavailable")
    saved = repo.latest_candidate_set(user_id)
    if saved is None:
        return None
    return {
        "candidateSetId": saved["candidate_set_id"],
        "version": saved["version"],
        "firstPassSnapshotId": saved["first_pass_snapshot_id"],
        "firstPassDigest": saved["first_pass_digest"],
        "stockIds": list(saved["stock_ids"]),
        "origin": saved["origin"],
        "selectionReasons": dict(saved["selection_reasons"]),
        "decisionDate": saved["decision_date"],
        "notes": list(saved["notes"]),
        "createdAt": saved["created_at"],
        "durable": True,
        "notNotionReportList": True,
    }


def create_technical_scan_from_candidate_set(
    *,
    user_id: str,
    expected_version: int | None = None,
    candidate_set_id: str | None = None,
    target_count: int = 10,
    trading_day: str | None = None,
    max_upstream_month_fetches: int | None = None,
    max_enrichments_per_run: int | None = None,
    repository: Any | None = None,
    scan_service: Any | None = None,
) -> dict[str, Any]:
    """Create a monthly/daily MACD/KD scan from an approved candidate set (#336).

    Consumes only the explicitly selected candidate IDs. Does not publish Notion
    report recommendations or expand the universe silently.
    """
    from app.services.tw_research.constants import (
        DEFAULT_MAX_ENRICHMENTS_PER_RUN,
        DEFAULT_MAX_UPSTREAM_MONTH_FETCHES,
    )
    from app.services.tw_research.scan import tw_research_scan_service
    from app.services.tw_research.universe import UniverseSelector

    repo = repository or tw_research_repository
    if not repo.is_available():
        raise ResearchMembershipUnavailable("candidate_set_store_unavailable")

    saved = repo.latest_candidate_set(user_id)
    if saved is None:
        raise KeyError("candidate_set_not_found")
    if candidate_set_id and str(saved["candidate_set_id"]) != str(candidate_set_id):
        # Historical versions remain immutable; technical scan uses the approved
        # latest set unless the caller pins expected_version below.
        raise CandidateSetValidationError(
            "candidate_set_id_not_latest;pass_expected_version_to_pin_latest_approval"
        )

    if expected_version is not None and int(saved["version"]) != int(expected_version):
        raise CandidateSetConflict(
            reason="version_mismatch",
            current_version=int(saved["version"]),
            expected_version=int(expected_version),
        )

    stock_ids = list(saved["stock_ids"] or [])
    if not stock_ids:
        raise CandidateSetValidationError("empty_candidate_set")

    service = scan_service or tw_research_scan_service
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=tuple(stock_ids)),
        target_count=target_count,
        trading_day=trading_day,
        max_upstream_month_fetches=(
            DEFAULT_MAX_UPSTREAM_MONTH_FETCHES
            if max_upstream_month_fetches is None
            else max_upstream_month_fetches
        ),
        max_enrichments_per_run=(
            DEFAULT_MAX_ENRICHMENTS_PER_RUN
            if max_enrichments_per_run is None
            else max_enrichments_per_run
        ),
        user_id=user_id,
    )
    # Stamp provenance onto scan notes without mutating candidate set.
    state.notes = list(state.notes or []) + [
        (
            "Technical second-pass sourced from candidate set "
            f"{saved['candidate_set_id']} v{saved['version']} "
            f"(firstPassSnapshotId={saved['first_pass_snapshot_id']})."
        ),
        "Monthly MACD/KD evaluated before daily; universe is the approved candidate IDs only.",
        "Not a Notion report list — final report selection remains external.",
    ]
    try:
        service._persist_scan(state)  # noqa: SLF001 — reuse durable scan write path
    except Exception:
        # In-memory fallback still returns the scan; durable note persist is best-effort.
        pass
    return {
        "scan": state,
        "candidateSetId": saved["candidate_set_id"],
        "candidateSetVersion": saved["version"],
        "firstPassSnapshotId": saved["first_pass_snapshot_id"],
        "firstPassDigest": saved["first_pass_digest"],
        "stockIds": list(state.stock_ids),
        "notNotionReportList": True,
    }
