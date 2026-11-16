"""Phase-2 run-owned technical drain on the First-Pass CronJob path (#361)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.tw_research.constants import (
    HISTORY_BUDGET_EXHAUSTED_REASON,
    PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
)
from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.research_run import research_run_store
from app.services.tw_research.scan import TwResearchConflictError

TAIPEI = ZoneInfo("Asia/Taipei")


def setup_function() -> None:
    research_run_store.clear_for_tests()


def teardown_function() -> None:
    research_run_store.disable_test_memory()


def _enable(monkeypatch, user_id: str = "owner") -> None:
    for mod in (
        "app.services.tw_research.research_run.settings",
        "app.services.tw_research.first_pass_drain.settings",
    ):
        monkeypatch.setattr(f"{mod}.tw_research_first_pass_drain_enabled", True)
        monkeypatch.setattr(f"{mod}.tw_research_first_pass_drain_user_id", user_id)
        monkeypatch.setattr(
            f"{mod}.tw_research_first_pass_drain_token", "unit-test-drain-token"
        )


def _approved_phase2_run(*, scan_id: str = "scan_owned", enabled: bool = True) -> None:
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_1")
    with research_run_store._lock:  # noqa: SLF001 — test fixture
        locked = research_run_store._require_locked("owner")
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.schedule_enabled = False
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = scan_id
        locked.candidate_set_id = "cand_1"
        locked.candidate_set_version = 1
        locked.dimensions["phase2Drain"] = {"enabled": enabled}
        research_run_store._persist_locked(locked)


class _Rec:
    def __init__(self, *, rejection_reason: str | None = None, readiness: str = "processed"):
        self.rejection_reason = rejection_reason
        self.readiness = readiness


def _fake_scan(
    *,
    scan_id: str,
    user_id: str,
    status: str = "queued",
    cursor: int = 0,
    remaining: int = 4,
    reserve_expires_at: str | None = None,
    stock_ids: tuple[str, ...] | None = None,
    records: dict | None = None,
    budget_windows_opened: int = 0,
    trading_day: str = "2024-06-27",
):
    ids = stock_ids or tuple(f"TW:{2330 + i}" for i in range(max(remaining + cursor, 1)))
    snap = {
        "scanId": scan_id,
        "status": status,
        "cursor": cursor,
        "remainingCount": remaining,
        "universeSize": len(ids),
        "processedCount": cursor,
        "reserveExpiresAt": reserve_expires_at,
    }

    class _State:
        def __init__(self) -> None:
            self.scan_id = scan_id
            self.user_id = user_id
            self.status = status
            self.cursor = cursor
            self.reserve_expires_at = reserve_expires_at
            self.stock_ids = ids
            self.records = dict(records or {})
            self.budget_windows_opened = budget_windows_opened
            self.trading_day = trading_day

        def snapshot(self):
            return dict(snap)

    return _State()


def test_phase2_tick_runs_one_batch_when_approved(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="owner", remaining=3),
    )

    def fake_run_batch(scan_id, *, batch_size=10, timeout_seconds=120, **_kwargs):
        calls.append(scan_id)
        return _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="running",
            cursor=2,
            remaining=1,
            stock_ids=("TW:2330", "TW:2454", "TW:2303"),
            records={"TW:2330": _Rec(), "TW:2454": _Rec()},
        )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        fake_run_batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )

    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=False,
        max_batches=3,
        batch_size=2,
    )
    assert result.outcome == "phase2_batch_ran"
    assert calls == ["scan_owned"]
    assert result.batches_run == 1
    assert result.as_dict()["phase2AutoStart"] is False


def test_run_status_budget_exhausted_still_resumes(monkeypatch) -> None:
    """Reconciled run.status=budget_exhausted must not block Phase-2 resume."""
    _enable(monkeypatch)
    _approved_phase2_run()
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.status = "budget_exhausted"
        research_run_store._persist_locked(locked)

    resumes: list[str] = []
    batches: list[str] = []
    state = _fake_scan(
        scan_id="scan_owned",
        user_id="owner",
        status="budget_exhausted",
        cursor=1,
        remaining=2,
        stock_ids=("TW:2330", "TW:2454", "TW:2303"),
        records={
            "TW:2330": _Rec(),
            "TW:2454": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
        },
        budget_windows_opened=1,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: state,
    )

    def resume(scan_id, **_kwargs):
        resumes.append(scan_id)
        state.status = "running"
        state.budget_windows_opened = 2
        state.records["TW:2454"] = _Rec()
        return state

    def batch(scan_id, **_kwargs):
        batches.append(scan_id)
        state.cursor = 3
        state.status = "running"
        return state

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        resume,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )

    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert resumes == ["scan_owned"]
    assert batches == ["scan_owned"]
    assert result.outcome == "phase2_batch_ran"
    assert "budget_window_resumed" in result.notes


def test_budget_exhausted_resumes_once_then_batch(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    resumes: list[str] = []
    batches: list[str] = []

    state = _fake_scan(
        scan_id="scan_owned",
        user_id="owner",
        status="budget_exhausted",
        cursor=1,
        remaining=2,
        stock_ids=("TW:2330", "TW:2454", "TW:2303"),
        records={
            "TW:2330": _Rec(),
            "TW:2454": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
        },
        budget_windows_opened=1,
    )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: state,
    )

    def resume(scan_id, **_kwargs):
        resumes.append(scan_id)
        state.status = "running"
        state.budget_windows_opened = 2
        state.records["TW:2454"] = _Rec()
        return state

    def batch(scan_id, **_kwargs):
        batches.append(scan_id)
        state.cursor = 3
        state.status = "running"
        return state

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        resume,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )

    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert resumes == ["scan_owned"]
    assert batches == ["scan_owned"]
    assert result.outcome == "phase2_batch_ran"
    assert "budget_window_resumed" in result.notes


def test_budget_cap_reached_no_resume(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    day = "2024-06-27"
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain._phase2_now",
        lambda: datetime(2024, 6, 27, 12, 0, tzinfo=TAIPEI),
    )
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.dimensions["phase2Drain"] = {
            "enabled": True,
            "windowsByTradingDay": {day: PHASE2_MAX_BUDGET_WINDOWS_PER_DAY},
            "windowBaselineByDay": {day: 0},
            "windowsOpened": PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
        }
        research_run_store._persist_locked(locked)

    resumes: list[str] = []
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="budget_exhausted",
            remaining=2,
            budget_windows_opened=PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
            stock_ids=("TW:2330", "TW:2454"),
            records={
                "TW:2330": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        lambda scan_id, **_k: resumes.append(scan_id),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "phase2_budget_cap_reached"
    assert resumes == []


def test_budget_cap_resets_next_calendar_day(monkeypatch) -> None:
    """Cap on Asia/Taipei day D blocks resume; same scan on D+1 is allowed."""
    _enable(monkeypatch)
    _approved_phase2_run()
    clock = {"now": datetime(2024, 6, 27, 15, 0, tzinfo=TAIPEI)}
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain._phase2_now",
        lambda: clock["now"],
    )
    day_d = "2024-06-27"
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.dimensions["phase2Drain"] = {
            "enabled": True,
            "windowsByTradingDay": {day_d: PHASE2_MAX_BUDGET_WINDOWS_PER_DAY},
            "windowBaselineByDay": {day_d: 10},
            "windowsOpened": PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
        }
        research_run_store._persist_locked(locked)

    state = _fake_scan(
        scan_id="scan_owned",
        user_id="owner",
        status="budget_exhausted",
        remaining=2,
        # lifetime opened; today = opened - baseline(10) = 24 on day D
        budget_windows_opened=10 + PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
        stock_ids=("TW:2330", "TW:2454"),
        records={
            "TW:2330": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
        },
        trading_day="2024-06-20",  # scan trading day must not drive the cap key
    )
    resumes: list[str] = []

    def resume(scan_id, **_kwargs):
        resumes.append(scan_id)
        state.status = "running"
        state.budget_windows_opened = int(state.budget_windows_opened) + 1
        state.records["TW:2330"] = _Rec()
        return state

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: state,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        resume,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lambda scan_id, **_k: state,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )

    blocked = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert blocked.outcome == "phase2_budget_cap_reached"
    assert resumes == []

    clock["now"] = datetime(2024, 6, 28, 9, 0, tzinfo=TAIPEI)
    allowed = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert resumes == ["scan_owned"]
    assert allowed.outcome in {"phase2_batch_ran", "phase2_no_progress", "phase2_lease_held"}
    run = research_run_store.get("owner")
    assert run is not None
    meta = (run.dimensions or {}).get("phase2Drain") or {}
    assert meta.get("windowBaselineByDay", {}).get("2024-06-28") == (
        10 + PHASE2_MAX_BUDGET_WINDOWS_PER_DAY
    )


def test_windows_today_prefers_scan_over_failed_telemetry(monkeypatch) -> None:
    """Telemetry undercount must not drop below scan-derived windows_today."""
    _enable(monkeypatch)
    _approved_phase2_run()
    day = "2024-06-27"
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain._phase2_now",
        lambda: datetime(2024, 6, 27, 12, 0, tzinfo=TAIPEI),
    )
    baseline = 5
    opened = baseline + PHASE2_MAX_BUDGET_WINDOWS_PER_DAY
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.dimensions["phase2Drain"] = {
            "enabled": True,
            # Telemetry lost / undercounted vs scan.
            "windowsByTradingDay": {day: 0},
            "windowBaselineByDay": {day: baseline},
            "windowsOpened": 0,
        }
        research_run_store._persist_locked(locked)

    resumes: list[str] = []
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="budget_exhausted",
            remaining=2,
            budget_windows_opened=opened,
            stock_ids=("TW:2330", "TW:2454"),
            records={
                "TW:2330": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        lambda scan_id, **_k: resumes.append(scan_id),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "phase2_budget_cap_reached"
    assert resumes == []


def test_verified_ready_with_history_exhausted_resumes(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    resumes: list[str] = []
    batches: list[str] = []
    state = _fake_scan(
        scan_id="scan_owned",
        user_id="owner",
        status="verified_ready",
        cursor=2,
        remaining=0,
        stock_ids=("TW:2330", "TW:2454"),
        records={
            "TW:2330": _Rec(),
            "TW:2454": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
        },
        budget_windows_opened=0,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: state,
    )

    def resume(scan_id, **_kwargs):
        resumes.append(scan_id)
        state.status = "running"
        state.records["TW:2454"] = _Rec()
        state.cursor = 1
        return state

    def batch(scan_id, **_kwargs):
        batches.append(scan_id)
        state.cursor = 2
        state.status = "completed_processing"
        state.records = {"TW:2330": _Rec(), "TW:2454": _Rec()}
        return state

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        resume,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert resumes == ["scan_owned"]
    assert batches == ["scan_owned"]
    assert result.outcome in {"phase2_batch_ran", "phase2_already_complete"}


def test_paused_phase2_no_work(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run(enabled=False)
    called = {"n": 0}
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="owner"),
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "skipped_phase2_paused"
    assert called["n"] == 0


def test_no_progress_without_lease(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="running",
            cursor=1,
            remaining=2,
            reserve_expires_at=None,
        ),
    )

    def no_progress(scan_id, **_kwargs):
        return _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="running",
            cursor=1,
            remaining=2,
            reserve_expires_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            stock_ids=("TW:2330", "TW:2454", "TW:2303"),
            records={"TW:2330": _Rec(readiness="failed", rejection_reason="upstream")},
        )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        no_progress,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "phase2_no_progress"
    assert result.lease_held is False


def test_dry_run_reports_resume_budget_and_run(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="budget_exhausted",
            remaining=2,
            stock_ids=("TW:2330", "TW:2454"),
            records={
                "TW:2330": _Rec(rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON),
            },
        ),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=True)
    assert result.dry_run is True
    assert result.preflight is not None
    assert result.preflight["phase2Step"]["action"] == "resume_budget_and_run"


def test_phase2_foreign_scan_untouched(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run(scan_id="scan_foreign")
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="other-user"),
    )
    called = {"n": 0}
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "skipped_phase2_foreign_scan"
    assert called["n"] == 0


def test_phase2_cancelled_run_no_work(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.status = "cancelled"
        research_run_store._persist_locked(locked)
    called = {"n": 0}
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="owner"),
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "skipped_run_status"
    assert called["n"] == 0


def test_phase2_lease_held_idempotent(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(
            scan_id=scan_id, user_id="owner", status="running", cursor=1, remaining=3
        ),
    )

    def lease_held_batch(scan_id, **_kwargs):
        # Cursor unchanged + remaining work + active lease → lease_held.
        return _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="running",
            cursor=1,
            remaining=3,
            reserve_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lease_held_batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )
    first = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    second = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert first.outcome == "phase2_lease_held"
    assert second.outcome == "phase2_lease_held"
    assert first.lease_held is True
    assert second.lease_held is True
    assert first.symbols_processed == 0
    assert second.symbols_processed == 0


def test_phase1_preferred_over_phase2_same_tick(monkeypatch) -> None:
    _enable(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_live")
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = "scan_owned"
        research_run_store._persist_locked(locked)

    fp_calls: list[str] = []
    tech_calls: list[str] = []

    def fp_batch(job_id, *, user_id, batch_size=10, **_kwargs):
        fp_calls.append(job_id)
        return {
            "job": {"status": "running", "job_id": job_id},
            "batchIds": ["TW:2330"],
            "processed": 1,
        }

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.first_pass_job_service.run_batch",
        fp_batch,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lambda scan_id, **_k: tech_calls.append(scan_id),
    )
    monkeypatch.setattr(
        research_run_store,
        "_load_first_pass_job",
        lambda _user, _job: {"status": "running"},
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert fp_calls == ["fpj_live"]
    assert tech_calls == []
    assert result.job_id == "fpj_live"
    assert result.technical_scan_id is None
    assert "phase2" not in str(result.outcome or "")


def test_run_batch_budget_resume_required_resumes_once(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    resumes: list[str] = []
    batch_calls = {"n": 0}
    state = _fake_scan(
        scan_id="scan_owned",
        user_id="owner",
        status="running",
        cursor=0,
        remaining=2,
        stock_ids=("TW:2330", "TW:2454"),
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: state,
    )

    def resume(scan_id, **_kwargs):
        resumes.append(scan_id)
        state.status = "running"
        state.budget_windows_opened = 1
        return state

    def batch(scan_id, **_kwargs):
        batch_calls["n"] += 1
        if batch_calls["n"] == 1:
            raise TwResearchConflictError(
                "need resume",
                scan_id=scan_id,
                operation="run_batch",
                code="budget_resume_required",
            )
        state.cursor = 2
        return state

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.resume_budget_window",
        resume,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "reconcile",
        lambda user_id: research_run_store.get(user_id),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert resumes == ["scan_owned"]
    assert batch_calls["n"] == 2
    assert result.outcome == "phase2_batch_ran"
