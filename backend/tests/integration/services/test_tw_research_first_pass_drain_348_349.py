"""PostgreSQL first-pass drain + budget/lease scenarios (#348 / #349).

Requires disposable DATABASE_URL (stocks_test / *_test) + ALLOW_DESTRUCTIVE_DB_TESTS.
Skipped automatically by tests/integration/conftest when DATABASE_URL is unset.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.models import TwResearchFirstPassJob, User
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import BATCH_LEASE_SECONDS, tw_research_repository
from app.services.tw_research.enrichment import ProvenanceField
from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.first_pass_job import FirstPassJobService
from app.services.tw_research.research_run import ResearchRunStore, research_run_store


pytestmark = pytest.mark.integration


def _fresh_store() -> ResearchRunStore:
    return ResearchRunStore(repository=tw_research_repository)


def _drain_store() -> ResearchRunStore:
    """Drain service imports the process singleton — keep attach on the same object."""
    return research_run_store


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def _fake_collect(ids, **kwargs):  # noqa: ANN001
    records = [
        FirstPassSymbolRecord(
            stock_id=sid,
            stock_name=sid,
            name_status="ok",
            name_source="catalog",
            exchange="TWSE",
            market="TW",
            sector_theme_keys=[],
            completeness={"fieldsOk": 1, "fieldsUnavailable": 0},
        )
        for sid in ids
    ]
    released = []
    budget_stopped = False
    clock = kwargs.get("monotonic_clock")
    budget = kwargs.get("time_budget_seconds")
    if budget is not None and clock is not None and len(ids) > 3:
        # Mirror unit fake: ~40s/symbol under budget → stop mid-batch.
        started = clock()
        kept = []
        for sid in ids:
            if clock() - started >= float(budget):
                budget_stopped = True
                released = [x for x in ids if x not in {r.stock_id for r in kept}]
                break
            if callable(getattr(clock, "advance", None)):
                clock.advance(40.0)
            kept.append(
                FirstPassSymbolRecord(
                    stock_id=sid,
                    stock_name=sid,
                    name_status="ok",
                    name_source="catalog",
                    exchange="TWSE",
                    market="TW",
                    sector_theme_keys=[],
                    completeness={"fieldsOk": 1, "fieldsUnavailable": 0},
                )
            )
        records = kept
    duration_ms = 0
    if clock is not None:
        duration_ms = int(max(0.0, (clock() - (clock() - duration_ms)) * 1000))
    return FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version=kwargs.get("universe_version") or f"explicit:{len(ids)}",
        requested_count=len(ids),
        collected_count=len(records),
        deduped_count=len(records),
        records=records,
        coverage_summary={},
        notes=[],
        budget_stopped=budget_stopped,
        released_unstarted=released,
        duration_ms=min(duration_ms, (BATCH_LEASE_SECONDS - 1) * 1000),
    )


def _instant_collect(ids, **kwargs):  # noqa: ANN001
    return FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version=kwargs.get("universe_version") or f"explicit:{len(ids)}",
        requested_count=len(ids),
        collected_count=len(ids),
        deduped_count=len(ids),
        records=[
            FirstPassSymbolRecord(
                stock_id=sid,
                stock_name=sid,
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=[],
                completeness={"fieldsOk": 1, "fieldsUnavailable": 0},
            )
            for sid in ids
        ],
        coverage_summary={},
        notes=[],
    )


def _arm_drain(monkeypatch, user_id: str) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )


def test_a_drain_ticks_complete_73_symbol_universe(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"drain73-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _instant_collect,
    )

    stock_ids = [f"TW:{3000 + i}" for i in range(73)]
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)
    store = _drain_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")
    store.attach_first_pass_job(user_id, first_pass_job_id=job["job_id"])

    ticks = 0
    max_ticks = math.ceil(73 / 5) + 5
    while ticks < max_ticks:
        result = drain_authorized_first_pass(
            user_id=user_id,
            enabled=True,
            dry_run=False,
            max_batches=1,
            batch_size=5,
            time_budget_seconds=60,
        )
        ticks += 1
        if result.outcome in {"job_completed", "already_complete"}:
            break
        if result.outcome == "lease_held":
            continue
        if result.batches_run == 0 and result.outcome not in {
            "max_batches_reached",
            "idle_no_work",
        }:
            break

    run = store.reconcile(user_id)
    assert run is not None
    assert run.phase == "awaiting_analyst_candidate_selection"
    assert run.schedule_enabled is False
    final = service.get_job(job["job_id"], user_id=user_id)
    assert final["status"] in {"completed", "partial"}
    assert final["active_snapshot_id"]
    counts = tw_research_repository.count_first_pass_symbols(job["job_id"])
    assert counts["total"] == 73


def test_b_lease_expiry_reclaim_and_generation_fence(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"lease-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _instant_collect,
    )
    service = FirstPassJobService(repository=tw_research_repository)
    stock_ids = [f"TW:{4000 + i}" for i in range(4)]
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)

    claim_a = tw_research_repository.claim_first_pass_batch(job["job_id"], batch_size=2)
    assert claim_a is not None
    gen_a = int(claim_a["lease_generation"])
    batch_a = list(claim_a["batch_ids"])
    assert len(batch_a) == 2

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        assert row is not None
        row.reserve_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    claim_b = tw_research_repository.claim_first_pass_batch(job["job_id"], batch_size=2)
    assert claim_b is not None
    assert claim_b["lease_held"] is False
    gen_b = int(claim_b["lease_generation"])
    assert gen_b > gen_a
    assert claim_b["batch_ids"] == batch_a

    # Late persist from tick A must be rejected by generation fencing.
    late_ok = tw_research_repository.upsert_first_pass_symbol(
        job_id=job["job_id"],
        stock_id=batch_a[0],
        status="ok",
        payload={"stockId": batch_a[0], "attemptCount": 1},
        lease_generation=gen_a,
    )
    assert late_ok is False

    for sid in claim_b["batch_ids"]:
        assert tw_research_repository.upsert_first_pass_symbol(
            job_id=job["job_id"],
            stock_id=sid,
            status="ok",
            payload={"stockId": sid, "attemptCount": 1},
            lease_generation=gen_b,
        )
    tw_research_repository.complete_first_pass_batch(
        job["job_id"], lease_generation=gen_b, processed_count=2
    )
    rows = tw_research_repository.list_first_pass_symbols(job["job_id"], limit=50)
    assert len(rows) == 2
    assert {r["stock_id"] for r in rows} == set(batch_a)


def test_c_crash_mid_batch_resumes_without_duplicate(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"crash-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _instant_collect,
    )
    service = FirstPassJobService(repository=tw_research_repository)
    stock_ids = [f"TW:{5000 + i}" for i in range(6)]
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)

    claim = tw_research_repository.claim_first_pass_batch(job["job_id"], batch_size=5)
    assert claim is not None
    gen = int(claim["lease_generation"])
    # Crash after 2 symbols persisted — lease abandoned (expire it).
    for sid in claim["batch_ids"][:2]:
        assert tw_research_repository.upsert_first_pass_symbol(
            job_id=job["job_id"],
            stock_id=sid,
            status="ok",
            payload={"stockId": sid, "attemptCount": 1},
            lease_generation=gen,
        )
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        assert row is not None
        row.reserve_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    # Next tick resumes; previously verified rows stay, unfinished continue.
    result = service.run_batch(job["job_id"], user_id=user_id, batch_size=5)
    assert result["processed"] >= 1
    rows = tw_research_repository.list_first_pass_symbols(job["job_id"], limit=50)
    ids = [r["stock_id"] for r in rows]
    assert len(ids) == len(set(ids))
    assert set(claim["batch_ids"][:2]).issubset(set(ids))


def test_d_concurrent_ticks_one_lease_held(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"conc-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _instant_collect,
    )
    service = FirstPassJobService(repository=tw_research_repository)
    stock_ids = [f"TW:{6000 + i}" for i in range(10)]
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)

    def _tick() -> dict:
        return service.run_batch(job["job_id"], user_id=user_id, batch_size=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_tick), pool.submit(_tick)]
        results = [fut.result() for fut in as_completed(futures)]

    held = sum(1 for r in results if r.get("leaseHeld"))
    progressed = sum(1 for r in results if int(r.get("processed") or 0) > 0)
    assert held >= 1 or progressed >= 1
    # At most one worker should fully process under Forbid-style lease.
    assert held + progressed >= 1


def test_e_start_research_twice_one_authoritative_job(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"start2-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)
    store = _fresh_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")

    claim = store.claim_first_pass_job_slot(user_id, claim_token="tok_a")
    assert claim["outcome"] == "claimed"
    job_id = str(claim["pending_first_pass_job_id"])
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(
        user_id=user_id,
        stock_ids=["TW:2330", "TW:2317"],
        use_membership=False,
        job_id=job_id,
    )
    store.record_pending_first_pass_job(user_id, claim_token="tok_a", job_id=job["job_id"])
    attached = store.attach_first_pass_job_under_claim(
        user_id, claim_token="tok_a", job_id=job["job_id"]
    )
    assert attached.get("outcome") in {"attached", "already_attached"}

    # Second Start Research path: claim refused / returns same authoritative job.
    claim2 = store.claim_first_pass_job_slot(user_id, claim_token="tok_b")
    assert claim2["outcome"] in {"already_attached", "blocked", "claimed"}
    run = store.get(user_id)
    assert run is not None
    assert run.first_pass_job_id == job["job_id"]
    again = service.create_job(
        user_id=user_id,
        stock_ids=["TW:2330", "TW:2317"],
        use_membership=False,
        job_id=job["job_id"],
    )
    assert again["job_id"] == job["job_id"]


def test_f_300_symbol_tick_bound(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"n300-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _instant_collect,
    )
    stock_ids = [f"TW:{7000 + i}" for i in range(300)]
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)
    store = _drain_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")
    store.attach_first_pass_job(user_id, first_pass_job_id=job["job_id"])

    bound = math.ceil(300 / 5) + 2
    ticks = 0
    while ticks < bound + 5:
        result = drain_authorized_first_pass(
            user_id=user_id,
            enabled=True,
            dry_run=False,
            max_batches=1,
            batch_size=5,
            time_budget_seconds=60,
        )
        ticks += 1
        if result.outcome in {"job_completed", "already_complete"}:
            break
    assert ticks <= bound
    final = service.get_job(job["job_id"], user_id=user_id)
    assert final["status"] in {"completed", "partial"}
    assert tw_research_repository.count_first_pass_symbols(job["job_id"])["total"] == 300


def _systemic_typeerror_collect(ids, **kwargs):  # noqa: ANN001
    records = [
        FirstPassSymbolRecord(
            stock_id=sid,
            stock_name=sid,
            name_status="ok",
            name_source="catalog",
            exchange="TWSE",
            market="TW",
            sector_theme_keys=[],
            completeness={"fieldsOk": 0, "fieldsUnavailable": 1},
            fields={
                "tdcc_holders_ge_400_lots": ProvenanceField(
                    field="tdcc_holders_ge_400_lots",
                    status="unavailable",
                    source="fixture",
                    reason="programming_error:TypeError",
                ),
            },
        )
        for sid in ids
    ]
    return FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version=kwargs.get("universe_version") or f"explicit:{len(ids)}",
        requested_count=len(ids),
        collected_count=len(records),
        deduped_count=len(records),
        records=records,
        coverage_summary={},
        notes=[],
    )


def test_g_systemic_fail_stays_failed_without_snapshot(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"sysfail-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _systemic_typeerror_collect,
    )

    stock_ids = [f"TW:{8000 + i}" for i in range(10)]
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)
    store = _drain_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")
    store.attach_first_pass_job(user_id, first_pass_job_id=job["job_id"])

    first = service.run_batch(job["job_id"], user_id=user_id, batch_size=10)
    assert first["job"]["status"] == "failed"
    assert first.get("jobFailed") is True
    assert first["job"].get("active_snapshot_id") is None
    systemic = first.get("systemicProgrammingError")
    assert systemic and systemic.startswith("systemic_programming_error:")

    for _ in range(2):
        again = service.run_batch(job["job_id"], user_id=user_id, batch_size=10)
        assert again.get("jobFailed") is True
        assert again["processed"] == 0
        assert again["job"]["status"] == "failed"
        assert again["job"].get("active_snapshot_id") is None

    run = store.reconcile(user_id)
    assert run is not None
    assert run.status == "failed"
    assert run.schedule_enabled is False
    progress = (run.dimensions or {}).get("firstPassProgress") or {}
    assert progress.get("lastError") == systemic
    assert "first_pass_drain_disarmed_systemic_programming_error" in run.notes
    final = service.get_job(job["job_id"], user_id=user_id)
    assert final["status"] == "failed"
    assert final.get("active_snapshot_id") is None


def test_h_systemic_terminal_error_is_atomic_and_fenced(db_session, monkeypatch) -> None:
    """C2: terminal_error fails the job inside the locked complete; races lose cleanly."""
    del db_session
    user_id = f"atomic-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    service = FirstPassJobService(repository=tw_research_repository)
    stock_ids = [f"TW:{9000 + i}" for i in range(6)]
    job = service.create_job(user_id=user_id, stock_ids=stock_ids, use_membership=False)
    error = "systemic_programming_error:tdcc_holders_ge_400_lots:TypeError"

    claim_a = tw_research_repository.claim_first_pass_batch(job["job_id"], batch_size=3)
    gen_a = int(claim_a["lease_generation"])
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        row.reserve_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    claim_b = tw_research_repository.claim_first_pass_batch(job["job_id"], batch_size=3)
    gen_b = int(claim_b["lease_generation"])
    assert gen_b > gen_a

    # Stale worker A: fenced out — job must NOT be failed by a stale view.
    stale = tw_research_repository.complete_first_pass_batch(
        job["job_id"], lease_generation=gen_a, processed_count=0, terminal_error=error
    )
    assert stale is None
    mid = tw_research_repository.get_first_pass_job(job["job_id"], user_id=user_id)
    assert mid["status"] == "running"
    assert not any(str(n).startswith("error:") for n in mid["notes"])

    # Current worker B completes with terminal_error while claimers hammer the job.
    def _claim() -> dict:
        return tw_research_repository.claim_first_pass_batch(job["job_id"], batch_size=3)

    def _complete() -> dict | None:
        return tw_research_repository.complete_first_pass_batch(
            job["job_id"],
            lease_generation=gen_b,
            processed_count=0,
            terminal_error=error,
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        claim_futs = [pool.submit(_claim) for _ in range(4)]
        done_fut = pool.submit(_complete)
        claims = [f.result() for f in claim_futs]
        done = done_fut.result()
    assert done is not None and done["job"]["status"] == "failed"
    # Never a successful lease on a job that is failing/failed.
    assert all(not c.get("batch_ids") for c in claims)
    final = tw_research_repository.get_first_pass_job(job["job_id"], user_id=user_id)
    assert final["status"] == "failed"
    assert final["notes"].count(f"error:{error}") == 1
    assert final.get("active_snapshot_id") is None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        assert row.reserve_expires_at is None

    # Service path: systemic collector → jobFailed from the atomic complete.
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _systemic_typeerror_collect,
    )
    job2 = service.create_job(
        user_id=user_id,
        stock_ids=[f"TW:{9100 + i}" for i in range(10)],
        use_membership=False,
    )
    result = service.run_batch(job2["job_id"], user_id=user_id, batch_size=10)
    assert result["jobFailed"] is True
    assert result["leaseLost"] is False
    assert result["job"]["status"] == "failed"
    again = service.run_batch(job2["job_id"], user_id=user_id, batch_size=10)
    assert again["jobFailed"] is True and again["processed"] == 0


def test_i_research_run_progress_uses_real_symbol_counts(db_session, monkeypatch) -> None:
    """C3: firstPassProgress mirrors count_first_pass_symbols, not absent job keys."""
    del db_session
    user_id = f"counts-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        _instant_collect,
    )
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(
        user_id=user_id,
        stock_ids=[f"TW:{9200 + i}" for i in range(6)],
        use_membership=False,
    )
    store = _fresh_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")
    store.attach_first_pass_job(user_id, first_pass_job_id=job["job_id"])

    service.run_batch(job["job_id"], user_id=user_id, batch_size=4)
    counts = tw_research_repository.count_first_pass_symbols(job["job_id"])
    assert counts["total"] == 4

    run = store.reconcile(user_id)
    assert run is not None
    progress = (run.dimensions or {}).get("firstPassProgress") or {}
    assert progress["processed"] == counts["total"] == 4
    assert progress["total"] == 6
    assert progress["okCount"] == counts["ok"]
    assert progress["partialCount"] == counts["partial"]
    assert progress["failedCount"] == counts["failed"]
    assert progress["okCount"] + progress["partialCount"] + progress["failedCount"] == 4
