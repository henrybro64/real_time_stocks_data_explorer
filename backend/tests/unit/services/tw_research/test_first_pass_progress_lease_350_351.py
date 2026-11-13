"""#350/#351: additive lease/retry honesty fields on FirstPassJobService.progress()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.repositories.tw_research_repository import BATCH_LEASE_SECONDS
from tests.unit.services.tw_research.test_first_pass_budget_circuit_348_349 import (
    _base_collectors,
    _install_job_repo,
)


def _service(monkeypatch, user_id: str):
    _, repo = _install_job_repo(monkeypatch, user_id)
    from app.services.tw_research.first_pass_job import FirstPassJobService

    return FirstPassJobService(repository=repo), repo


def test_progress_exposes_additive_lease_fields(monkeypatch) -> None:
    service, repo = _service(monkeypatch, "user-prog-fields")
    job = service.create_job(
        user_id="user-prog-fields",
        stock_ids=["TW:1000", "TW:1001"],
        use_membership=False,
    )
    progress = service.progress(job["job_id"], user_id="user-prog-fields")
    for key in ("leaseActive", "leaseExpiresAt", "updatedAt", "nextRetryAt"):
        assert key in progress
    assert progress["leaseActive"] is False
    assert progress["leaseExpiresAt"] is None
    assert progress["nextRetryAt"] is None
    assert progress["updatedAt"]
    # Existing contract preserved.
    assert progress["universeSize"] == 2
    assert progress["pendingCount"] == 2
    repo.clear_session_factory_override()


def test_lease_active_during_claim_and_false_after_complete(monkeypatch) -> None:
    service, repo = _service(monkeypatch, "user-prog-lease")
    job = service.create_job(
        user_id="user-prog-lease",
        stock_ids=["TW:1000", "TW:1001", "TW:1002"],
        use_membership=False,
    )
    claim = repo.claim_first_pass_batch(job["job_id"], batch_size=2)
    assert claim and claim["batch_ids"]
    during = service.progress(job["job_id"], user_id="user-prog-lease")
    assert during["leaseActive"] is True
    expires = datetime.fromisoformat(during["leaseExpiresAt"])
    remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 0 < remaining <= BATCH_LEASE_SECONDS + 1

    completed = repo.complete_first_pass_batch(
        job["job_id"],
        lease_generation=claim["lease_generation"],
        processed_count=0,
    )
    assert completed is not None
    after = service.progress(job["job_id"], user_id="user-prog-lease")
    assert after["leaseActive"] is False
    assert after["leaseExpiresAt"] is None
    repo.clear_session_factory_override()


def test_expired_lease_is_not_active(monkeypatch) -> None:
    service, repo = _service(monkeypatch, "user-prog-expired")
    job = service.create_job(
        user_id="user-prog-expired",
        stock_ids=["TW:1000", "TW:1001"],
        use_membership=False,
    )
    claim = repo.claim_first_pass_batch(job["job_id"], batch_size=1, lease_seconds=1)
    assert claim and claim["batch_ids"]
    from app.db.models import TwResearchFirstPassJob

    session_factory = repo._session_factory()
    with session_factory() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        row.reserve_expires_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        session.commit()
    progress = service.progress(job["job_id"], user_id="user-prog-expired")
    assert progress["leaseActive"] is False
    repo.clear_session_factory_override()


def test_next_retry_at_is_earliest_deferred_row(monkeypatch) -> None:
    service, repo = _service(monkeypatch, "user-prog-retry")
    job = service.create_job(
        user_id="user-prog-retry",
        stock_ids=["TW:1000", "TW:1001", "TW:1002", "TW:1003"],
        use_membership=False,
    )
    claim = repo.claim_first_pass_batch(job["job_id"], batch_size=4)
    gen = claim["lease_generation"]
    now = datetime.now(timezone.utc)
    later = (now + timedelta(minutes=10)).isoformat()
    earliest = (now + timedelta(minutes=2)).isoformat()
    exhausted_sooner = (now + timedelta(seconds=5)).isoformat()
    rows = [
        ("TW:1000", "failed", {"nextRetryAt": later, "retryExhausted": False}),
        ("TW:1001", "failed", {"nextRetryAt": earliest, "retryExhausted": False}),
        # Exhausted rows never count as scheduled retries.
        ("TW:1002", "failed", {"nextRetryAt": exhausted_sooner, "retryExhausted": True}),
        ("TW:1003", "ok", {"nextRetryAt": None}),
    ]
    for stock_id, status, payload in rows:
        assert repo.upsert_first_pass_symbol(
            job_id=job["job_id"],
            stock_id=stock_id,
            status=status,
            payload=payload,
            lease_generation=gen,
        )
    repo.complete_first_pass_batch(
        job["job_id"], lease_generation=gen, processed_count=4
    )
    expected = datetime.fromisoformat(earliest).astimezone(timezone.utc).isoformat()
    assert repo.earliest_first_pass_next_retry_at(job["job_id"]) == expected
    progress = service.progress(job["job_id"], user_id="user-prog-retry")
    assert progress["nextRetryAt"] == expected
    assert progress["leaseActive"] is False
    repo.clear_session_factory_override()


def test_next_retry_at_none_without_scheduled_retries(monkeypatch) -> None:
    service, repo = _service(monkeypatch, "user-prog-noretry")
    job = service.create_job(
        user_id="user-prog-noretry",
        stock_ids=["TW:1000"],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-prog-noretry",
        batch_size=1,
        collectors=_base_collectors(),
        time_budget_seconds=90,
    )
    assert result["processed"] == 1
    progress = service.progress(job["job_id"], user_id="user-prog-noretry")
    assert progress["leaseActive"] is False
    assert progress["nextRetryAt"] is None
    repo.clear_session_factory_override()
