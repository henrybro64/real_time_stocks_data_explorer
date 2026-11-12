"""PostgreSQL atomic candidate approval for managed research runs (#345 P1).

Requires disposable DATABASE_URL (stocks_test / *_test) + ALLOW_DESTRUCTIVE_DB_TESTS.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import TwResearchCandidateSet, TwResearchResearchRun
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
from app.services.tw_research.first_pass_durable import (
    CandidateSetConflict,
    persist_first_pass_batch,
    submit_candidate_set,
    submit_candidate_set_for_research_run,
)
from app.services.tw_research.research_run import (
    ResearchRunGateError,
    ResearchRunStore,
)


pytestmark = pytest.mark.integration


def _fresh_store() -> ResearchRunStore:
    return ResearchRunStore(repository=tw_research_repository)


def _seed_snapshot(user_id: str, stock_ids: tuple[str, ...] = ("TW:2330", "TW:2454")):
    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version=f"atomic-{uuid4().hex[:8]}",
        requested_count=len(stock_ids),
        collected_count=len(stock_ids),
        deduped_count=len(stock_ids),
        records=[
            FirstPassSymbolRecord(
                stock_id=sid,
                stock_name=sid,
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=[],
            )
            for sid in stock_ids
        ],
        coverage_summary={},
        notes=[],
    )
    return persist_first_pass_batch(
        batch, user_id=user_id, repository=tw_research_repository
    )


def _gate_run(*, user_id: str, snapshot_id: str) -> None:
    store = _fresh_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchResearchRun, user_id)
        assert row is not None
        row.phase = "awaiting_analyst_candidate_selection"
        row.status = "gated"
        row.first_pass_snapshot_id = snapshot_id
        row.candidate_set_id = None
        row.candidate_set_version = None
        row.gates_json = {
            "candidateSelection": "awaiting_analyst",
            "reportDecision": "blocked_until_phase2",
            "notionPublication": "external_only_never_auto",
        }
        row.revision = int(row.revision or 0) + 1
        session.commit()


def test_valid_candidate_approval_commits_both_records(db_session) -> None:
    del db_session
    user_id = f"ok-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    saved = submit_candidate_set_for_research_run(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2330"],
        origin="analyst",
        expected_version=0,
    )
    assert saved["approvalOutcome"] == "approved"
    assert int(saved["version"]) == 1

    run = _fresh_store().get(user_id)
    assert run is not None
    assert run.candidate_set_id == saved["candidateSetId"]
    assert run.candidate_set_version == 1
    assert run.gates.get("candidateSelection") == "approved"
    assert run.first_pass_snapshot_id == snap.snapshot_id

    latest = tw_research_repository.latest_candidate_set(user_id)
    assert latest is not None
    assert latest["candidate_set_id"] == saved["candidateSetId"]
    assert latest["first_pass_snapshot_id"] == snap.snapshot_id


def test_snapshot_mismatch_commits_neither(db_session) -> None:
    del db_session
    user_id = f"snap-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    other = _seed_snapshot(user_id, stock_ids=("TW:2303", "TW:2317"))
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    with pytest.raises(ResearchRunGateError, match="snapshot_mismatch"):
        submit_candidate_set_for_research_run(
            user_id=user_id,
            first_pass_snapshot_id=other.snapshot_id,
            first_pass_digest=other.content_digest,
            stock_ids=["TW:2303"],
            origin="analyst",
            expected_version=0,
        )

    assert tw_research_repository.latest_candidate_set(user_id) is None
    run = _fresh_store().get(user_id)
    assert run is not None
    assert run.candidate_set_id is None
    assert run.gates.get("candidateSelection") == "awaiting_analyst"


def test_concurrent_approvals_single_authoritative(db_session) -> None:
    del db_session
    user_id = f"race-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    def _attempt(stock_id: str) -> str:
        try:
            saved = submit_candidate_set_for_research_run(
                user_id=user_id,
                first_pass_snapshot_id=snap.snapshot_id,
                first_pass_digest=snap.content_digest,
                stock_ids=[stock_id],
                origin="analyst",
                expected_version=0,
            )
            return f"ok:{saved['candidateSetId']}:{saved['version']}"
        except (CandidateSetConflict, ResearchRunGateError) as exc:
            return f"err:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_attempt, "TW:2330"),
            pool.submit(_attempt, "TW:2454"),
        ]
        results = [fut.result() for fut in as_completed(futures)]

    oks = [item for item in results if item.startswith("ok:")]
    errs = [item for item in results if item.startswith("err:")]
    assert len(oks) == 1
    assert len(errs) == 1

    run = _fresh_store().get(user_id)
    assert run is not None
    assert run.candidate_set_id is not None
    assert run.candidate_set_version == 1
    assert run.gates.get("candidateSelection") == "approved"

    assert SessionLocal is not None
    with SessionLocal() as session:
        rows = session.execute(
            select(TwResearchCandidateSet).where(
                TwResearchCandidateSet.user_id == user_id
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].candidate_set_id == run.candidate_set_id


def test_phase_change_after_prevalidation_commits_neither(db_session) -> None:
    """Former TOCTOU: phase flip must not leave an unapproved latest CS."""
    del db_session
    user_id = f"toctou-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    # Simulate the old two-step path only to document the failure mode is closed
    # by the atomic API: mutate phase, then atomic approve must fail with no write.
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchResearchRun, user_id)
        assert row is not None
        row.phase = "phase1_first_pass"
        row.status = "running"
        row.gates_json = {
            **dict(row.gates_json or {}),
            "candidateSelection": "pending",
        }
        row.revision = int(row.revision or 0) + 1
        session.commit()

    with pytest.raises(ResearchRunGateError):
        submit_candidate_set_for_research_run(
            user_id=user_id,
            first_pass_snapshot_id=snap.snapshot_id,
            first_pass_digest=snap.content_digest,
            stock_ids=["TW:2330"],
            origin="analyst",
            expected_version=0,
        )

    assert tw_research_repository.latest_candidate_set(user_id) is None
    run = _fresh_store().get(user_id)
    assert run is not None
    assert run.candidate_set_id is None


def test_failure_during_attachment_rolls_back_candidate_set(db_session) -> None:
    del db_session
    user_id = f"rb-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    with pytest.raises(RuntimeError, match="test_fail_after_candidate_flush"):
        submit_candidate_set_for_research_run(
            user_id=user_id,
            first_pass_snapshot_id=snap.snapshot_id,
            first_pass_digest=snap.content_digest,
            stock_ids=["TW:2330"],
            origin="analyst",
            expected_version=0,
            _fail_after_candidate_flush=True,
        )

    assert tw_research_repository.latest_candidate_set(user_id) is None
    run = _fresh_store().get(user_id)
    assert run is not None
    assert run.candidate_set_id is None
    assert run.gates.get("candidateSelection") == "awaiting_analyst"


def test_duplicate_retry_is_idempotent(db_session) -> None:
    del db_session
    user_id = f"idem-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    first = submit_candidate_set_for_research_run(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2330", "TW:2454"],
        origin="analyst",
        expected_version=0,
    )
    second = submit_candidate_set_for_research_run(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2330", "TW:2454"],
        origin="analyst",
        expected_version=0,
    )
    assert first["candidateSetId"] == second["candidateSetId"]
    assert first["version"] == second["version"] == 1
    assert second["approvalOutcome"] == "already_approved"

    assert SessionLocal is not None
    with SessionLocal() as session:
        rows = session.execute(
            select(TwResearchCandidateSet).where(
                TwResearchCandidateSet.user_id == user_id
            )
        ).scalars().all()
        assert len(rows) == 1


def test_version_mismatch_does_not_mutate(db_session) -> None:
    del db_session
    user_id = f"ver-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    # Legacy standalone write creates tip v1 without run approval — version tip moves.
    # Managed approval with stale expected_version must not invent v2 or attach.
    submit_candidate_set(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2454"],
        origin="manual",
        expected_version=0,
    )
    before = tw_research_repository.latest_candidate_set(user_id)
    assert before is not None
    assert int(before["version"]) == 1

    with pytest.raises(CandidateSetConflict) as raised:
        submit_candidate_set_for_research_run(
            user_id=user_id,
            first_pass_snapshot_id=snap.snapshot_id,
            first_pass_digest=snap.content_digest,
            stock_ids=["TW:2330"],
            origin="analyst",
            expected_version=0,
        )
    assert raised.value.reason == "version_mismatch"
    assert raised.value.current_version == 1

    after = tw_research_repository.latest_candidate_set(user_id)
    assert after is not None
    assert after["candidate_set_id"] == before["candidate_set_id"]
    assert after["stock_ids"] == before["stock_ids"]
    run = _fresh_store().get(user_id)
    assert run is not None
    assert run.candidate_set_id is None
    assert run.gates.get("candidateSelection") == "awaiting_analyst"


def test_successful_response_keeps_run_and_candidate_provenance_aligned(
    db_session,
) -> None:
    del db_session
    user_id = f"align-{uuid4().hex[:10]}"
    snap = _seed_snapshot(user_id)
    _gate_run(user_id=user_id, snapshot_id=snap.snapshot_id)

    saved = submit_candidate_set_for_research_run(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2330"],
        origin="analyst",
        expected_version=0,
    )
    run = _fresh_store().get(user_id)
    latest = tw_research_repository.latest_candidate_set(user_id)
    assert run is not None and latest is not None
    assert run.candidate_set_id == saved["candidateSetId"] == latest["candidate_set_id"]
    assert run.candidate_set_version == saved["version"] == latest["version"]
    assert (
        run.first_pass_snapshot_id
        == saved["firstPassSnapshotId"]
        == latest["first_pass_snapshot_id"]
        == snap.snapshot_id
    )
    assert saved["firstPassDigest"] == snap.content_digest == latest["first_pass_digest"]
