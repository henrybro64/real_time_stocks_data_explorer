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
    TwResearchCandidateSet,
    TwResearchDeletionAudit,
    TwResearchExportSnapshot,
    TwResearchFirstPassJob,
    TwResearchFirstPassSnapshot,
    TwResearchFirstPassSymbolResult,
    TwResearchResearchRun,
    TwResearchScan,
    TwResearchSymbolResult,
    User,
)
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import BATCH_LEASE_SECONDS, tw_research_repository
from app.services.tw_research.first_pass_job import FirstPassJobService
from app.services.tw_research.persistence import persist_scan, persist_symbol
from app.services.tw_research.scan import SymbolScanRecord
from app.services.tw_research.universe import UniverseSelector


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
        run = session.get(TwResearchResearchRun, user_id)
        if run is not None:
            for attr, table, col in (
                ("first_pass_job_id", "tw_research_first_pass_jobs", "job_id"),
                ("pending_first_pass_job_id", "tw_research_first_pass_jobs", "job_id"),
                (
                    "first_pass_snapshot_id",
                    "tw_research_first_pass_snapshots",
                    "snapshot_id",
                ),
                ("candidate_set_id", "tw_research_candidate_sets", "candidate_set_id"),
                ("technical_scan_id", "tw_research_scans", "scan_id"),
                ("pending_technical_scan_id", "tw_research_scans", "scan_id"),
            ):
                value = getattr(run, attr, None)
                if not value:
                    continue
                exists = session.execute(
                    text(f"SELECT 1 FROM {table} WHERE {col} = :id LIMIT 1"),
                    {"id": value},
                ).scalar_one_or_none()
                assert exists is not None, f"run.{attr} points to missing {value}"
        orphan_scan_prov = session.execute(
            text(
                """
                SELECT COUNT(*) FROM tw_research_scans s
                WHERE s.user_id = :uid
                  AND COALESCE(s.selector_json->>'candidateProvenance', '') <> ''
                  AND COALESCE(
                    (s.selector_json->'candidateProvenance'->>'candidateSetId'), ''
                  ) <> ''
                  AND NOT EXISTS (
                    SELECT 1 FROM tw_research_candidate_sets c
                    WHERE c.candidate_set_id = (
                      s.selector_json->'candidateProvenance'->>'candidateSetId'
                    )
                  )
                """
            ),
            {"uid": user_id},
        ).scalar_one()
        assert int(orphan_scan_prov or 0) == 0
        null_export = session.execute(
            text(
                """
                SELECT COUNT(*) FROM tw_research_export_snapshots e
                WHERE e.user_id = :uid AND e.scan_id IS NULL
                """
            ),
            {"uid": user_id},
        ).scalar_one()
        assert int(null_export or 0) == 0


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



def _seed_snapshot(session, *, user_id: str, snapshot_id: str, digest: str) -> None:
    session.add(
        TwResearchFirstPassSnapshot(
            snapshot_id=snapshot_id,
            user_id=user_id,
            universe_version="uv-test",
            first_pass_version="fpv1",
            content_digest=digest,
            payload_json={"rows": []},
            stock_ids_json=["TW:2330"],
        )
    )
    # Flush so later candidate-set inserts see the FK target in-order.
    session.flush()


def test_cancelled_scan_unclaimable_during_full_run_quiesce() -> None:
    user_id = f"u_scanq_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])
    scan_id = f"scan_{uuid4().hex[:16]}"
    assert SessionLocal is not None
    with SessionLocal() as session:
        session.add(
            TwResearchScan(
                scan_id=scan_id,
                user_id=user_id,
                trading_day="2024-06-28",
                universe_version="uv",
                calculation_version="cv",
                enrichment_version="ev",
                selector_json={"mode": "stock_ids"},
                stock_ids_json=["TW:2330", "TW:2454"],
                status="running",
                quality_status="processed",
                cursor=0,
                reserve_cursor=1,
                reserve_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
                enrich_cursor=0,
                enrich_reserve_ids_json=[],
                enrich_lease_generation=3,
                scan_lease_generation=5,
                budget_windows_opened=0,
                target_count=10,
                upstream_month_fetches=0,
                max_upstream_month_fetches=20,
                enrichments_run=0,
                max_enrichments_per_run=10,
                day_pass_ids_json=[],
                qualified_ids_json=["TW:2330"],
                notes_json=[],
            )
        )
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=f"rr_{uuid4().hex[:20]}",
                version="v1",
                phase="phase2_technical",
                status="running",
                first_pass_job_id=job_id,
                technical_scan_id=scan_id,
                gates_json={},
                dimensions_json={},
                notes_json=[],
                schedule_enabled=False,
            )
        )
        session.commit()

    preview = svc.research_run_deletion_preview(user_id=user_id)
    first = svc.delete_research_run(
        user_id=user_id, preview_token=preview["preview_token"]
    )
    assert first["outcome"] == "requested"
    with SessionLocal() as session:
        scan = session.get(TwResearchScan, scan_id)
        assert scan is not None
        assert scan.status == "cancelled"
        gen = int(scan.scan_lease_generation or 0)
        egen = int(scan.enrich_lease_generation or 0)

    claimed = tw_research_repository.claim_scan_batch(
        scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
    )
    assert claimed is not None and claimed.get("cancelled") is True
    assert claimed.get("lease_generation") == gen
    enrich = tw_research_repository.claim_enrichment_batch(scan_id, batch_size=1)
    assert enrich is not None and enrich.get("cancelled") is True
    assert enrich.get("lease_generation") == egen

    tw_research_repository.update_scan(
        {
            "scan_id": scan_id,
            "status": "running",
            "quality_status": "processed",
            "trading_day": "2024-06-28",
            "universe_version": "uv",
            "calculation_version": "cv",
            "enrichment_version": "ev",
            "selector_json": {"mode": "stock_ids"},
            "stock_ids_json": ["TW:2330"],
            "target_count": 10,
            "upstream_month_fetches": 0,
            "max_upstream_month_fetches": 20,
            "enrichments_run": 0,
            "max_enrichments_per_run": 10,
            "day_pass_ids_json": [],
            "qualified_ids_json": [],
            "notes_json": [],
        }
    )
    with SessionLocal() as session:
        scan = session.get(TwResearchScan, scan_id)
        assert scan is not None
        assert scan.status == "cancelled"
        audit = session.execute(
            select(TwResearchDeletionAudit)
            .where(
                TwResearchDeletionAudit.user_id == user_id,
                TwResearchDeletionAudit.entity_type == "research_run",
                TwResearchDeletionAudit.outcome == "requested",
            )
            .limit(1)
        ).scalar_one()
        audit.requested_at = datetime.now(timezone.utc) - timedelta(
            seconds=BATCH_LEASE_SECONDS + 5
        )
        job = session.get(TwResearchFirstPassJob, job_id)
        if job is not None and job.deletion_state == "requested":
            job.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
                seconds=BATCH_LEASE_SECONDS + 5
            )
        session.commit()

    for _ in range(3):
        again = tw_research_repository.claim_scan_batch(
            scan_id,
            batch_size=1,
            terminal_statuses={"completed_processing", "verified_ready"},
        )
        assert again is not None and again.get("cancelled") is True

    preview2 = svc.research_run_deletion_preview(user_id=user_id)
    done = svc.delete_research_run(
        user_id=user_id, preview_token=preview2["preview_token"]
    )
    assert done["outcome"] == "deleted"
    with SessionLocal() as session:
        assert session.get(TwResearchScan, scan_id) is None
        assert session.get(TwResearchResearchRun, user_id) is None
        audit_rows = list(
            session.execute(
                select(TwResearchDeletionAudit).where(
                    TwResearchDeletionAudit.user_id == user_id,
                    TwResearchDeletionAudit.entity_type == "technical_scan",
                    TwResearchDeletionAudit.entity_id == scan_id,
                )
            )
            .scalars()
            .all()
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].outcome == "deleted"
    _assert_no_orphan_rows(user_id)


def test_terminal_scan_enrichment_fenced_during_full_run_deletion() -> None:
    """verified_ready scans with enrich leases must cancel so claims stay fenced."""
    user_id = f"u_termscan_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])
    scan_id = f"scan_{uuid4().hex[:16]}"
    assert SessionLocal is not None
    with SessionLocal() as session:
        session.add(
            TwResearchScan(
                scan_id=scan_id,
                user_id=user_id,
                trading_day="2024-06-28",
                universe_version="uv",
                calculation_version="cv",
                enrichment_version="ev",
                selector_json={"mode": "stock_ids"},
                stock_ids_json=["TW:2330", "TW:2454"],
                status="verified_ready",
                quality_status="processed",
                cursor=2,
                reserve_cursor=2,
                reserve_expires_at=None,
                enrich_cursor=0,
                enrich_reserve_ids_json=["TW:2330"],
                enrich_reserve_expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=120),
                enrich_lease_generation=3,
                scan_lease_generation=5,
                budget_windows_opened=0,
                target_count=10,
                upstream_month_fetches=0,
                max_upstream_month_fetches=20,
                enrichments_run=0,
                max_enrichments_per_run=10,
                day_pass_ids_json=[],
                qualified_ids_json=["TW:2330"],
                notes_json=[],
            )
        )
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=f"rr_{uuid4().hex[:20]}",
                version="v1",
                phase="phase2_technical",
                status="evidence_ready",
                first_pass_job_id=job_id,
                technical_scan_id=scan_id,
                gates_json={},
                dimensions_json={},
                notes_json=[],
                schedule_enabled=False,
            )
        )
        session.commit()

    preview = svc.research_run_deletion_preview(user_id=user_id)
    first = svc.delete_research_run(
        user_id=user_id, preview_token=preview["preview_token"]
    )
    assert first["outcome"] == "requested"
    with SessionLocal() as session:
        scan = session.get(TwResearchScan, scan_id)
        assert scan is not None
        assert scan.status == "cancelled"

    enrich = tw_research_repository.claim_enrichment_batch(scan_id, batch_size=1)
    assert enrich is not None and enrich.get("cancelled") is True
    assert enrich.get("batch_ids") == []


def test_full_run_delete_completed_closure_counts_exact() -> None:
    user_id = f"u_full_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])
    snap_id = f"fps_{uuid4().hex[:16]}"
    cand1 = f"cs_{uuid4().hex[:14]}"
    cand2 = f"cs_{uuid4().hex[:14]}"
    scan_id = f"scan_{uuid4().hex[:16]}"
    export_id = f"exp_{uuid4().hex[:16]}"
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
        _seed_snapshot(session, user_id=user_id, snapshot_id=snap_id, digest="digest-a")
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.status = "completed"
        row.active_snapshot_id = snap_id
        session.add(
            TwResearchCandidateSet(
                candidate_set_id=cand1,
                user_id=user_id,
                version=1,
                first_pass_snapshot_id=snap_id,
                first_pass_digest="digest-a",
                stock_ids_json=["TW:2330"],
                origin="manual",
                selection_reasons_json={},
                notes_json=[],
            )
        )
        session.add(
            TwResearchCandidateSet(
                candidate_set_id=cand2,
                user_id=user_id,
                version=2,
                first_pass_snapshot_id=snap_id,
                first_pass_digest="digest-a",
                stock_ids_json=["TW:2330"],
                origin="manual",
                selection_reasons_json={},
                notes_json=[],
            )
        )
        session.add(
            TwResearchScan(
                scan_id=scan_id,
                user_id=user_id,
                trading_day="2024-06-28",
                universe_version="uv",
                calculation_version="cv",
                enrichment_version="ev",
                selector_json={
                    "mode": "stock_ids",
                    "candidateProvenance": {
                        "candidateSetId": cand2,
                        "firstPassSnapshotId": snap_id,
                    },
                },
                stock_ids_json=["TW:2330"],
                status="verified_ready",
                quality_status="verified_ready",
                cursor=1,
                enrich_cursor=0,
                enrich_reserve_ids_json=[],
                enrich_lease_generation=0,
                scan_lease_generation=0,
                budget_windows_opened=0,
                target_count=10,
                upstream_month_fetches=0,
                max_upstream_month_fetches=20,
                enrichments_run=0,
                max_enrichments_per_run=10,
                day_pass_ids_json=[],
                qualified_ids_json=["TW:2330"],
                notes_json=[],
            )
        )
        session.add(
            TwResearchSymbolResult(
                scan_id=scan_id,
                stock_id="TW:2330",
                trading_day="2024-06-28",
                universe_version="uv",
                calculation_version="cv",
                stage="B",
                readiness="technically_qualified",
                payload_json={},
            )
        )
        session.add(
            TwResearchExportSnapshot(
                snapshot_id=export_id,
                scan_id=scan_id,
                user_id=user_id,
                universe_version="uv",
                trading_day="2024-06-28",
                schema_version="s1",
                calculation_version="cv",
                enrichment_version="ev",
                quality_status="verified_ready",
                content_digest=f"expdigest_{uuid4().hex[:8]}",
                manifest_json={},
                ledger_json=[],
                chunk_digests_json={},
                finalized_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=f"rr_{uuid4().hex[:20]}",
                version="v1",
                phase="complete_evidence_ready",
                status="evidence_ready",
                first_pass_job_id=job_id,
                first_pass_snapshot_id=snap_id,
                candidate_set_id=cand2,
                candidate_set_version=2,
                technical_scan_id=scan_id,
                gates_json={},
                dimensions_json={},
                notes_json=[],
                schedule_enabled=False,
            )
        )
        session.commit()

    preview = svc.research_run_deletion_preview(user_id=user_id)
    assert preview["blockers"] == []
    expected = dict(preview["closure"])
    deleted = svc.delete_research_run(
        user_id=user_id, preview_token=preview["preview_token"]
    )
    assert deleted["outcome"] == "deleted"
    assert deleted["counts"] == expected
    with SessionLocal() as session:
        assert session.get(TwResearchResearchRun, user_id) is None
        assert session.get(TwResearchFirstPassJob, job_id) is None
        assert session.get(TwResearchFirstPassSnapshot, snap_id) is None
        assert session.get(TwResearchCandidateSet, cand1) is None
        assert session.get(TwResearchCandidateSet, cand2) is None
        assert session.get(TwResearchScan, scan_id) is None
        assert session.get(TwResearchExportSnapshot, export_id) is None
    # GET research-runs/current style: store returns None without error.
    assert tw_research_repository.get_research_run(user_id) is None
    _assert_no_orphan_rows(user_id)


def test_shared_snapshot_kept_until_last_job_deleted() -> None:
    user_id = f"u_share_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job_a = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_b = svc.create_job(
        user_id=user_id, stock_ids=["TW:2454"], use_membership=False
    )
    id_a = str(job_a["job_id"])
    id_b = str(job_b["job_id"])
    snap_id = f"fps_{uuid4().hex[:16]}"
    digest = "shared-digest-xyz"
    assert SessionLocal is not None
    with SessionLocal() as session:
        _seed_snapshot(session, user_id=user_id, snapshot_id=snap_id, digest=digest)
        for jid in (id_a, id_b):
            row = session.get(TwResearchFirstPassJob, jid)
            assert row is not None
            row.status = "completed"
            row.active_snapshot_id = snap_id
        session.commit()

    preview_a = svc.deletion_preview(id_a, user_id=user_id)
    del_a = svc.delete_job(
        id_a, user_id=user_id, preview_token=preview_a["preview_token"]
    )
    assert del_a["outcome"] == "deleted"
    with SessionLocal() as session:
        assert session.get(TwResearchFirstPassSnapshot, snap_id) is not None

    preview_b = svc.deletion_preview(id_b, user_id=user_id)
    del_b = svc.delete_job(
        id_b, user_id=user_id, preview_token=preview_b["preview_token"]
    )
    assert del_b["outcome"] == "deleted"
    with SessionLocal() as session:
        assert session.get(TwResearchFirstPassSnapshot, snap_id) is None
    _assert_no_orphan_rows(user_id)


def test_concurrent_deletes_one_wins() -> None:
    import threading

    user_id = f"u_conc_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330"], use_membership=False
    )
    job_id = str(job["job_id"])
    preview = svc.deletion_preview(job_id, user_id=user_id)
    token = preview["preview_token"]
    results: list[dict] = []

    def _worker() -> None:
        results.append(
            svc.delete_job(job_id, user_id=user_id, preview_token=token)
        )

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(results) == 2
    outcomes = {r.get("outcome") for r in results}
    assert "deleted" in outcomes
    assert outcomes <= {"deleted", "conflict", "not_found"}
    assert all(r.get("outcome") != "error" for r in results)
    assert not _job_exists(job_id)
    with SessionLocal() as session:
        audits = list(
            session.execute(
                select(TwResearchDeletionAudit).where(
                    TwResearchDeletionAudit.user_id == user_id,
                    TwResearchDeletionAudit.entity_type == "first_pass_job",
                    TwResearchDeletionAudit.entity_id == job_id,
                    TwResearchDeletionAudit.outcome == "deleted",
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 1
    _assert_no_orphan_rows(user_id)


def test_late_writes_fenced_after_deletion_request_and_full_run() -> None:
    user_id = f"u_late_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    job = svc.create_job(
        user_id=user_id, stock_ids=["TW:2330", "TW:2454"], use_membership=False
    )
    job_id = str(job["job_id"])
    old_gen = 7
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        row.status = "running"
        row.lease_generation = old_gen
        row.reserve_cursor = 1
        row.cursor = 0
        row.reserve_expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
        session.commit()

    preview = svc.deletion_preview(job_id, user_id=user_id)
    requested = svc.delete_job(
        job_id, user_id=user_id, preview_token=preview["preview_token"]
    )
    assert requested["outcome"] == "requested"
    assert (
        tw_research_repository.complete_first_pass_batch(
            job_id, lease_generation=old_gen, processed_count=1
        )
        is None
    )
    assert (
        tw_research_repository.renew_first_pass_lease(
            job_id, lease_generation=old_gen
        )
        is False
    )
    assert (
        tw_research_repository.upsert_first_pass_symbol(
            job_id=job_id,
            stock_id="TW:2330",
            status="ok",
            payload={"late": True},
            lease_generation=old_gen,
        )
        is False
    )
    assert _count_symbols(job_id) == 0

    # Full-run path: fence scan after delete completes.
    scan_id = f"scan_{uuid4().hex[:16]}"
    job2 = svc.create_job(
        user_id=user_id, stock_ids=["TW:2303"], use_membership=False
    )
    job2_id = str(job2["job_id"])
    with SessionLocal() as session:
        # Finish pending job1 delete first.
        row = session.get(TwResearchFirstPassJob, job_id)
        if row is not None:
            row.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
                seconds=BATCH_LEASE_SECONDS + 5
            )
            session.commit()
    status = svc.deletion_status(job_id, user_id=user_id)
    assert status.get("gone") is True

    with SessionLocal() as session:
        session.add(
            TwResearchScan(
                scan_id=scan_id,
                user_id=user_id,
                trading_day="2024-06-28",
                universe_version="uv",
                calculation_version="cv",
                enrichment_version="ev",
                selector_json={"mode": "stock_ids"},
                stock_ids_json=["TW:2303"],
                status="running",
                quality_status="processed",
                cursor=0,
                enrich_cursor=0,
                enrich_reserve_ids_json=[],
                enrich_lease_generation=1,
                scan_lease_generation=1,
                budget_windows_opened=0,
                target_count=10,
                upstream_month_fetches=0,
                max_upstream_month_fetches=20,
                enrichments_run=0,
                max_enrichments_per_run=10,
                day_pass_ids_json=[],
                qualified_ids_json=[],
                notes_json=[],
            )
        )
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=f"rr_{uuid4().hex[:20]}",
                version="v1",
                phase="phase2_technical",
                status="running",
                first_pass_job_id=job2_id,
                technical_scan_id=scan_id,
                gates_json={},
                dimensions_json={},
                notes_json=[],
                schedule_enabled=False,
            )
        )
        session.commit()

    run_preview = svc.research_run_deletion_preview(user_id=user_id)
    # Quiesce then complete.
    mid = svc.delete_research_run(
        user_id=user_id, preview_token=run_preview["preview_token"]
    )
    if mid["outcome"] == "requested":
        with SessionLocal() as session:
            audit = session.execute(
                select(TwResearchDeletionAudit)
                .where(
                    TwResearchDeletionAudit.user_id == user_id,
                    TwResearchDeletionAudit.entity_type == "research_run",
                    TwResearchDeletionAudit.outcome == "requested",
                )
                .limit(1)
            ).scalar_one_or_none()
            if audit is not None:
                audit.requested_at = datetime.now(timezone.utc) - timedelta(
                    seconds=BATCH_LEASE_SECONDS + 5
                )
            job_row = session.get(TwResearchFirstPassJob, job2_id)
            if job_row is not None and job_row.deletion_state == "requested":
                job_row.deletion_requested_at = datetime.now(timezone.utc) - timedelta(
                    seconds=BATCH_LEASE_SECONDS + 5
                )
            session.commit()
        run_preview = svc.research_run_deletion_preview(user_id=user_id)
        mid = svc.delete_research_run(
            user_id=user_id, preview_token=run_preview["preview_token"]
        )
    assert mid["outcome"] == "deleted"

    from app.services.tw_research.scan import ScanState

    ghost = ScanState(
        scan_id=scan_id,
        trading_day="2024-06-28",
        universe_version="uv",
        calculation_version="cv",
        selector=UniverseSelector(mode="stock_ids", stock_ids=("TW:2303",)),
        stock_ids=("TW:2303",),
        status="running",
        quality_status="processed",
        user_id=user_id,
    )
    assert persist_scan(ghost, repository=tw_research_repository, user_id=user_id) is False
    record = SymbolScanRecord(
        stock_id="TW:2303",
        stage="A",
        readiness="processed",
    )
    assert (
        persist_symbol(ghost, record, repository=tw_research_repository) is False
    )
    with SessionLocal() as session:
        assert session.get(TwResearchScan, scan_id) is None
        assert (
            session.execute(
                select(func.count())
                .select_from(TwResearchSymbolResult)
                .where(TwResearchSymbolResult.scan_id == scan_id)
            ).scalar_one()
            == 0
        )
    _assert_no_orphan_rows(user_id)


def test_migration_additive_columns_preserve_existing_job_values() -> None:
    """Existing jobs keep key values; additive lifecycle columns stay NULL."""
    user_id = f"u_mig_{uuid4().hex[:10]}"
    _ensure_user(user_id)
    svc = _service()
    snapshots = []
    for _ in range(3):
        job = svc.create_job(
            user_id=user_id, stock_ids=["TW:2330"], use_membership=False
        )
        snapshots.append(
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "universe_version": job["universe_version"],
                "cursor": job["cursor"],
                "quality_status": job["quality_status"],
            }
        )
    assert SessionLocal is not None
    with SessionLocal() as session:
        for snap in snapshots:
            row = session.get(TwResearchFirstPassJob, snap["job_id"])
            assert row is not None
            assert row.archived_at is None
            assert row.deletion_state is None
            assert row.status == snap["status"]
            assert row.universe_version == snap["universe_version"]
            assert int(row.cursor or 0) == int(snap["cursor"])
            assert row.quality_status == snap["quality_status"]
