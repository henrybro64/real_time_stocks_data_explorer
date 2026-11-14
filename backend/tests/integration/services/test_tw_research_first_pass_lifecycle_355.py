"""PostgreSQL hard-delete / archive lifecycle for first-pass jobs (#355).

Requires disposable DATABASE_URL (stocks_test / *_test) + ALLOW_DESTRUCTIVE_DB_TESTS.
Skipped automatically by tests/integration/conftest when DATABASE_URL is unset.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.db.models import (
    TwResearchDeletionAudit,
    TwResearchFirstPassJob,
    TwResearchFirstPassSymbolResult,
    TwResearchResearchRun,
    User,
)
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import BATCH_LEASE_SECONDS, tw_research_repository
from app.services.tw_research.first_pass_job import FirstPassJobService


pytestmark = pytest.mark.integration


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def _service() -> FirstPassJobService:
    return FirstPassJobService(repository=tw_research_repository)


def _count_symbols(job_id: str) -> int:
    assert SessionLocal is not None
    with SessionLocal() as session:
        return int(
            session.execute(
                select(func.count())
                .select_from(TwResearchFirstPassSymbolResult)
                .where(TwResearchFirstPassSymbolResult.job_id == job_id)
            ).scalar_one()
            or 0
        )


def _job_exists(job_id: str) -> bool:
    assert SessionLocal is not None
    with SessionLocal() as session:
        return session.get(TwResearchFirstPassJob, job_id) is not None


def test_migration_additive_columns_exist() -> None:
    """Additive #355 columns exist; this does not mutate existing job payloads."""
    assert SessionLocal is not None
    with SessionLocal() as session:
        cols = {
            row[0]
            for row in session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'tw_research_first_pass_jobs'
                    """
                )
            )
        }
        assert "archived_at" in cols
        assert "deletion_state" in cols
        assert "deletion_requested_at" in cols
        assert "deletion_error" in cols
        tables = {
            row[0]
            for row in session.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    """
                )
            )
        }
        assert "tw_research_deletion_audit" in tables


def test_hard_delete_pending_job_removes_parent_and_symbols() -> None:
    user_id = f"u_del_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330", "TW:2454"], use_membership=False
    )
    job_id = str(job["job_id"])

    # Seed a symbol row so CASCADE/delete counts are non-zero.
    assert SessionLocal is not None
    with SessionLocal() as session:
        session.add(
            TwResearchFirstPassSymbolResult(
                job_id=job_id,
                stock_id="TW:2330",
                status="failed",
                payload_json={"reason": "test"},
                lease_generation=0,
            )
        )
        session.commit()
    assert _count_symbols(job_id) == 1

    preview = svc.deletion_preview(job_id, user_id=user_id)
    assert preview["requires_full_run_delete"] is False
    result = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview["preview_token"]
    )
    assert result["outcome"] == "deleted"
    assert result["counts"]["tw_research_first_pass_jobs"] == 1
    assert result["counts"]["tw_research_first_pass_symbol_results"] == 1
    assert not _job_exists(job_id)
    assert _count_symbols(job_id) == 0

    with SessionLocal() as session:
        audit = session.execute(
            select(TwResearchDeletionAudit).where(
                TwResearchDeletionAudit.user_id == user_id,
                TwResearchDeletionAudit.entity_id == job_id,
            )
        ).scalar_one()
        assert audit.outcome == "deleted"


def test_blocked_run_owned_job_mutates_nothing_without_full_run() -> None:
    user_id = f"u_owned_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])

    assert SessionLocal is not None
    with SessionLocal() as session:
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=f"rr_{uuid4().hex[:20]}",
                version="v1",
                phase="phase1_first_pass",
                status="running",
                first_pass_job_id=job_id,
                gates_json={},
                dimensions_json={},
                notes_json=[],
                schedule_enabled=False,
            )
        )
        session.commit()

    preview = svc.deletion_preview(job_id, user_id=user_id)
    assert preview["requires_full_run_delete"] is True
    result = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview["preview_token"]
    )
    assert result["outcome"] == "conflict"
    assert result["code"] == "run_owned_requires_full_run_delete"
    assert _job_exists(job_id)

    # Full-run path removes the run + job.
    run_preview = svc.research_run_deletion_preview(user_id=user_id)
    deleted = svc.delete_research_run(
        user_id=user_id, preview_token=run_preview["preview_token"]
    )
    assert deleted["outcome"] in {"deleted", "requested"}
    if deleted["outcome"] == "deleted":
        assert not _job_exists(job_id)
        with SessionLocal() as session:
            assert session.get(TwResearchResearchRun, user_id) is None


def test_running_job_delete_returns_requested_then_completes() -> None:
    user_id = f"u_run_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id,
        stock_ids=["TW:2330", "TW:2454", "TW:2303"],
        use_membership=False,
    )
    job_id = str(job["job_id"])

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.status = "running"
        row.reserve_cursor = 2
        row.cursor = 0
        row.reserve_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        row.lease_generation = 3
        session.commit()

    preview = svc.deletion_preview(job_id, user_id=user_id)
    first = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview["preview_token"]
    )
    assert first["outcome"] == "requested"
    assert first["deletion_state"] == "requested"
    assert _job_exists(job_id)

    # Claim must refuse while deletion_state is set.
    claim = tw_research_repository.claim_first_pass_batch(job_id, batch_size=1)
    assert claim is None or not claim.get("batch_ids")

    # Force quiescence window elapsed, then lazy-complete.
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
            seconds=BATCH_LEASE_SECONDS + 5
        )
        row.reserve_expires_at = None
        session.commit()

    preview2 = svc.deletion_preview(job_id, user_id=user_id)
    second = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview2["preview_token"]
    )
    assert second["outcome"] == "deleted"
    assert not _job_exists(job_id)
    assert _count_symbols(job_id) == 0


def test_archive_restore_hides_from_default_list() -> None:
    user_id = f"u_arc_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])
    archived = svc.archive_job(job_id, user_id=user_id, archived=True)
    assert archived.get("archived_at")
    visible = svc.list_jobs(user_id=user_id, include_archived=False)
    assert all(str(j["job_id"]) != job_id for j in visible)
    with_arch = svc.list_jobs(user_id=user_id, include_archived=True)
    assert any(str(j["job_id"]) == job_id for j in with_arch)
    restored = svc.archive_job(job_id, user_id=user_id, archived=False)
    assert restored.get("archived_at") in (None, "")


def _assert_no_orphan_rows(user_id: str) -> None:
    """Consistency: no dangling FK-ish references after a delete (#355)."""
    assert SessionLocal is not None
    with SessionLocal() as session:
        orphan_symbols = session.execute(
            text(
                """
                SELECT COUNT(*) FROM tw_research_first_pass_symbol_results s
                WHERE NOT EXISTS (
                  SELECT 1 FROM tw_research_first_pass_jobs j WHERE j.job_id = s.job_id
                )
                """
            )
        ).scalar_one()
        assert int(orphan_symbols or 0) == 0
        orphan_cands = session.execute(
            text(
                """
                SELECT COUNT(*) FROM tw_research_candidate_sets c
                WHERE c.user_id = :uid
                  AND c.first_pass_snapshot_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM tw_research_first_pass_snapshots snap
                    WHERE snap.snapshot_id = c.first_pass_snapshot_id
                  )
                """
            ),
            {"uid": user_id},
        ).scalar_one()
        assert int(orphan_cands or 0) == 0


def test_deletion_status_completes_after_bound_without_job_list() -> None:
    """GET deletion status alone completes after the quiescence bound (#355)."""
    user_id = f"u_stat_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id,
        stock_ids=["TW:2330", "TW:2454"],
        use_membership=False,
    )
    job_id = str(job["job_id"])
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.status = "running"
        row.reserve_cursor = 1
        row.cursor = 0
        row.reserve_expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
        session.commit()

    preview = svc.deletion_preview(job_id, user_id=user_id)
    first = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview["preview_token"]
    )
    assert first["outcome"] == "requested"

    # Bound not yet passed → still present.
    mid = svc.deletion_status(job_id, user_id=user_id)
    assert mid.get("gone") is False
    assert mid.get("deletion_state") == "requested"

    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        # Past 2× lease — must still complete (never fail for wall time alone).
        row.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
            seconds=BATCH_LEASE_SECONDS * 2 + 5
        )
        session.commit()

    status = svc.deletion_status(job_id, user_id=user_id)
    assert status.get("gone") is True
    assert status.get("outcome") == "deleted"
    assert not _job_exists(job_id)
    assert _count_symbols(job_id) == 0

    with SessionLocal() as session:
        audits = list(
            session.execute(
                select(TwResearchDeletionAudit).where(
                    TwResearchDeletionAudit.user_id == user_id,
                    TwResearchDeletionAudit.entity_id == job_id,
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 1
        assert audits[0].outcome == "deleted"
    _assert_no_orphan_rows(user_id)


def test_forced_delete_exception_marks_failed_then_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = f"u_fail_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])
    assert SessionLocal is not None
    with SessionLocal() as session:
        session.add(
            TwResearchFirstPassSymbolResult(
                job_id=job_id,
                stock_id="TW:2330",
                status="ok",
                payload_json={},
                lease_generation=0,
            )
        )
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.status = "running"
        row.reserve_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        session.commit()

    preview = svc.deletion_preview(job_id, user_id=user_id)
    first = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview["preview_token"]
    )
    assert first["outcome"] == "requested"
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
            seconds=BATCH_LEASE_SECONDS + 5
        )
        session.commit()

    from sqlalchemy.orm import Session as SaSession

    original_delete = SaSession.delete

    def _fail_delete(self, instance):  # noqa: ANN001
        if isinstance(instance, TwResearchFirstPassJob):
            raise RuntimeError("forced_delete_boom")
        return original_delete(self, instance)

    monkeypatch.setattr(SaSession, "delete", _fail_delete)
    preview2 = svc.deletion_preview(job_id, user_id=user_id)
    failed = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview2["preview_token"]
    )
    assert failed["outcome"] == "conflict"
    assert _job_exists(job_id)
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        assert row.deletion_state == "failed"
        assert row.deletion_error == "RuntimeError"
        assert _count_symbols(job_id) == 1

    monkeypatch.setattr(SaSession, "delete", original_delete)
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.deletion_state = "requested"
        row.deletion_error = None
        row.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
            seconds=BATCH_LEASE_SECONDS + 5
        )
        session.commit()
    preview3 = svc.deletion_preview(job_id, user_id=user_id)
    ok = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview3["preview_token"]
    )
    assert ok["outcome"] == "deleted"
    assert not _job_exists(job_id)
    assert _count_symbols(job_id) == 0
    _assert_no_orphan_rows(user_id)

