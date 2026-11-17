"""Start outcome must reflect durable claim/attach state (#361/#362 P1-A)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.services.tw_research.research_run import ResearchRun, research_run_store


client = TestClient(app)


def _start() -> dict:
    response = client.post(
        "/api/tw-research/research-runs/start",
        json={"phase": "phase1_first_pass", "tradingDay": "2024-06-27"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_first_start_reports_job_created_when_attach_wins(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    jobs: list[str] = []

    def fake_create_job(**kwargs):  # noqa: ANN003
        job_id = str(kwargs.get("job_id") or f"fpj_{len(jobs) + 1}")
        jobs.append(job_id)
        return {"job_id": job_id, "status": "queued"}

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.create_job",
        fake_create_job,
    )
    payload = _start()
    assert payload["startOutcome"] == "job_created"
    assert payload["researchRun"]["firstPassJobId"] == jobs[0]
    research_run_store.disable_test_memory()


def test_duplicate_start_reports_job_resumed_not_created(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    jobs: list[str] = []

    def fake_create_job(**kwargs):  # noqa: ANN003
        job_id = str(kwargs.get("job_id") or f"fpj_{len(jobs) + 1}")
        jobs.append(job_id)
        return {"job_id": job_id, "status": "queued"}

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.create_job",
        fake_create_job,
    )
    first = _start()
    second = _start()
    assert first["startOutcome"] == "job_created"
    assert second["startOutcome"] in {"job_resumed", "drain_rearmed"}
    assert second["startOutcome"] != "job_created"
    assert len(jobs) == 1
    assert (
        second["researchRun"]["firstPassJobId"]
        == first["researchRun"]["firstPassJobId"]
        == jobs[0]
    )
    research_run_store.disable_test_memory()


def test_claim_held_reports_creation_in_progress(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    from app.db.init_ids import DEFAULT_USER_ID

    begin = research_run_store.begin_phase1(
        DEFAULT_USER_ID,
        trading_day="2024-06-27",
        notes=[],
    )
    claim = research_run_store.claim_first_pass_job_slot(
        DEFAULT_USER_ID, claim_token="claim_holder_alpha"
    )
    assert claim["outcome"] == "claimed"
    assert begin.first_pass_job_id is None

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.create_job",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    payload = _start()
    assert payload["startOutcome"] == "creation_in_progress"
    assert payload["researchRun"]["firstPassJobId"] in (None, "")
    assert payload["researchRun"]["pendingFirstPassJobId"]
    research_run_store.disable_test_memory()


def test_claim_lost_attach_does_not_report_job_created(monkeypatch) -> None:
    """Loser created a job row but attach lost the claim — outcome is not job_created."""
    research_run_store.clear_for_tests()
    from app.db.init_ids import DEFAULT_USER_ID

    research_run_store.begin_phase1(
        DEFAULT_USER_ID,
        trading_day="2024-06-27",
        notes=[],
    )
    # Authoritative job already attached by a concurrent winner.
    winner = research_run_store.claim_first_pass_job_slot(
        DEFAULT_USER_ID, claim_token="claim_winner"
    )
    pending = str(winner["pending_first_pass_job_id"])
    research_run_store.attach_first_pass_job_under_claim(
        DEFAULT_USER_ID,
        claim_token="claim_winner",
        job_id=pending,
        notes=["winner"],
    )

    # Direct unit of the attach helper: claim_lost with authoritative job present.
    from app.api.routes import tw_research as routes

    monkeypatch.setattr(
        research_run_store,
        "attach_first_pass_job_under_claim",
        lambda *a, **k: {
            "outcome": "claim_lost",
            "orphan_job_id": "fpj_loser_orphan",
            "first_pass_job_id": pending,
            "run": research_run_store.get(DEFAULT_USER_ID).as_persist_payload(),
        },
    )
    orphaned: list[str] = []
    monkeypatch.setattr(
        research_run_store,
        "mark_orphaned_first_pass_job",
        lambda job_id, **kwargs: orphaned.append(str(job_id)),
    )
    response = routes._attach_created_first_pass_job(  # noqa: SLF001
        user_id=DEFAULT_USER_ID,
        claim_token="claim_loser",
        job_id="fpj_loser_orphan",
    )
    assert response.startOutcome in {"job_resumed", "drain_rearmed"}
    assert response.startOutcome != "job_created"
    assert response.researchRun["firstPassJobId"] == pending
    assert orphaned == ["fpj_loser_orphan"]
    research_run_store.disable_test_memory()


def test_expired_claim_allows_new_create(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    from app.db.init_ids import DEFAULT_USER_ID

    research_run_store.begin_phase1(
        DEFAULT_USER_ID,
        trading_day="2024-06-27",
        notes=[],
    )
    claim = research_run_store.claim_first_pass_job_slot(
        DEFAULT_USER_ID, claim_token="claim_expired"
    )
    assert claim["outcome"] == "claimed"
    run = research_run_store.get(DEFAULT_USER_ID)
    assert run is not None
    run.job_claim_expires_at = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()

    jobs: list[str] = []

    def fake_create_job(**kwargs):  # noqa: ANN003
        job_id = str(kwargs.get("job_id") or f"fpj_{len(jobs) + 1}")
        jobs.append(job_id)
        return {"job_id": job_id, "status": "queued"}

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.create_job",
        fake_create_job,
    )
    payload = _start()
    assert payload["startOutcome"] == "job_created"
    assert payload["researchRun"]["firstPassJobId"] == jobs[0]
    research_run_store.disable_test_memory()


def test_start_outcome_helper_maps_durable_states() -> None:
    from app.api.routes import tw_research as routes

    created = ResearchRun(
        research_run_id="rr_a",
        user_id="u1",
        phase="phase1_first_pass",
        status="running",
        first_pass_job_id="fpj_a",
    )
    assert (
        routes._start_outcome_from_durable_run(  # noqa: SLF001
            created, created_job_id="fpj_a", attach_outcome="attached"
        )
        == "job_created"
    )
    assert (
        routes._start_outcome_from_durable_run(  # noqa: SLF001
            created, created_job_id="fpj_other", attach_outcome="claim_lost"
        )
        == "job_resumed"
    )
    pending = ResearchRun(
        research_run_id="rr_b",
        user_id="u1",
        phase="phase1_first_pass",
        status="queued",
        pending_first_pass_job_id="fpj_pending",
        job_claim_token="claim_x",
    )
    assert (
        routes._start_outcome_from_durable_run(pending)  # noqa: SLF001
        == "creation_in_progress"
    )
