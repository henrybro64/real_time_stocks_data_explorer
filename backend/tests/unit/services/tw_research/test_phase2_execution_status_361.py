"""Phase-2 execution classification for managed technical scans (#361)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.tw_research.execution_status import classify_phase2_execution
from app.services.tw_research.research_run import ResearchRun


def _run(**overrides):
    base = ResearchRun(
        research_run_id="rr_p2",
        user_id="u1",
        phase="phase2_technical",
        status="running",
        technical_scan_id="scan_1",
        gates={"candidateSelection": "approved"},
        schedule_enabled=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_awaiting_technical_start_when_approved_without_scan(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(technical_scan_id=None),
        scan=None,
    )
    assert result is not None
    assert result["state"] == "awaiting_technical_start"
    assert result["authorized"] is False


def test_technical_scan_created_when_drain_available_no_tick(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(),
        scan={"status": "queued", "remainingCount": 5, "universeSize": 5, "cursor": 0},
    )
    assert result["state"] == "technical_scan_created"
    assert result["nextAction"] == "waiting_for_drain_tick"


def test_executor_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )
    result = classify_phase2_execution(
        _run(),
        scan={"status": "queued", "remainingCount": 2},
    )
    assert result["state"] == "executor_unavailable"
    assert result["blocker"] == "first_pass_drain_globally_disabled"


def test_executor_not_ticking_when_stale(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(
            dimensions={
                "firstPassDrain": {"lastTickAt": "2020-01-01T00:00:00+00:00"}
            }
        ),
        scan={"status": "queued", "remainingCount": 2},
    )
    assert result["state"] == "executor_not_ticking"


def test_fresh_tick_without_lease_is_between_batches(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(
            dimensions={
                "firstPassDrain": {"lastTickAt": datetime.now(UTC).isoformat()}
            }
        ),
        scan={
            "status": "running",
            "remainingCount": 3,
            "processedCount": 2,
            "universeSize": 5,
        },
    )
    assert result["state"] == "between_batches"
    assert result["leaseActive"] is False


def test_actively_analyzing_requires_live_lease(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    clock = datetime(2024, 6, 28, 4, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.services.tw_research.execution_status._phase2_now",
        lambda: clock,
    )
    result = classify_phase2_execution(
        _run(
            dimensions={
                "firstPassDrain": {"lastTickAt": clock.isoformat()}
            }
        ),
        scan={
            "status": "running",
            "remainingCount": 3,
            "processedCount": 2,
            "universeSize": 5,
            "reserveExpiresAt": "2024-06-28T04:10:00+00:00",
        },
    )
    assert result["state"] == "actively_analyzing"
    assert result["leaseActive"] is True


def test_expired_lease_not_actively_analyzing(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    clock = datetime(2024, 6, 28, 4, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.services.tw_research.execution_status._phase2_now",
        lambda: clock,
    )
    result = classify_phase2_execution(
        _run(
            dimensions={
                "firstPassDrain": {"lastTickAt": clock.isoformat()}
            }
        ),
        scan={
            "status": "running",
            "remainingCount": 3,
            "processedCount": 2,
            "universeSize": 5,
            "reserveExpiresAt": "2024-06-28T03:50:00+00:00",
        },
    )
    assert result["state"] == "between_batches"
    assert result["leaseActive"] is False


def test_waiting_for_budget_window(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(status="budget_exhausted"),
        scan={"status": "budget_exhausted", "remainingCount": 2},
    )
    assert result["state"] == "waiting_for_budget_window"


def test_completed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(
            phase="awaiting_analyst_report_decision",
            status="gated",
        ),
        scan={"status": "completed_processing", "remainingCount": 0},
    )
    assert result["state"] == "completed"


def test_failed_cancelled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(status="cancelled"),
        scan={"status": "cancelled"},
    )
    assert result["state"] == "failed"
