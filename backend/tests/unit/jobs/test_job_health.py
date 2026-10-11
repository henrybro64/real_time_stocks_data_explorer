"""Background job health registry (#266)."""

from __future__ import annotations

from app.jobs.health import (
    JOB_TICK_INCOMPLETE,
    get_background_jobs_health,
    note_job_success,
    register_background_job,
    reset_job_health_for_tests,
    run_guarded_tick,
)


def test_run_guarded_tick_records_success_and_keeps_going() -> None:
    reset_job_health_for_tests()
    register_background_job("demo-job", enabled=True)
    calls = {"count": 0}

    def boom() -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("transient")

    run_guarded_tick("demo-job", event="demo.failed", tick=boom)
    run_guarded_tick("demo-job", event="demo.failed", tick=boom)
    note_job_success("demo-job")

    health = get_background_jobs_health()["demo-job"]
    assert health["enabled"] is True
    assert health["alive"] is False
    assert health["last_error_type"] == "ValueError"
    assert health["last_success_at"] is not None
    assert calls["count"] == 2


def test_incomplete_tick_does_not_advance_last_success() -> None:
    reset_job_health_for_tests()
    register_background_job("demo-job", enabled=True)

    run_guarded_tick(
        "demo-job",
        event="demo.failed",
        tick=lambda: JOB_TICK_INCOMPLETE,
    )

    health = get_background_jobs_health()["demo-job"]
    assert health["last_success_at"] is None
    assert health["last_error_at"] is None
    assert health["alive"] is False


def test_stale_enabled_job_is_unhealthy() -> None:
    from datetime import UTC, datetime, timedelta

    from app.jobs.health import unhealthy_job_names

    reset_job_health_for_tests()
    register_background_job("demo-job", enabled=True, max_stale_seconds=10)
    note_job_success("demo-job")
    assert unhealthy_job_names() == []
    from app.jobs import health as health_module

    with health_module._lock:
        health_module._jobs["demo-job"].last_success_at = datetime.now(UTC) - timedelta(
            seconds=30
        )
    assert unhealthy_job_names() == ["demo-job"]
