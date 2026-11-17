"""Research Run created_at refreshes only when research_run_id changes (#362 D1)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.models import TwResearchResearchRun, User
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import tw_research_repository

pytestmark = pytest.mark.integration


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def _as_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def test_new_research_run_id_refreshes_created_at(db_session) -> None:
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"rr-created-{uuid4().hex[:8]}"
    _ensure_user(user_id)

    old_created = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)
    tw_research_repository.upsert_research_run(
        {
            "user_id": user_id,
            "research_run_id": "rr_old_identity_0001",
            "phase": "phase1_first_pass",
            "status": "failed",
            "revision": 0,
            "gates": {},
            "dimensions": {},
            "notes": ["finished"],
            "schedule_enabled": False,
        }
    )
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchResearchRun, user_id)
        assert row is not None
        row.created_at = old_created
        row.updated_at = old_created
        row.revision = 1
        session.commit()
        revision = int(row.revision)

    same = tw_research_repository.upsert_research_run(
        {
            "user_id": user_id,
            "research_run_id": "rr_old_identity_0001",
            "phase": "phase1_first_pass",
            "status": "failed",
            "revision": revision,
            "gates": {},
            "dimensions": {},
            "notes": ["touch"],
            "schedule_enabled": False,
            "created_at": "2026-11-17T03:17:00+00:00",
        }
    )
    assert _as_dt(same["created_at"]) == old_created

    new_created = datetime(2026, 11, 17, 3, 17, tzinfo=timezone.utc)
    replaced = tw_research_repository.upsert_research_run(
        {
            "user_id": user_id,
            "research_run_id": "rr_new_identity_0002",
            "phase": "phase1_first_pass",
            "status": "queued",
            "revision": int(same["revision"]),
            "gates": {},
            "dimensions": {},
            "notes": ["new run"],
            "schedule_enabled": False,
            "created_at": new_created.isoformat(),
        }
    )
    assert replaced["research_run_id"] == "rr_new_identity_0002"
    assert _as_dt(replaced["created_at"]) == new_created
