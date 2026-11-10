"""Schedule token auth must work when AUTH_ENABLED=true (#330 Finding 4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.integration.local_auth_helpers import enable_local_auth


def test_schedule_run_works_with_token_when_auth_enabled(monkeypatch) -> None:  # noqa: ANN001
    enable_local_auth(monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "tw_research_schedule_token", "real-schedule-token-abc123")
    monkeypatch.setattr(settings, "tw_research_schedule_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_schedule_enabled", True)
    monkeypatch.setattr("app.main.seed_default_user", lambda: None)
    monkeypatch.setattr("app.main.ensure_bootstrap_local_user", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr("app.main.stop_background_jobs", lambda: None)

    with TestClient(app) as client:
        # Ordinary research route still requires a session.
        denied = client.get("/api/tw-research/contract")
        assert denied.status_code == 401

        # Schedule tick authenticates only via the dedicated capture token.
        ok = client.post(
            "/api/tw-research/schedule/run",
            json={"dryRun": True, "tradingDay": "2024-06-28"},
            headers={"X-Tw-Research-Schedule-Token": "real-schedule-token-abc123"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["outcome"] == "dry_run"
        assert body["tradingDay"] == "2024-06-28"

        # Schedule token must not unlock user research routes.
        still_denied = client.get(
            "/api/tw-research/contract",
            headers={"X-Tw-Research-Schedule-Token": "real-schedule-token-abc123"},
        )
        assert still_denied.status_code == 401


def test_schedule_token_does_not_bypass_user_scan_routes(monkeypatch) -> None:  # noqa: ANN001
    enable_local_auth(monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "tw_research_schedule_token", "real-schedule-token-abc123")
    monkeypatch.setattr(settings, "tw_research_schedule_user_id", "owner-1")
    monkeypatch.setattr(settings, "tw_research_schedule_enabled", True)
    monkeypatch.setattr("app.main.seed_default_user", lambda: None)
    monkeypatch.setattr("app.main.ensure_bootstrap_local_user", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr("app.main.stop_background_jobs", lambda: None)

    with TestClient(app) as client:
        response = client.post(
            "/api/tw-research/scans",
            json={
                "universe": {"mode": "stock_ids", "stockIds": ["TW:2330"]},
                "targetCount": 10,
                "tradingDay": "2024-06-28",
            },
            headers={"X-Tw-Research-Schedule-Token": "real-schedule-token-abc123"},
        )
        assert response.status_code == 401
