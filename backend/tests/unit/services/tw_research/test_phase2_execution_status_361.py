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


def test_paused_scheduling_keeps_active_lease_and_progress(monkeypatch) -> None:
    """phase2Enabled=false must not hide an in-flight worker or wipe counts (#362)."""
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
                "phase2Drain": {"enabled": False, "lastTickAt": clock.isoformat()},
                "firstPassDrain": {"lastTickAt": clock.isoformat()},
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
    assert result["phase2Enabled"] is False
    assert result["authorized"] is False
    assert result["processedCount"] == 2
    assert result["remainingCount"] == 3
    assert result["universeSize"] == 5
    assert result["blocker"] == "phase2_scheduling_paused"


def test_paused_without_lease_is_paused_but_keeps_scan_progress(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(dimensions={"phase2Drain": {"enabled": False}}),
        scan={
            "status": "queued",
            "remainingCount": 4,
            "processedCount": 1,
            "universeSize": 5,
        },
    )
    assert result["state"] == "paused"
    assert result["phase2Enabled"] is False
    assert result["processedCount"] == 1
    assert result["remainingCount"] == 4
    assert result["scanStatus"] == "queued"
    assert result["leaseActive"] is False


def test_stale_budget_outcome_does_not_override_completion(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(
            phase="awaiting_analyst_report_decision",
            status="gated",
            dimensions={
                "phase2Drain": {
                    "enabled": True,
                    "lastOutcome": "phase2_budget_cap_reached",
                }
            },
        ),
        scan={
            "status": "completed_processing",
            "remainingCount": 0,
            "processedCount": 5,
            "universeSize": 5,
        },
    )
    assert result["state"] == "completed"
    assert result["state"] != "waiting_for_budget_window"
    assert result["nextAction"] == "review_evidence"


def test_budget_cap_still_applies_when_work_remains(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    result = classify_phase2_execution(
        _run(
            status="budget_exhausted",
            dimensions={
                "phase2Drain": {"lastOutcome": "phase2_budget_cap_reached"}
            },
        ),
        scan={
            "status": "budget_exhausted",
            "remainingCount": 3,
            "processedCount": 2,
            "universeSize": 5,
        },
    )
    assert result["state"] == "waiting_for_budget_window"
    assert result["blocker"] == "phase2_budget_cap_reached"


def test_analyzed_count_excludes_failed_and_insufficient(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (True, None),
    )
    from types import SimpleNamespace

    from app.services.tw_research.constants import HISTORY_BUDGET_EXHAUSTED_REASON

    scan_state = SimpleNamespace(
        status="running",
        stock_ids=("TW:1", "TW:2", "TW:3", "TW:4", "TW:5"),
        records={
            "TW:1": SimpleNamespace(rejection_reason=None, readiness="processed"),
            "TW:2": SimpleNamespace(
                rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
                readiness="source_incomplete",
            ),
            "TW:3": SimpleNamespace(
                rejection_reason="symbol_error:Timeout", readiness="rejected"
            ),
            # TW:4 pending (no record)
            "TW:5": SimpleNamespace(
                rejection_reason=None, readiness="technically_qualified"
            ),
        },
    )
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.schedule_has_technical_work",
        lambda state: True,
    )
    result = classify_phase2_execution(
        _run(),
        scan={"status": "running", "universeSize": 5, "remainingCount": 1},
        scan_state=scan_state,
    )
    assert result["analyzedCount"] == 2
    assert result["insufficientHistoryCount"] == 1
    assert result["failedCount"] == 1
    assert result["pendingCount"] == 1
    # Buckets are mutually exclusive and sum to universe.
    assert (
        result["analyzedCount"]
        + result["insufficientHistoryCount"]
        + result["failedCount"]
        + result["pendingCount"]
        == 5
    )
