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


def _finished_thread():
    import threading

    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return thread


def test_stale_job_is_unhealthy_but_not_dead() -> None:
    from datetime import UTC, datetime, timedelta

    from app.jobs import health as health_module
    from app.jobs.health import dead_job_names, unhealthy_job_names

    reset_job_health_for_tests()
    register_background_job("demo-job", enabled=True, max_stale_seconds=10)
    with health_module._lock:
        health_module._jobs["demo-job"].registered_at = datetime.now(UTC) - timedelta(
            seconds=30
        )
    # Stale (for example Postgres down) is a readiness problem, not a liveness one (#289).
    assert unhealthy_job_names() == ["demo-job"]
    assert dead_job_names() == []


def test_dead_thread_is_both_dead_and_unhealthy() -> None:
    from app.jobs.health import dead_job_names, unhealthy_job_names

    reset_job_health_for_tests()
    register_background_job("demo-job", enabled=True, thread=_finished_thread())
    assert dead_job_names() == ["demo-job"]
    assert unhealthy_job_names() == ["demo-job"]


def test_disabled_or_threadless_jobs_are_not_dead() -> None:
    from app.jobs.health import dead_job_names

    reset_job_health_for_tests()
    register_background_job("off-job", enabled=False, thread=_finished_thread())
    register_background_job("no-thread-job", enabled=True)
    assert dead_job_names() == []
