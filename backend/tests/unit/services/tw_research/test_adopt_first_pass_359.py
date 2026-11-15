"""Explicit standalone first-pass job adoption (#359)."""

from __future__ import annotations

import pytest

from app.services.tw_research.research_run import (
    ResearchRunConflict,
    research_run_store,
)


def _base_job(job_id: str = "fpj_8a41bc24cac84d9bb1fff806", **overrides):
    payload = {
        "job_id": job_id,
        "status": "running",
        "universe_digest": "digest-abc",
        "reserve_expires_at": None,
        "deletion_state": None,
        "archived_at": None,
        "stock_ids": ["TW:8046"] + [f"TW:{8000 + i}" for i in range(72)],
        "cursor": 1,
        "done_count": 1,
    }
    payload.update(overrides)
    return payload


def test_adopt_attaches_standalone_job_and_preserves_id(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    monkeypatch.setattr(
        "app.services.tw_research.research_run.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del user_id
        return _base_job(job_id)

    adopted = research_run_store.adopt_standalone_first_pass_job(
        "u_adopt",
        job_id="fpj_8a41bc24cac84d9bb1fff806",
        confirm_universe_digest="digest-abc",
        job_loader=_loader,
    )
    assert adopted.first_pass_job_id == "fpj_8a41bc24cac84d9bb1fff806"
    assert adopted.phase == "phase1_first_pass"
    assert adopted.schedule_enabled is False

    # Idempotent re-adopt.
    again = research_run_store.adopt_standalone_first_pass_job(
        "u_adopt",
        job_id="fpj_8a41bc24cac84d9bb1fff806",
        confirm_universe_digest="digest-abc",
        job_loader=_loader,
    )
    assert again.research_run_id == adopted.research_run_id
    research_run_store.disable_test_memory()


def test_adopt_rejects_active_lease(monkeypatch) -> None:
    research_run_store.clear_for_tests()

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del job_id, user_id
        return _base_job(reserve_expires_at="2099-01-01T00:00:00+00:00")

    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            "u_busy",
            job_id="fpj_busy",
            confirm_universe_digest="digest-abc",
            job_loader=_loader,
        )
    assert exc.value.reason == "first_pass_job_lease_active"
    research_run_store.disable_test_memory()


def test_adopt_rejects_digest_mismatch(monkeypatch) -> None:
    research_run_store.clear_for_tests()

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del job_id, user_id
        return _base_job(universe_digest="digest-a")

    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            "u_digest",
            job_id="fpj_x",
            confirm_universe_digest="digest-b",
            job_loader=_loader,
        )
    assert exc.value.reason == "first_pass_job_universe_digest_mismatch"
    research_run_store.disable_test_memory()


def test_adopt_retains_gated_run(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    monkeypatch.setattr(
        "app.services.tw_research.research_run.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )
    run = research_run_store.begin_phase1("u_gated")
    with research_run_store._lock:  # noqa: SLF001
        run.phase = "awaiting_analyst_candidate_selection"
        run.status = "gated"
        run.first_pass_job_id = "fpj_other"
        research_run_store._persist_locked(run)  # noqa: SLF001

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del user_id
        return _base_job(job_id)

    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            "u_gated",
            job_id="fpj_new",
            confirm_universe_digest="digest-abc",
            job_loader=_loader,
        )
    assert exc.value.reason == "research_run_retained"
    current = research_run_store.get("u_gated")
    assert current is not None
    assert current.first_pass_job_id == "fpj_other"
    research_run_store.disable_test_memory()


def test_adopt_rejects_nothing_pending(monkeypatch) -> None:
    research_run_store.clear_for_tests()

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del job_id, user_id
        return _base_job(done_count=73, cursor=73, status="partial")

    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            "u_done",
            job_id="fpj_done",
            confirm_universe_digest="digest-abc",
            job_loader=_loader,
        )
    assert exc.value.reason == "first_pass_job_nothing_pending"
    research_run_store.disable_test_memory()
