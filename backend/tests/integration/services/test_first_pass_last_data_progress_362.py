"""lastDataProgressAt tracks symbol evidence persists, not job admin updates (#362)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.models import TwResearchFirstPassJob, User
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.first_pass_job import FirstPassJobService

pytestmark = pytest.mark.integration


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def test_job_admin_update_does_not_advance_last_data_progress(db_session) -> None:
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"ldp-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(
        user_id=user_id,
        stock_ids=["TW:2330", "TW:2317", "TW:2454"],
        use_membership=False,
    )
    job_id = str(job["job_id"])

    assert (
        tw_research_repository.latest_first_pass_symbol_data_progress_at(job_id)
        is None
    )

    claim = tw_research_repository.claim_first_pass_batch(job_id, batch_size=2)
    assert claim is not None
    gen = int(claim["lease_generation"])
    stock_id = list(claim["batch_ids"])[0]
    assert tw_research_repository.upsert_first_pass_symbol(
        job_id=job_id,
        stock_id=stock_id,
        status="ok",
        payload={"stockId": stock_id, "attemptCount": 1},
        lease_generation=gen,
    )

    progress_at = tw_research_repository.latest_first_pass_symbol_data_progress_at(
        job_id
    )
    assert progress_at is not None

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchFirstPassJob, job_id)
        assert row is not None
        # Administrative / lease-style job update without new symbol evidence.
        row.updated_at = datetime.now(timezone.utc) + timedelta(hours=2)
        session.commit()
        job_updated = row.updated_at.isoformat()

    still = tw_research_repository.latest_first_pass_symbol_data_progress_at(job_id)
    assert still == progress_at
    assert still != job_updated

    # Completing the batch also bumps job.updated_at but not symbol max unless new rows.
    tw_research_repository.complete_first_pass_batch(
        job_id, lease_generation=gen, processed_count=1
    )
    after_complete = tw_research_repository.latest_first_pass_symbol_data_progress_at(
        job_id
    )
    assert after_complete == progress_at
