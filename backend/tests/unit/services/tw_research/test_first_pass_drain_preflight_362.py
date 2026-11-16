"""Read-only First-Pass drain dry-run / preflight (#362)."""

from __future__ import annotations

from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.research_run import research_run_store


def setup_function() -> None:
    research_run_store.clear_for_tests()
    research_run_store.set_first_pass_job_lookup_for_tests(
        lambda job_id, user_id="owner": {
            "job_id": job_id,
            "user_id": user_id,
            "status": "pending",
            "deletion_state": None,
            "archived_at": None,
            "universe_size": 73,
        }
    )


def teardown_function() -> None:
    research_run_store.disable_test_memory()


def _enable(monkeypatch, user_id: str = "owner") -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "unit-test-drain-token",
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_token",
        "unit-test-drain-token",
    )


def test_dry_run_preflight_does_not_write(monkeypatch) -> None:
    _enable(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_pf")
    rev_before = int(run.revision)
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=True,
        batch_size=5,
    )
    assert result.dry_run is True
    assert result.outcome == "dry_run_ready"
    assert result.batches_run == 0
    assert result.lease_held is False
    assert result.preflight is not None
    avail = result.preflight["availability"]
    assert avail["available"] is True
    assert avail["drainEnabledConfigured"] is True
    assert avail["drainOwnerConfigured"] is True
    assert avail["drainTokenConfigured"] is True
    # Never leak token/owner values.
    blob = str(result.as_dict())
    assert "unit-test-drain-token" not in blob
    after = research_run_store.get("owner")
    assert after is not None
    assert int(after.revision) == rev_before
    assert bool(after.schedule_enabled) is True
