"""Re-arm managed First-Pass drain on Start after executor becomes available (#366)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pytest

from app.services.tw_research.execution_status import classify_first_pass_execution
from app.services.tw_research.first_pass_job import FirstPassJobError
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


def _fake_job(
    *,
    job_id: str = "fpj_1",
    user_id: str = "owner-1",
    status: str = "pending",
    deletion_state: str | None = None,
    archived_at: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "user_id": user_id,
        "status": status,
        "deletion_state": deletion_state,
        "archived_at": archived_at,
        "universe_size": 73,
    }


def _install_lookup(store: ResearchRunStore, job: dict[str, Any] | Exception | None):
    def _lookup(job_id: str, *, user_id: str):
        del job_id, user_id
        if isinstance(job, Exception):
            raise job
        if job is None:
            raise KeyError("first_pass_job_not_found")
        return dict(job)

    store.set_first_pass_job_lookup_for_tests(_lookup)


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


@pytest.mark.parametrize(
    ("job", "expect_armed"),
    [
        (_fake_job(deletion_state="requested"), False),
        (_fake_job(deletion_state="failed"), False),
        (_fake_job(archived_at="2024-01-01T00:00:00+00:00"), False),
        (KeyError("first_pass_job_not_found"), False),
        (FirstPassJobError("first_pass_job_store_unavailable"), False),
        (RuntimeError("boom"), False),
        (_fake_job(user_id="other-user"), False),
        (_fake_job(status="cancelled"), False),
        (_fake_job(status="failed"), False),
        (_fake_job(status="completed"), False),
        (_fake_job(status="pending"), True),
        (_fake_job(status="running"), True),
        (_fake_job(status="deferred"), True),
        (_fake_job(status="partial"), True),
    ],
)
def test_rearm_job_eligibility_matrix(
    store: ResearchRunStore, monkeypatch, job, expect_armed: bool
) -> None:
    _disable_executor(monkeypatch)
    store.begin_phase1("owner-1")
    store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_1")
    _install_lookup(store, job)
    _enable_executor(monkeypatch, user_id="owner-1")
    before = store.get("owner-1")
    assert before is not None
    rev_before = int(before.revision)
    out = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert out.first_pass_job_id == "fpj_1"
    assert out.schedule_enabled is expect_armed
    if expect_armed:
        assert int(out.revision) == rev_before + 1
    else:
        assert int(out.revision) == rev_before


def test_start_unavailable_then_rearm_same_run_job(
    store: ResearchRunStore, monkeypatch
) -> None:
    _disable_executor(monkeypatch)
    store.begin_phase1("owner-1", trading_day="2024-06-27")
    attached = store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_same")
    _install_lookup(store, _fake_job(job_id="fpj_same"))
    assert attached.schedule_enabled is False
    run_id = attached.research_run_id

    again = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert again.research_run_id == run_id
    assert again.schedule_enabled is False
    exec_state = classify_first_pass_execution(
        again, job={"status": "pending", "universe_size": 73}
    )
    assert exec_state["state"] == "executor_unavailable"

    _enable_executor(monkeypatch, user_id="owner-1")
    rearmed = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert rearmed.research_run_id == run_id
    assert rearmed.first_pass_job_id == "fpj_same"
    assert rearmed.schedule_enabled is True

    second = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert second.research_run_id == run_id
    assert second.schedule_enabled is True
    assert int(second.revision) == int(rearmed.revision)


def test_rearm_blocked_for_cancelled_run(store: ResearchRunStore, monkeypatch) -> None:
    _enable_executor(monkeypatch, user_id="owner-1")
    store.begin_phase1("owner-1")
    store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_c")
    _install_lookup(store, _fake_job(job_id="fpj_c"))
    with store._lock:
        run = store._require_locked("owner-1")
        run.status = "cancelled"
        run.schedule_enabled = False
        store._persist_locked(run)
    out = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert out.schedule_enabled is False


@pytest.mark.parametrize(
    ("patch_kwargs", "blocker"),
    [
        ({"enabled": False, "user_id": "owner-1", "token": "t"}, "first_pass_drain_globally_disabled"),
        ({"enabled": True, "user_id": "other", "token": "t"}, "first_pass_drain_unavailable_for_user"),
        ({"enabled": True, "user_id": "owner-1", "token": ""}, "first_pass_drain_token_unconfigured"),
    ],
)
def test_unavailable_leaves_run_unchanged_with_blocker(
    store: ResearchRunStore, monkeypatch, patch_kwargs: dict[str, Any], blocker: str
) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        patch_kwargs["enabled"],
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        patch_kwargs["user_id"],
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        patch_kwargs["token"],
    )
    store.begin_phase1("owner-1")
    attached = store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_ua")
    _install_lookup(store, _fake_job(job_id="fpj_ua"))
    rev = int(attached.revision)
    out = store.maybe_rearm_first_pass_drain_on_start("owner-1")
    assert out.schedule_enabled is False
    assert int(out.revision) == rev
    exec_state = classify_first_pass_execution(
        out, job={"status": "pending", "universe_size": 73}
    )
    assert exec_state["state"] == "executor_unavailable"
    assert exec_state.get("blocker") == blocker


def test_concurrent_rearm_no_duplicate_jobs(store: ResearchRunStore, monkeypatch) -> None:
    _disable_executor(monkeypatch)
    store.begin_phase1("owner-1")
    store.attach_first_pass_job("owner-1", first_pass_job_id="fpj_conc")
    _install_lookup(store, _fake_job(job_id="fpj_conc"))
    _enable_executor(monkeypatch, user_id="owner-1")

    def _rearm() -> str:
        run = store.maybe_rearm_first_pass_drain_on_start("owner-1")
        return f"{run.research_run_id}:{run.first_pass_job_id}:{run.schedule_enabled}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [
            fut.result() for fut in as_completed([pool.submit(_rearm) for _ in range(8)])
        ]
    assert len(set(results)) == 1
    assert results[0].endswith(":fpj_conc:True")
