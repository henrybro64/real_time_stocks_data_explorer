"""Phase-2 run-owned technical drain on the First-Pass CronJob path (#361)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.research_run import research_run_store


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


def _approved_phase2_run(*, scan_id: str = "scan_owned") -> None:
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
        research_run_store._persist_locked(locked)


def _fake_scan(
    *,
    scan_id: str,
    user_id: str,
    status: str = "queued",
    cursor: int = 0,
    remaining: int = 4,
    reserve_expires_at: str | None = None,
):
    snap = {
        "scanId": scan_id,
        "status": status,
        "cursor": cursor,
        "remainingCount": remaining,
        "universeSize": cursor + remaining,
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
        state = _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="running",
            cursor=2,
            remaining=1,
        )
        return state

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
    assert result.symbols_processed == 2
    assert result.as_dict()["phase2AutoStart"] is False
    run = research_run_store.get("owner")
    assert run is not None
    meta = (run.dimensions or {}).get("firstPassDrain") or {}
    assert isinstance(meta.get("lastTickAt"), str)


def test_phase2_unapproved_does_no_work(monkeypatch) -> None:
    _enable(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.phase = "awaiting_analyst_candidate_selection"
        locked.status = "gated"
        locked.schedule_enabled = False
        locked.gates["candidateSelection"] = "awaiting_analyst"
        locked.technical_scan_id = "scan_should_not_run"
        research_run_store._persist_locked(locked)

    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("run_batch must not be called")

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="owner"),
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        boom,
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=False)
    assert result.outcome == "skipped_phase2_not_approved"
    assert called["n"] == 0


def test_phase2_foreign_scan_untouched(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run(scan_id="scan_foreign")
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="other-user"),
    )
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("foreign scan must not run")

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        boom,
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
        # Cursor unchanged + remaining work → lease_held.
        return _fake_scan(
            scan_id=scan_id,
            user_id="owner",
            status="running",
            cursor=1,
            remaining=3,
            reserve_expires_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
    assert second.symbols_processed == 0


def test_phase2_dry_run_reports_step(monkeypatch) -> None:
    _enable(monkeypatch)
    _approved_phase2_run()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.get_scan",
        lambda scan_id: _fake_scan(scan_id=scan_id, user_id="owner", remaining=5),
    )
    called = {"n": 0}
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service.run_batch",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1),
    )
    result = drain_authorized_first_pass(user_id="owner", enabled=True, dry_run=True)
    assert result.dry_run is True
    assert result.outcome == "dry_run_phase2_ready"
    assert result.batches_run == 0
    assert called["n"] == 0
    assert result.preflight is not None
    step = result.preflight["phase2Step"]
    assert step["action"] == "run_technical_batch"
    assert step["technicalScanId"] == "scan_owned"
    assert step["remainingCount"] == 5


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
