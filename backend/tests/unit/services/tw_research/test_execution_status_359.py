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
        job={"status": "pending", "universe_size": 73, "cursor": 1},
    )
    assert result["state"] == "executor_unavailable"
    assert result["authorized"] is False
    assert result["blocker"] == "first_pass_drain_globally_disabled"
    assert result["pendingCount"] == 72


def test_execution_authorized_when_drain_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_first_pass_execution(
        _run(schedule_enabled=True),
        job={"status": "running", "universe_size": 10, "cursor": 0},
    )
    assert result["state"] == "execution_authorized"
    assert result["authorized"] is True
    assert result["nextAction"] == "waiting_for_drain_tick"


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
