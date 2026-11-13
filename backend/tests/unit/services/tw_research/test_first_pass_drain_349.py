"""Durable first-pass drain for managed research runs (#349)."""

from __future__ import annotations

import pytest

from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.research_run import research_run_store


def setup_function() -> None:
    research_run_store.clear_for_tests()


def teardown_function() -> None:
    research_run_store.disable_test_memory()


def _enable_drain_for_owner(monkeypatch: pytest.MonkeyPatch, user_id: str = "owner") -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )


def test_drain_skips_when_globally_disabled(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_1")
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=False,
        dry_run=True,
    )
    assert result.outcome == "skipped_disabled"


def test_drain_skips_when_schedule_not_authorized(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    run = research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_1")
    # Pause after attach (attach turns schedule_enabled on when available).
    research_run_store.set_first_pass_drain_enabled("owner", enabled=False)
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=True,
        job_id=run.first_pass_job_id,
    )
    assert result.outcome == "skipped_not_authorized"
    assert result.schedule_enabled is False


def test_attach_authorizes_drain_and_dry_run_ready(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_auth")
    assert run.schedule_enabled is True
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=True,
    )
    assert result.outcome == "dry_run_ready"
    assert result.job_id == "fpj_auth"
    assert result.schedule_enabled is True


def test_attach_does_not_arm_for_non_owner(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "drain-owner",
    )
    research_run_store.begin_phase1("other", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("other", first_pass_job_id="fpj_x")
    assert run.schedule_enabled is False
    assert "first_pass_drain_unavailable_for_user" in run.notes
    with pytest.raises(Exception) as excinfo:
        research_run_store.set_first_pass_drain_enabled("other", enabled=True)
    assert "first_pass_drain_unavailable_for_user" in str(excinfo.value)


def test_attach_does_not_arm_when_globally_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "owner",
    )
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_x")
    assert run.schedule_enabled is False
    assert "first_pass_drain_globally_disabled" in run.notes


def test_drain_refuses_non_authoritative_historical_job(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_owned")
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=True,
        job_id="fpj_orphan_pending",
    )
    assert result.outcome == "skipped_non_authoritative_job"


def test_drain_refuses_job_id_when_run_has_no_attached_job(monkeypatch) -> None:
    """Resume-without-attach must not let caller-supplied orphan ids drain (#349 P1)."""
    from app.services.tw_research.research_run import ResearchRunConflict

    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    with pytest.raises(ResearchRunConflict):
        research_run_store.set_first_pass_drain_enabled("owner", enabled=True)

    # Even if schedule_enabled is force-armed without a job, refuse caller jobId.
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.schedule_enabled = True
        research_run_store._persist_locked(locked)

    called: list[str] = []

    def fake_run_batch(job_id, *, user_id, batch_size=10, **_kwargs):
        called.append(job_id)
        return {"processed": 0, "batchIds": [], "job": {"status": "running"}}

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.first_pass_job_service.run_batch",
        fake_run_batch,
    )
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=False,
        job_id="fpj_orphan_historical",
        max_batches=1,
    )
    assert result.outcome == "skipped_non_authoritative_job"
    assert called == []


def test_resume_requires_phase1_attached_job(monkeypatch) -> None:
    from app.services.tw_research.research_run import ResearchRunConflict

    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    with pytest.raises(ResearchRunConflict) as missing_job:
        research_run_store.set_first_pass_drain_enabled("owner", enabled=True)
    assert "requires_attached_job" in missing_job.value.reason

    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_1")
    research_run_store.set_first_pass_drain_enabled("owner", enabled=False)
    # Pause always allowed; resume after pause OK in phase1 with job.
    research_run_store.set_first_pass_drain_enabled("owner", enabled=True)

    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked("owner")
        locked.phase = "awaiting_analyst_candidate_selection"
        locked.schedule_enabled = False
        research_run_store._persist_locked(locked)
    with pytest.raises(ResearchRunConflict) as past_gate:
        research_run_store.set_first_pass_drain_enabled("owner", enabled=True)
    assert "requires_phase1" in past_gate.value.reason


def test_drain_stops_past_analyst_gate(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_1")
    # Force gated phase without going through job finalize.
    with research_run_store._lock:  # noqa: SLF001 — test-only phase fixture
        locked = research_run_store._require_locked("owner")
        locked.phase = "awaiting_analyst_candidate_selection"
        locked.status = "gated"
        locked.schedule_enabled = True
        research_run_store._persist_locked(locked)
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=False,
        job_id=run.first_pass_job_id,
    )
    assert result.outcome == "skipped_past_phase1_gate"


def test_drain_runs_bounded_batches_and_reconciles(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_drain")

    calls = {"n": 0}

    def fake_run_batch(job_id, *, user_id, batch_size=10, **_kwargs):
        calls["n"] += 1
        assert job_id == "fpj_drain"
        assert user_id == "owner"
        if calls["n"] < 2:
            return {
                "job": {"status": "running", "job_id": job_id},
                "batchIds": ["TW:2330"],
                "processed": 1,
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": False,
            }
        return {
            "job": {
                "status": "completed",
                "job_id": job_id,
                "active_snapshot_id": "fps_1",
            },
            "batchIds": ["TW:2317"],
            "processed": 1,
            "leaseHeld": False,
            "alreadyComplete": False,
            "waitingForRetry": False,
        }

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.first_pass_job_service.run_batch",
        fake_run_batch,
    )

    # Reconcile needs job loader — stub completed snapshot gate.
    monkeypatch.setattr(
        research_run_store,
        "_load_first_pass_job",
        lambda _user, _job: {
            "status": "completed",
            "active_snapshot_id": "fps_1",
        },
    )

    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=False,
        max_batches=5,
        batch_size=10,
    )
    assert calls["n"] == 2
    assert result.batches_run == 2
    assert result.symbols_processed == 2
    assert result.outcome == "job_completed"
    assert result.phase == "awaiting_analyst_candidate_selection"
    assert result.schedule_enabled is False
    payload = result.as_dict()
    assert payload["notionWrites"] is False
    assert payload["phase2AutoStart"] is False
    assert payload["stopsAtAnalystGate"] is True


def test_drain_stops_on_lease_held(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_lease")

    def fake_run_batch(job_id, *, user_id, batch_size=10, **_kwargs):
        return {
            "job": {"status": "running", "job_id": job_id},
            "batchIds": [],
            "processed": 0,
            "leaseHeld": True,
            "alreadyComplete": False,
            "waitingForRetry": False,
        }

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.first_pass_job_service.run_batch",
        fake_run_batch,
    )
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        max_batches=3,
        batch_size=10,
    )
    assert result.outcome == "lease_held"
    assert result.lease_held is True
    assert result.batches_run == 0


def test_drain_stops_mid_tick_when_paused(monkeypatch) -> None:
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_pause")
    calls = {"n": 0}

    def fake_run_batch(job_id, *, user_id, batch_size=10, **_kwargs):
        calls["n"] += 1
        # Pause after the first successful batch.
        research_run_store.set_first_pass_drain_enabled("owner", enabled=False)
        return {
            "job": {"status": "running", "job_id": job_id},
            "batchIds": ["TW:2330"],
            "processed": 1,
            "leaseHeld": False,
            "alreadyComplete": False,
            "waitingForRetry": False,
        }

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.first_pass_job_service.run_batch",
        fake_run_batch,
    )
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        max_batches=5,
        batch_size=10,
    )
    assert calls["n"] == 1
    assert result.batches_run == 1
    assert result.outcome == "paused"


def test_deferred_batch_exposes_progress_and_tick_outcome(monkeypatch) -> None:
    """Operator-visible firstPassProgress + lastOutcome after a deferred tick (#349)."""
    _enable_drain_for_owner(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_deferred")

    deferred_job = {
        "status": "deferred",
        "job_id": "fpj_deferred",
        "processed_count": 2,
        "universe_size": 10,
        "ok_count": 1,
        "partial_count": 1,
        "failed_count": 0,
        "next_retry_at": "2024-06-27T12:00:00+00:00",
        "last_error": "rate_limited:provider",
        "last_error_class": "transient",
        "updated_at": "2024-06-27T11:00:00+00:00",
    }

    def fake_run_batch(job_id, *, user_id, batch_size=10, **_kwargs):
        return {
            "job": deferred_job,
            "batchIds": ["TW:2330"],
            "processed": 1,
            "leaseHeld": False,
            "alreadyComplete": False,
            "waitingForRetry": True,
        }

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.first_pass_job_service.run_batch",
        fake_run_batch,
    )
    monkeypatch.setattr(
        research_run_store,
        "_load_first_pass_job",
        lambda _user, _job: deferred_job,
    )

    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        max_batches=3,
        batch_size=5,
    )
    assert result.outcome == "waiting_for_retry"

    run = research_run_store.get("owner")
    assert run is not None
    progress = (run.dimensions or {}).get("firstPassProgress") or {}
    assert progress.get("nextRetryAt") == "2024-06-27T12:00:00+00:00"
    assert progress.get("lastErrorClass") == "transient"
    drain_meta = (run.dimensions or {}).get("firstPassDrain") or {}
    assert drain_meta.get("lastOutcome") == result.outcome
    payload = run.as_dict()
    assert payload["firstPassDrain"]["lastOutcome"] == result.outcome
    assert payload["firstPassProgress"]["nextRetryAt"] == progress["nextRetryAt"]
