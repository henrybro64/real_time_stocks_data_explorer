"""PostgreSQL atomic adopt of standalone first-pass jobs (#359).

Skipped automatically when DATABASE_URL is unset (tests/integration/conftest).
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Thread
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import (
    TwResearchFirstPassJob,
    TwResearchFirstPassSymbolResult,
    TwResearchResearchRun,
    User,
)
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.research_run import ResearchRunConflict, research_run_store


pytestmark = pytest.mark.integration


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def _seed_partial_job(user_id: str, *, digest: str = "digest-adopt-73") -> str:
    job_id = f"fpj_adopt_{uuid4().hex[:16]}"
    stock_ids = ["TW:8046"] + [f"TW:{8100 + i}" for i in range(72)]
    assert SessionLocal is not None
    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(
            TwResearchFirstPassJob(
                job_id=job_id,
                user_id=user_id,
                universe_version="explicit:73",
                universe_digest=digest,
                membership_version=0,
                first_pass_version="test",
                stock_ids_json=stock_ids,
                selector_json={"mode": "stock_ids", "stockIds": stock_ids},
                status="running",
                quality_status="incomplete",
                cursor=1,
                notes_json=["seeded for adopt test"],
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TwResearchFirstPassSymbolResult(
                job_id=job_id,
                stock_id="TW:8046",
                status="ok",
                payload_json={"stockId": "TW:8046", "fields": {}},
                lease_generation=0,
                updated_at=now,
            )
        )
        session.commit()
    return job_id


def test_adopt_1_of_73_preserves_symbol_and_points_run() -> None:
    user_id = f"u_adopt_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    digest = "digest-adopt-73"
    job_id = _seed_partial_job(user_id, digest=digest)

    run = research_run_store.adopt_standalone_first_pass_job(
        user_id,
        job_id=job_id,
        confirm_universe_digest=digest,
    )
    assert run.first_pass_job_id == job_id
    assert run.phase == "phase1_first_pass"

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.execute(
            select(TwResearchFirstPassSymbolResult).where(
                TwResearchFirstPassSymbolResult.job_id == job_id,
                TwResearchFirstPassSymbolResult.stock_id == "TW:8046",
            )
        ).scalar_one()
        assert row.status == "ok"
        assert row.payload_json.get("stockId") == "TW:8046"
    counts = tw_research_repository.count_first_pass_symbols(job_id)
    assert counts["ok"] == 1
    assert counts["total"] == 1


def test_concurrent_adopts_single_winner() -> None:
    user_id = f"u_race_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    digest = "digest-race"
    job_id = _seed_partial_job(user_id, digest=digest)
    results: list[object] = []

    def worker() -> None:
        try:
            results.append(
                research_run_store.adopt_standalone_first_pass_job(
                    user_id,
                    job_id=job_id,
                    confirm_universe_digest=digest,
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    wins = [item for item in results if not isinstance(item, Exception)]
    losses = [item for item in results if isinstance(item, Exception)]
    assert len(wins) >= 1
    # Other outcome is idempotent same-run or 409 retained/conflict.
    assert all(
        (not isinstance(item, Exception))
        or isinstance(item, ResearchRunConflict)
        or isinstance(item, KeyError)
        for item in results
    )
    assert SessionLocal is not None
    with SessionLocal() as session:
        runs = session.execute(
            select(TwResearchResearchRun).where(TwResearchResearchRun.user_id == user_id)
        ).scalars().all()
        assert len(runs) == 1
        assert runs[0].first_pass_job_id == job_id


def test_gated_run_is_retained() -> None:
    user_id = f"u_gated_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    digest = "digest-gated"
    job_id = _seed_partial_job(user_id, digest=digest)
    # Seed an active gated run pointing at a different job.
    assert SessionLocal is not None
    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=f"rr_{uuid4().hex[:12]}",
                version="research-run-v1",
                phase="awaiting_analyst_candidate_selection",
                status="gated",
                first_pass_job_id="fpj_other_gated",
                gates_json={},
                dimensions_json={},
                notes_json=[],
                schedule_enabled=False,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    before = tw_research_repository.get_research_run(user_id)
    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            user_id,
            job_id=job_id,
            confirm_universe_digest=digest,
        )
    assert exc.value.reason == "research_run_retained"
    after = tw_research_repository.get_research_run(user_id)
    assert before is not None and after is not None
    assert after["first_pass_job_id"] == "fpj_other_gated"
    assert after["revision"] == before["revision"]


def test_concurrent_start_and_adopt_no_500_single_run() -> None:
    """Concurrent managed Start + adopt → no 500, one run, job attached ≤ once."""
    user_id = f"u_race_start_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    digest = "digest-start-adopt"
    job_id = _seed_partial_job(user_id, digest=digest)
    results: list[object] = []

    def start_worker() -> None:
        try:
            results.append(("start", research_run_store.begin_phase1(user_id)))
        except Exception as exc:  # noqa: BLE001
            results.append(("start", exc))

    def adopt_worker() -> None:
        try:
            results.append(
                (
                    "adopt",
                    research_run_store.adopt_standalone_first_pass_job(
                        user_id,
                        job_id=job_id,
                        confirm_universe_digest=digest,
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(("adopt", exc))

    threads = [Thread(target=start_worker), Thread(target=adopt_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 2
    # Never a raw 500 / unexpected exception type from the race.
    for _label, item in results:
        assert not isinstance(item, Exception) or isinstance(
            item, (ResearchRunConflict, KeyError)
        )

    assert SessionLocal is not None
    with SessionLocal() as session:
        runs = session.execute(
            select(TwResearchResearchRun).where(
                TwResearchResearchRun.user_id == user_id
            )
        ).scalars().all()
        assert len(runs) == 1
        attached = [
            r for r in runs if str(r.first_pass_job_id or "") == str(job_id)
        ]
        assert len(attached) <= 1


def test_replace_failed_run_gets_fresh_identity() -> None:
    user_id = f"u_replace_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    digest = "digest-replace"
    job_id = _seed_partial_job(user_id, digest=digest)
    old_run_id = f"rr_old_{uuid4().hex[:12]}"
    assert SessionLocal is not None
    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(
            TwResearchResearchRun(
                user_id=user_id,
                research_run_id=old_run_id,
                version="research-run-v1",
                phase="phase1_first_pass",
                status="failed",
                first_pass_job_id="fpj_old_failed",
                first_pass_snapshot_id="fps_stale",
                candidate_set_id=None,
                technical_scan_id=None,
                gates_json={"candidateSelection": "approved"},
                dimensions_json={
                    "fundamentals": "failed",
                    "firstPassDrain": {"lastTickAt": "2024-01-01T00:00:00+00:00"},
                },
                notes_json=["prior failure"],
                schedule_enabled=False,
                revision=3,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    run = research_run_store.adopt_standalone_first_pass_job(
        user_id,
        job_id=job_id,
        confirm_universe_digest=digest,
    )
    assert run.first_pass_job_id == job_id
    assert run.research_run_id != old_run_id
    assert run.first_pass_snapshot_id is None
    assert run.gates.get("candidateSelection") == "pending"
    assert run.dimensions.get("fundamentals") == "in_progress"
    # Drain telemetry may be retained.
    assert (run.dimensions.get("firstPassDrain") or {}).get("lastTickAt")


def test_adopt_allows_transient_failed_with_retries() -> None:
    """Job whose only unfinished work is retry-scheduled failures is adoptable."""
    user_id = f"u_retry_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    digest = "digest-retry"
    job_id = f"fpj_retry_{uuid4().hex[:16]}"
    stock_ids = ["TW:8046", "TW:8100"]
    assert SessionLocal is not None
    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(
            TwResearchFirstPassJob(
                job_id=job_id,
                user_id=user_id,
                universe_version="explicit:2",
                universe_digest=digest,
                membership_version=0,
                first_pass_version="test",
                stock_ids_json=stock_ids,
                selector_json={"mode": "stock_ids", "stockIds": stock_ids},
                status="partial",
                quality_status="incomplete",
                cursor=1,
                notes_json=[],
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TwResearchFirstPassSymbolResult(
                job_id=job_id,
                stock_id="TW:8046",
                status="ok",
                payload_json={"stockId": "TW:8046", "fields": {}},
                lease_generation=0,
                updated_at=now,
            )
        )
        session.add(
            TwResearchFirstPassSymbolResult(
                job_id=job_id,
                stock_id="TW:8100",
                status="failed",
                payload_json={
                    "stockId": "TW:8100",
                    "retryExhausted": False,
                    "failureClass": "timeout",
                    "nextRetryAt": "2099-01-01T00:00:00+00:00",
                },
                lease_generation=0,
                updated_at=now,
            )
        )
        session.commit()

    run = research_run_store.adopt_standalone_first_pass_job(
        user_id,
        job_id=job_id,
        confirm_universe_digest=digest,
    )
    assert run.first_pass_job_id == job_id


def test_concurrent_adopt_and_cancel_no_500() -> None:
    """Adopt + cancel the same job concurrently → no 500; final state consistent."""
    from sqlalchemy.exc import OperationalError

    for _ in range(10):
        user_id = f"u_ac_{uuid4().hex[:8]}"
        _ensure_user(user_id)
        digest = "digest-adopt-cancel"
        job_id = _seed_partial_job(user_id, digest=digest)
        results: list[object] = []

        def adopt_worker() -> None:
            try:
                results.append(
                    (
                        "adopt",
                        research_run_store.adopt_standalone_first_pass_job(
                            user_id,
                            job_id=job_id,
                            confirm_universe_digest=digest,
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(("adopt", exc))

        def cancel_worker() -> None:
            try:
                results.append(
                    (
                        "cancel",
                        tw_research_repository.cancel_first_pass_job(
                            job_id, user_id=user_id
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(("cancel", exc))

        threads = [Thread(target=adopt_worker), Thread(target=cancel_worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(results) == 2
        for _label, item in results:
            # Deadlocks must not escape as raw OperationalError / 500.
            assert not isinstance(item, OperationalError)
            if isinstance(item, Exception):
                assert isinstance(item, (ResearchRunConflict, KeyError))

        assert SessionLocal is not None
        with SessionLocal() as session:
            job = session.get(TwResearchFirstPassJob, job_id)
            assert job is not None
            run = session.execute(
                select(TwResearchResearchRun).where(
                    TwResearchResearchRun.user_id == user_id
                )
            ).scalar_one_or_none()
            if str(job.status) == "cancelled":
                # Cancel won (possibly after adopt): run cancelled if attached.
                if run is not None and str(run.first_pass_job_id or "") == str(job_id):
                    assert str(run.status) == "cancelled"
            else:
                # Adopt won without cancel finishing the job: run points at job.
                assert run is not None
                assert str(run.first_pass_job_id or "") == str(job_id)
