"""Schedule tick route stays disabled without a real capture token (#330)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app


def test_schedule_run_requires_configured_token(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "tw_research_schedule_token", "")
    monkeypatch.setattr(settings, "tw_research_schedule_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_schedule_enabled", False)
    client = TestClient(app)
    response = client.post("/api/tw-research/schedule/run", json={"dryRun": True})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_schedule_run_rejects_bad_token(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "tw_research_schedule_token", "real-schedule-token-abc123")
    monkeypatch.setattr(settings, "tw_research_schedule_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_schedule_enabled", True)
    client = TestClient(app)
    response = client.post(
        "/api/tw-research/schedule/run",
        json={"dryRun": True},
        headers={"X-Tw-Research-Schedule-Token": "wrong"},
    )
    assert response.status_code == 403


def test_schedule_run_dry_run_with_token(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "tw_research_schedule_token", "real-schedule-token-abc123")
    monkeypatch.setattr(settings, "tw_research_schedule_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_schedule_enabled", True)
    client = TestClient(app)
    response = client.post(
        "/api/tw-research/schedule/run",
        json={"dryRun": True, "tradingDay": "2024-06-28"},
        headers={"X-Tw-Research-Schedule-Token": "real-schedule-token-abc123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "dry_run"
    assert body["dryRun"] is True
    assert body["tradingDay"] == "2024-06-28"
