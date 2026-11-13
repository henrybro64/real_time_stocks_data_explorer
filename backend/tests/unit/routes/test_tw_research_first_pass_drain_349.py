"""Route auth + wiring for first-pass drain (#349)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


client = TestClient(app)


def test_drain_endpoint_requires_configured_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_token", "")
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_enabled", False)
    response = client.post("/api/tw-research/first-pass/drain", json={"dryRun": True})
    assert response.status_code == 503


def test_drain_endpoint_rejects_bad_token(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "tw_research_first_pass_drain_token", "real-drain-token-abc123"
    )
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_enabled", True)
    response = client.post(
        "/api/tw-research/first-pass/drain",
        json={"dryRun": True},
        headers={"X-Tw-Research-First-Pass-Drain-Token": "wrong"},
    )
    assert response.status_code == 403


def test_drain_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "tw_research_first_pass_drain_token", "real-drain-token-abc123"
    )
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_first_pass_drain_enabled", True)

    def fake_drain(**kwargs):
        assert kwargs["user_id"] == "owner-1"
        assert kwargs["enabled"] is True
        assert kwargs["dry_run"] is True
        from app.services.tw_research.first_pass_drain import FirstPassDrainResult

        return FirstPassDrainResult(
            outcome="dry_run_ready",
            user_id="owner-1",
            research_run_id="rr_1",
            job_id="fpj_1",
            phase="phase1_first_pass",
            status="running",
            schedule_enabled=True,
            dry_run=True,
        )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.drain_authorized_first_pass",
        fake_drain,
    )
    response = client.post(
        "/api/tw-research/first-pass/drain",
        json={"dryRun": True, "maxBatches": 2, "batchSize": 10},
        headers={"X-Tw-Research-First-Pass-Drain-Token": "real-drain-token-abc123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "dry_run_ready"
    assert body["stopsAtAnalystGate"] is True
    assert body["notionWrites"] is False


def test_pause_resume_route_maps_conflict_to_409(monkeypatch) -> None:
    from app.services.tw_research.research_run import ResearchRunConflict

    def boom(*_a, **_k):
        raise ResearchRunConflict(
            "first_pass_drain_resume_requires_attached_job",
            reason="first_pass_drain_resume_requires_attached_job",
        )

    monkeypatch.setattr(
        "app.api.routes.tw_research.research_run_store.set_first_pass_drain_enabled",
        boom,
    )
    response = client.post(
        "/api/tw-research/research-runs/first-pass-drain",
        json={"enabled": True},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "research_run_revision_conflict"
    assert "requires_attached_job" in detail["reason"]


def test_batch_route_returns_409_when_job_failed(monkeypatch) -> None:
    systemic = "systemic_programming_error:tdcc_holders_ge_400_lots:TypeError"

    def fake_run_batch(job_id, *, user_id, **_kwargs):  # noqa: ANN001
        assert job_id == "fpj_x"
        return {
            "job": {
                "job_id": job_id,
                "user_id": user_id,
                "status": "failed",
                "notes": [f"error:{systemic}"],
                "active_snapshot_id": None,
            },
            "batchIds": [],
            "processed": 0,
            "leaseHeld": False,
            "alreadyComplete": False,
            "jobFailed": True,
            "lastError": systemic,
            "leaseLost": False,
        }

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.run_batch",
        fake_run_batch,
    )
    response = client.post(
        "/api/tw-research/first-pass/jobs/fpj_x/batches",
        json={"batchSize": 5},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "first_pass_job_failed"
    assert detail["lastError"] == systemic
