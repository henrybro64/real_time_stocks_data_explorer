"""PostgreSQL Start re-arm for managed First-Pass runs (#366).

Skipped when DATABASE_URL is unset (tests/integration/conftest).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import (
    TwResearchFirstPassJob,
    TwResearchFirstPassSymbolResult,
    TwResearchResearchRun,
    User,
)
from app.db.session import SessionLocal
from app.main import app
from app.services.tw_research.research_run import research_run_store


pytestmark = pytest.mark.integration


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def _disable_drain(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_enabled", False)
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_user_id", "")
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_token", "")
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "",
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "",
    )


def _enable_drain(monkeypatch, user_id: str) -> None:
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_enabled", True)
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_user_id", user_id)
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_token", "pg-test-token")
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "pg-test-token",
    )


def _seed_run_and_job(user_id: str, *, schedule_enabled: bool = False) -> tuple[str, str]:
    assert SessionLocal is not None
    job_id = f"fpj_rearm_{uuid4().hex[:12]}"
    run_id = f"rr_rearm_{uuid4().hex[:12]}"
    stock_ids = [f"TW:{8200 + i}" for i in range(5)]
    with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        session.add(
            TwResearchFirstPassJob(
                job_id=job_id,
                user_id=user_id,
                universe_version="explicit:5",
                universe_digest="digest-rearm-5",
                membership_version=0,
                first_pass_version="test",
                stock_ids_json=stock_ids,
                selector_json={"mode": "stock_ids", "stockIds": stock_ids},
                status="pending",
                quality_status="incomplete",
                cursor=1,
                active_snapshot_id="fps_rearm_keep",
                notes_json=["seeded rearm"],
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TwResearchFirstPassSymbolResult(
                job_id=job_id,
                stock_id=stock_ids[0],
                status="ok",
                payload_json={"stockId": stock_ids[0], "fields": {}},
                lease_generation=0,
                updated_at=now,
            )
        )
        session.add(
            TwResearchResearchRun(
                research_run_id=run_id,
                user_id=user_id,
                version="test",
                phase="phase1_first_pass",
                status="running",
                first_pass_job_id=job_id,
                first_pass_snapshot_id="fps_rearm_keep",
                schedule_enabled=schedule_enabled,
                gates_json={"candidateSelection": "pending"},
                dimensions_json={"fundamentals": "in_progress"},
                notes_json=["seeded"],
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return run_id, job_id


def test_start_rearms_same_run_job_in_db(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"u_rearm_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _disable_drain(monkeypatch)
    run_id, job_id = _seed_run_and_job(user_id, schedule_enabled=False)

    _enable_drain(monkeypatch, user_id)
    # Bypass auth by depending on route with overridden user — use store path
    # directly for durable re-arm, then verify DB (route uses same store method).
    research_run_store.disable_test_memory()
    out = research_run_store.maybe_rearm_first_pass_drain_on_start(user_id)
    assert out.research_run_id == run_id
    assert out.first_pass_job_id == job_id
    assert out.schedule_enabled is True

    assert SessionLocal is not None
    with SessionLocal() as session:
        run = session.get(TwResearchResearchRun, user_id)
        assert run is not None
        assert run.research_run_id == run_id
        assert run.schedule_enabled is True
        assert run.first_pass_job_id == job_id
        assert run.first_pass_snapshot_id == "fps_rearm_keep"
        job_count = session.execute(
            select(func.count()).select_from(TwResearchFirstPassJob).where(
                TwResearchFirstPassJob.user_id == user_id
            )
        ).scalar_one()
        assert int(job_count) == 1
        sym = session.execute(
            select(TwResearchFirstPassSymbolResult).where(
                TwResearchFirstPassSymbolResult.job_id == job_id,
                TwResearchFirstPassSymbolResult.stock_id == "TW:8200",
            )
        ).scalar_one()
        assert sym.status == "ok"


def test_deletion_requested_blocks_rearm(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"u_del_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _disable_drain(monkeypatch)
    run_id, job_id = _seed_run_and_job(user_id, schedule_enabled=False)
    assert SessionLocal is not None
    with SessionLocal() as session:
        job = session.get(TwResearchFirstPassJob, job_id)
        assert job is not None
        job.deletion_state = "requested"
        session.commit()

    _enable_drain(monkeypatch, user_id)
    research_run_store.disable_test_memory()
    out = research_run_store.maybe_rearm_first_pass_drain_on_start(user_id)
    assert out.research_run_id == run_id
    assert out.schedule_enabled is False


def test_concurrent_start_rearm_no_409(db_session, monkeypatch) -> None:
    del db_session
    user_id = f"u_conc_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _disable_drain(monkeypatch)
    run_id, job_id = _seed_run_and_job(user_id, schedule_enabled=False)
    _enable_drain(monkeypatch, user_id)
    research_run_store.disable_test_memory()

    def _rearm() -> tuple[str, bool]:
        run = research_run_store.maybe_rearm_first_pass_drain_on_start(user_id)
        return run.research_run_id, bool(run.schedule_enabled)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [
            fut.result() for fut in as_completed([pool.submit(_rearm) for _ in range(6)])
        ]
    assert all(r[0] == run_id and r[1] is True for r in results)
    assert SessionLocal is not None
    with SessionLocal() as session:
        job_count = session.execute(
            select(func.count()).select_from(TwResearchFirstPassJob).where(
                TwResearchFirstPassJob.user_id == user_id
            )
        ).scalar_one()
        assert int(job_count) == 1
        run = session.get(TwResearchResearchRun, user_id)
        assert run is not None and run.schedule_enabled is True
        assert run.first_pass_job_id == job_id


def test_start_http_route_rearms(db_session, monkeypatch) -> None:
    """Exercise POST /research-runs/start re-arm path with TestClient."""
    del db_session
    user_id = f"u_http_{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _disable_drain(monkeypatch)
    run_id, job_id = _seed_run_and_job(user_id, schedule_enabled=False)
    _enable_drain(monkeypatch, user_id)
    research_run_store.disable_test_memory()

    from app.api.deps import get_effective_user_id

    app.dependency_overrides[get_effective_user_id] = lambda: user_id
    try:
        client = TestClient(app)
        response = client.post(
            "/api/tw-research/research-runs/start",
            json={"phase": "phase1_first_pass"},
        )
        assert response.status_code == 200, response.text
        body = response.json()["researchRun"]
        assert body["researchRunId"] == run_id
        assert body["firstPassJobId"] == job_id
        assert body["scheduleEnabled"] is True
    finally:
        app.dependency_overrides.pop(get_effective_user_id, None)
