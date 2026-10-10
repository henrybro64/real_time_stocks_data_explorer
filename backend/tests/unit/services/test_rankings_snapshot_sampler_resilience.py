"""Rankings snapshot sampler must survive iteration failures (#266)."""

from __future__ import annotations

from app.jobs.health import get_background_jobs_health, reset_job_health_for_tests
from app.services.rankings_snapshot_service import RankingsSnapshotService


def test_rankings_sampler_continues_after_repository_failure(monkeypatch) -> None:
    reset_job_health_for_tests()
    service = RankingsSnapshotService()
    calls = {"count": 0}

    def boom(*, trigger: str) -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("postgres restarted")
        return 2

    monkeypatch.setattr(service, "capture_scheduled_snapshots", boom)

    wait_state = {"count": 0}

    def fake_wait(timeout=None):
        if timeout == 0:
            wait_state["count"] += 1
            return wait_state["count"] > 2
        return False if wait_state["count"] <= 2 else True

    monkeypatch.setattr(service._stop_event, "wait", fake_wait)
    service._run_sampler(poll_seconds=1)

    assert calls["count"] == 2
    health = get_background_jobs_health()["rankings-snapshot-sampler"]
    assert health["last_success_at"] is not None
    assert health["last_error_type"] == "RuntimeError"
