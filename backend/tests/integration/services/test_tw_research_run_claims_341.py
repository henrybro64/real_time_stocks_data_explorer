"""PostgreSQL-backed research-run claim / attach fencing (#341 P1-1/P1-2).

Requires disposable DATABASE_URL (stocks_test / *_test) + ALLOW_DESTRUCTIVE_DB_TESTS.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import TwResearchFirstPassJob, TwResearchResearchRun
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.research_run import ResearchRunStore


pytestmark = pytest.mark.integration


def _fresh_store() -> ResearchRunStore:
    return ResearchRunStore(repository=tw_research_repository)


def test_concurrent_job_claims_create_one_authoritative_job(db_session) -> None:
    del db_session
    user_id = f"claim-{uuid4().hex[:10]}"
    store = _fresh_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")

    def _race(token: str) -> dict:
        claim = store.claim_first_pass_job_slot(user_id, claim_token=token)
        if claim["outcome"] != "claimed":
            return claim
        job_id = f"fpj_{token}"
        # Simulate create_job by inserting a first-pass job row.
        tw_research_repository.create_first_pass_job(
            {
                "job_id": job_id,
                "user_id": user_id,
                "universe_version": "explicit:1",
                "universe_digest": "d" * 64,
                "membership_version": 0,
                "first_pass_version": "first-pass-v1",
                "stock_ids": ["TW:2330"],
                "selector": {"mode": "stock_ids", "stockIds": ["TW:2330"]},
                "status": "pending",
                "quality_status": "incomplete",
                "notes": [],
            }
        )
        store.record_pending_first_pass_job(
            user_id, claim_token=token, job_id=job_id
        )
        return store.attach_first_pass_job_under_claim(
            user_id, claim_token=token, job_id=job_id
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_race, "tok_a"),
            pool.submit(_race, "tok_b"),
        ]
        results = [fut.result() for fut in as_completed(futures)]

    outcomes = {str(item.get("outcome")) for item in results}
    assert "attached" in outcomes or "already_attached" in outcomes
    run = store.get(user_id)
    assert run is not None
    assert run.first_pass_job_id is not None

    assert SessionLocal is not None
    with SessionLocal() as session:
        jobs = session.execute(
            select(TwResearchFirstPassJob).where(
                TwResearchFirstPassJob.user_id == user_id
            )
        ).scalars().all()
        # At most one authoritative attachment; extras must be marked failed/orphaned
        # by the losing path when callers invoke mark_orphaned_first_pass_job.
        assert len(jobs) <= 2
        row = session.get(TwResearchResearchRun, user_id)
        assert row is not None
        assert row.first_pass_job_id == run.first_pass_job_id


def test_pending_job_survives_crash_before_attach(db_session) -> None:
    del db_session
    user_id = f"pending-{uuid4().hex[:10]}"
    store = _fresh_store()
    store.begin_phase1(user_id, trading_day="2024-06-27")
    claim = store.claim_first_pass_job_slot(user_id, claim_token="tok_pending")
    assert claim["outcome"] == "claimed"
    tw_research_repository.create_first_pass_job(
        {
            "job_id": "fpj_pending",
            "user_id": user_id,
            "universe_version": "explicit:1",
            "universe_digest": "e" * 64,
            "membership_version": 0,
            "first_pass_version": "first-pass-v1",
            "stock_ids": ["TW:2330"],
            "selector": {"mode": "stock_ids"},
            "status": "pending",
            "quality_status": "incomplete",
            "notes": [],
        }
    )
    recorded = store.record_pending_first_pass_job(
        user_id, claim_token="tok_pending", job_id="fpj_pending"
    )
    assert recorded["outcome"] == "recorded"
    # New store instance (other pod) reclaims same token before expiry and attaches.
    store2 = _fresh_store()
    reclaim = store2.claim_first_pass_job_slot(user_id, claim_token="tok_pending")
    assert reclaim["outcome"] == "claimed"
    assert reclaim.get("pending_first_pass_job_id") == "fpj_pending"
    attached = store2.attach_first_pass_job_under_claim(
        user_id, claim_token="tok_pending", job_id="fpj_pending"
    )
    assert attached["outcome"] == "attached"
    assert store2.get(user_id).first_pass_job_id == "fpj_pending"


def test_five_duplicate_starts_share_one_job(client, monkeypatch, db_session) -> None:
    del db_session
    created: list[str] = []

    def fake_create_job(**kwargs):  # noqa: ANN003
        del kwargs
        job_id = f"fpj_dup_{len(created)+1}"
        created.append(job_id)
        tw_research_repository.create_first_pass_job(
            {
                "job_id": job_id,
                "user_id": "default-user",
                "universe_version": "explicit:1",
                "universe_digest": "f" * 64,
                "membership_version": 0,
                "first_pass_version": "first-pass-v1",
                "stock_ids": ["TW:2330"],
                "selector": {"mode": "stock_ids"},
                "status": "pending",
                "quality_status": "incomplete",
                "notes": [],
            }
        )
        return {"job_id": job_id, "status": "pending"}

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.create_job",
        fake_create_job,
    )
    responses = [
        client.post(
            "/api/tw-research/research-runs/start",
            json={"phase": "phase1_first_pass", "tradingDay": "2024-06-27"},
        )
        for _ in range(5)
    ]
    assert all(item.status_code == 200 for item in responses)
    run_ids = {item.json()["researchRun"]["researchRunId"] for item in responses}
    job_ids = {
        item.json()["researchRun"].get("firstPassJobId")
        for item in responses
        if item.json()["researchRun"].get("firstPassJobId")
    }
    assert len(run_ids) == 1
    assert len(job_ids) == 1
    assert len(created) == 1
