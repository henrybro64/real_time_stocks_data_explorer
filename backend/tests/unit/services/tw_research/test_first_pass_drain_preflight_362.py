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


def test_dry_run_disabled_returns_availability_flags_no_writes(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_user_id",
        "owner",
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "owner",
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_token",
        "unit-test-drain-token",
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "unit-test-drain-token",
    )
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_dis")
    rev_before = int(run.revision)
    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=False,
        dry_run=True,
    )
    assert result.outcome == "skipped_disabled"
    assert result.dry_run is True
    assert result.preflight is not None
    avail = result.preflight["availability"]
    assert avail["drainEnabledConfigured"] is False
    assert avail["available"] is False
    assert avail["reason"] == "first_pass_drain_globally_disabled"
    assert "unit-test-drain-token" not in str(result.as_dict())
    after = research_run_store.get("owner")
    assert after is not None
    assert int(after.revision) == rev_before


def test_dry_run_counts_failure_yields_preflight_errors(monkeypatch) -> None:
    _enable(monkeypatch)
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("owner", first_pass_job_id="fpj_err")

    class _Repo:
        def is_available(self) -> bool:
            return True

        def count_first_pass_symbols(self, job_id: str):
            raise RuntimeError(f"counts boom for {job_id}")

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.first_pass_job_service.get_job",
        lambda job_id, user_id=None: {
            "job_id": job_id,
            "user_id": user_id or "owner",
            "status": "pending",
            "universe_size": 73,
        },
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )
    # Import path used inside _build_dry_run_preflight
    import app.repositories.tw_research_repository as repo_mod

    monkeypatch.setattr(repo_mod, "tw_research_repository", _Repo())

    result = drain_authorized_first_pass(
        user_id="owner",
        enabled=True,
        dry_run=True,
    )
    assert result.outcome == "dry_run_ready"
    assert result.preflight is not None
    assert "counts_unavailable" in (result.preflight.get("preflightErrors") or [])
    assert result.batches_run == 0
    assert result.lease_held is False
