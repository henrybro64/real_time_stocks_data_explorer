"""First-pass job cancel/archive/delete routes (#355)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.tw_research.first_pass_job import FirstPassJobError


client = TestClient(app)


def _job(**overrides):
    base = {
        "job_id": "fpj_demo",
        "user_id": "default",
        "universe_version": "u1",
        "universe_digest": "d1",
        "membership_version": 1,
        "first_pass_version": "fp1",
        "stock_ids": ["2330"],
        "universe_size": 1,
        "selector": {},
        "status": "pending",
        "quality_status": "incomplete",
        "cursor": 0,
        "reserve_cursor": 0,
        "lease_generation": 1,
        "active_snapshot_id": None,
        "notes": [],
        "archived_at": None,
        "deletion_state": None,
        "deletion_requested_at": None,
        "deletion_error": None,
        "created_at": "2026-11-14T00:00:00+00:00",
        "updated_at": "2026-11-14T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def test_list_jobs_passes_include_archived(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_list(*, user_id, limit, include_archived=False):
        seen["user_id"] = user_id
        seen["limit"] = limit
        seen["include_archived"] = include_archived
        return [_job()]

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.list_jobs",
        fake_list,
    )
    response = client.get(
        "/api/tw-research/first-pass/jobs?includeArchived=true&limit=10"
    )
    assert response.status_code == 200
    assert seen["include_archived"] is True
    assert seen["limit"] == 10
    assert response.json()["jobs"][0]["jobId"] == "fpj_demo"


def test_cancel_route(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.cancel_job",
        lambda job_id, *, user_id: _job(status="cancelled", lease_generation=2),
    )
    response = client.post("/api/tw-research/first-pass/jobs/fpj_demo/cancel")
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "cancelled"
    assert response.json()["job"]["leaseGeneration"] == 2


def test_archive_conflict_maps_409(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise FirstPassJobError(
            "job_lease_active_cancel_first",
            operation="archive_first_pass_job",
        )

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.archive_job",
        boom,
    )
    response = client.post("/api/tw-research/first-pass/jobs/fpj_demo/archive")
    assert response.status_code == 409


def test_deletion_preview_route(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.deletion_preview",
        lambda job_id, *, user_id: {
            "job_id": job_id,
            "category": "pending",
            "closure": {"tw_research_first_pass_jobs": 1},
            "retained": [],
            "blockers": [],
            "requires_full_run_delete": False,
            "preview_token": "tok",
            "status": "pending",
            "lease_active": False,
            "deletion_state": None,
            "archived_at": None,
            "symbol_count": 0,
            "run_owned": False,
            "active_snapshot_id": None,
        },
    )
    response = client.get(
        "/api/tw-research/first-pass/jobs/fpj_demo/deletion-preview"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["previewToken"] == "tok"
    assert body["category"] == "pending"


def test_delete_returns_200(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.delete_job",
        lambda job_id, *, user_id, preview_token: {
            "outcome": "deleted",
            "idempotent": False,
            "counts": {"tw_research_first_pass_jobs": 1},
            "audit_id": "tda_1",
        },
    )
    response = client.request(
        "DELETE",
        "/api/tw-research/first-pass/jobs/fpj_demo",
        headers={"X-Deletion-Preview-Token": "tok"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_delete_running_returns_202(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.delete_job",
        lambda job_id, *, user_id, preview_token: {
            "outcome": "requested",
            "deletion_state": "requested",
            "job": _job(status="cancelled", deletion_state="requested"),
            "audit_id": "tda_2",
            "quiesce_after": "2026-11-14T01:00:00+00:00",
        },
    )
    response = client.request(
        "DELETE",
        "/api/tw-research/first-pass/jobs/fpj_demo",
        headers={"X-Deletion-Preview-Token": "tok"},
    )
    assert response.status_code == 202
    assert response.json()["deletionState"] == "requested"


def test_delete_conflict_409(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.delete_job",
        lambda job_id, *, user_id, preview_token: {
            "outcome": "conflict",
            "code": "run_owned_requires_full_run_delete",
            "preview": {"job_id": job_id},
        },
    )
    response = client.request(
        "DELETE",
        "/api/tw-research/first-pass/jobs/fpj_demo",
        headers={"X-Deletion-Preview-Token": "tok"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "run_owned_requires_full_run_delete"


def test_delete_missing_404(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.delete_job",
        lambda job_id, *, user_id, preview_token: {"outcome": "not_found"},
    )
    response = client.request(
        "DELETE",
        "/api/tw-research/first-pass/jobs/fpj_missing",
        headers={"X-Deletion-Preview-Token": "tok"},
    )
    assert response.status_code == 404


def test_deletion_status_410_when_gone(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.deletion_status",
        lambda job_id, *, user_id: {
            "job_id": job_id,
            "gone": True,
            "outcome": "deleted",
            "counts": {"tw_research_first_pass_jobs": 1},
            "completed_at": "2026-11-14T02:00:00+00:00",
            "audit_id": "tda_3",
        },
    )
    response = client.get("/api/tw-research/first-pass/jobs/fpj_demo/deletion")
    assert response.status_code == 410
    assert response.json()["gone"] is True


def test_research_run_deletion_preview_route(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.research_run_deletion_preview",
        lambda *, user_id: {
            "research_run_id": "rr_demo",
            "category": "research_run",
            "closure": {"tw_research_research_runs": 1},
            "retained": [],
            "blockers": [],
            "preview_token": "run_tok",
            "job_ids": ["fpj_demo"],
            "needs_quiesce": False,
            "deletion_requested": False,
        },
    )
    response = client.get("/api/tw-research/research-runs/current/deletion-preview")
    assert response.status_code == 200
    assert response.json()["previewToken"] == "run_tok"
    assert response.json()["requiresFullRunDelete"] is True


def test_delete_research_run_returns_200(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.delete_research_run",
        lambda *, user_id, preview_token: {
            "outcome": "deleted",
            "idempotent": False,
            "counts": {"tw_research_research_runs": 1},
            "audit_id": "tda_run",
            "research_run_id": "rr_demo",
        },
    )
    response = client.request(
        "DELETE",
        "/api/tw-research/research-runs/current",
        headers={"X-Deletion-Preview-Token": "run_tok"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_delete_research_run_returns_202(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.delete_research_run",
        lambda *, user_id, preview_token: {
            "outcome": "requested",
            "deletion_state": "requested",
            "research_run_id": "rr_demo",
            "audit_id": "tda_run2",
            "quiesce_after": "2026-11-14T01:00:00+00:00",
        },
    )
    response = client.request(
        "DELETE",
        "/api/tw-research/research-runs/current",
        headers={"X-Deletion-Preview-Token": "run_tok"},
    )
    assert response.status_code == 202
    assert response.json()["deletionState"] == "requested"
