"""Honest execution readiness classification for managed First-Pass start (#359)."""

from __future__ import annotations

from app.services.tw_research.execution_status import classify_first_pass_execution
from app.services.tw_research.research_run import ResearchRun


def _run(**overrides):
    base = ResearchRun(
        research_run_id="rr_test",
        user_id="u1",
        phase="phase1_first_pass",
        status="running",
        first_pass_job_id="fpj_1",
        schedule_enabled=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_executor_unavailable_when_drain_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )
    result = classify_first_pass_execution(
        _run(),
        job={
            "status": "pending",
            "universe_size": 73,
            "ok_count": 1,
            "partial_count": 0,
            "failed_count": 0,
        },
    )
    assert result["state"] == "executor_unavailable"
    assert result["authorized"] is False
    assert result["blocker"] == "first_pass_drain_globally_disabled"
    assert result["processedCount"] == 1
    assert result["pendingCount"] == 72


def test_execution_authorized_when_drain_fresh(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    from datetime import UTC, datetime

    result = classify_first_pass_execution(
        _run(
            schedule_enabled=True,
            dimensions={
                "firstPassDrain": {
                    "lastTickAt": datetime.now(UTC).isoformat(),
                }
            },
        ),
        job={"status": "running", "universe_size": 10, "ok_count": 0},
    )
    assert result["state"] == "execution_authorized"
    assert result["authorized"] is True
    assert result["nextAction"] == "waiting_for_drain_tick"


def test_executor_not_ticking_when_last_tick_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_first_pass_execution(
        _run(
            schedule_enabled=True,
            dimensions={"firstPassDrain": {"lastTickAt": "2020-01-01T00:00:00+00:00"}},
        ),
        job={"status": "pending", "universe_size": 5, "ok_count": 0},
    )
    assert result["state"] == "executor_not_ticking"
    assert result["blocker"] == "first_pass_drain_not_ticking"
    assert result["nextAction"] == "wait_or_contact_operator"


def test_stalled_when_mid_progress_and_tick_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_first_pass_execution(
        _run(
            schedule_enabled=True,
            dimensions={"firstPassDrain": {"lastTickAt": "2020-01-01T00:00:00+00:00"}},
        ),
        job={
            "status": "running",
            "universe_size": 73,
            "ok_count": 10,
            "partial_count": 0,
            "failed_count": 0,
            "updated_at": "2026-11-17T02:00:00+00:00",
        },
    )
    assert result["state"] == "stalled"
    assert result["jobUpdatedAt"] == "2026-11-17T02:00:00+00:00"
    assert result["processedCount"] == 10
    assert result["pendingCount"] == 63
    assert result["authorized"] is True


def test_last_data_progress_at_is_distinct_from_job_updated_at(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )
    result = classify_first_pass_execution(
        _run(),
        job={
            "status": "pending",
            "universe_size": 73,
            "ok_count": 0,
            "updated_at": "2026-11-17T04:00:00+00:00",
        },
        last_data_progress_at="2026-11-17T03:00:00+00:00",
    )
    assert result["jobUpdatedAt"] == "2026-11-17T04:00:00+00:00"
    assert result["lastDataProgressAt"] == "2026-11-17T03:00:00+00:00"
    assert result["lastDataProgressAt"] != result["jobUpdatedAt"]


def test_missing_last_data_progress_at_stays_null(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )
    result = classify_first_pass_execution(
        _run(),
        job={"status": "pending", "universe_size": 73, "ok_count": 0},
        last_data_progress_at=None,
    )
    assert result["lastDataProgressAt"] is None


def test_running_status_without_lease_is_not_actively_collecting(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )
    result = classify_first_pass_execution(
        _run(status="running", schedule_enabled=False),
        job={
            "status": "pending",
            "universe_size": 73,
            "ok_count": 0,
            "partial_count": 0,
            "failed_count": 0,
        },
    )
    assert result["state"] == "executor_unavailable"
    assert result["state"] != "actively_collecting"
    assert result["processedCount"] == 0
    assert result["pendingCount"] == 73


def test_awaiting_analyst_after_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_first_pass_execution(
        _run(
            phase="awaiting_analyst_candidate_selection",
            status="gated",
            schedule_enabled=False,
        ),
        job={"status": "completed"},
    )
    assert result["state"] == "awaiting_analyst_candidate_selection"
