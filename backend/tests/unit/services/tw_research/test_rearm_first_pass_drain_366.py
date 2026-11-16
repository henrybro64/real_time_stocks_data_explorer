"""Re-arm managed First-Pass drain on Start after executor becomes available (#366)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from app.services.tw_research.research_run import (
    ResearchRunStore,
    first_pass_drain_availability,
)


@pytest.fixture()
def store() -> ResearchRunStore:
    research_run_store = ResearchRunStore()
    research_run_store.clear_for_tests()
    yield research_run_store
    research_run_store.disable_test_memory()


def _enable_executor(monkeypatch, *, user_id: str = "owner-1") -> None:
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
        "drain-token-test-not-a-secret-value",
    )


def _disable_executor(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "",
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "",
    )


def test_availability_requires_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "owner-1",
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "",
    )
    ok, reason = first_pass_drain_availability("owner-1")
    assert ok is False
    assert reason == "first_pass_drain_token_unconfigured"


def test_start_unavailable_then_rearm_same_run_job(store: ResearchRunStore, monkeypatch) -> None:
    _disable_executor(monkeypatch)
    run = store.begin_phase1("owner-1", trading_day="2024-06-27")
    attached = store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_same")
    assert attached.first_pass_job_id == "fpj_same"
    assert attached.schedule_enabled is False
    run_id = attached.research_run_id

    # Idempotent Start while still unavailable — no arm.
    again = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert again.research_run_id == run_id
    assert again.first_pass_job_id == "fpj_same"
    assert again.schedule_enabled is False

    _enable_executor(monkeypatch, user_id="owner-1")
    rearmed = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert rearmed.research_run_id == run_id
    assert rearmed.first_pass_job_id == "fpj_same"
    assert rearmed.schedule_enabled is True

    # Repeated re-arm is idempotent — same run/job, still armed.
    second = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert second.research_run_id == run_id
    assert second.first_pass_job_id == "fpj_same"
    assert second.schedule_enabled is True
    del run


def test_rearm_blocked_for_cancelled_run(store: ResearchRunStore, monkeypatch) -> None:
    _enable_executor(monkeypatch, user_id="owner-1")
    store.begin_phase1("owner-1")
    store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_c")
    with store._lock:
        run = store._require_locked("owner-1")
        run.status = "cancelled"
        run.schedule_enabled = False
        store._persist_locked(run)
    out = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert out.schedule_enabled is False


def test_concurrent_rearm_no_duplicate_jobs(store: ResearchRunStore, monkeypatch) -> None:
    _disable_executor(monkeypatch)
    store.begin_phase1("owner-1")
    store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_conc")
    _enable_executor(monkeypatch, user_id="owner-1")

    def _rearm() -> str:
        run = store.maybe_rearm_first_pass_drain_on_start("owner-1")
        return f"{run.research_run_id}:{run.first_pass_job_id}:{run.schedule_enabled}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [fut.result() for fut in as_completed([pool.submit(_rearm) for _ in range(8)])]
    assert len(set(results)) == 1
    assert results[0].endswith(":fpj_conc:True")


def test_begin_phase1_returns_same_active_run(store: ResearchRunStore, monkeypatch) -> None:
    _disable_executor(monkeypatch)
    first = store.begin_phase1("owner-1")
    store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_keep")
    second = store.begin_phase1("owner-1")
    assert second.research_run_id == first.research_run_id
    assert second.first_pass_job_id == "fpj_keep"
