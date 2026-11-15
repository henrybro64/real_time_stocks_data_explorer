"""Explicit standalone first-pass job adoption (#359)."""

from __future__ import annotations

import pytest

from app.services.tw_research.research_run import (
    ResearchRunConflict,
    research_run_store,
)


def test_adopt_attaches_standalone_job_and_preserves_id(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    monkeypatch.setattr(
        "app.services.tw_research.research_run.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del user_id
        return {
            "job_id": job_id,
            "status": "running",
            "universe_digest": "digest-abc",
            "reserve_expires_at": None,
            "deletion_state": None,
        }

    adopted = research_run_store.adopt_standalone_first_pass_job(
        "u_adopt",
        job_id="fpj_8a41bc24cac84d9bb1fff806",
        confirm_universe_digest="digest-abc",
        job_loader=_loader,
    )
    assert adopted.first_pass_job_id == "fpj_8a41bc24cac84d9bb1fff806"
    assert adopted.phase == "phase1_first_pass"
    assert adopted.schedule_enabled is False
    research_run_store.disable_test_memory()


def test_adopt_rejects_active_lease(monkeypatch) -> None:
    research_run_store.clear_for_tests()

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del job_id, user_id
        return {
            "job_id": "fpj_busy",
            "status": "running",
            "reserve_expires_at": "2099-01-01T00:00:00+00:00",
            "deletion_state": None,
        }

    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            "u_busy",
            job_id="fpj_busy",
            job_loader=_loader,
        )
    assert exc.value.reason == "first_pass_job_lease_active"
    research_run_store.disable_test_memory()


def test_adopt_rejects_digest_mismatch(monkeypatch) -> None:
    research_run_store.clear_for_tests()

    def _loader(job_id, *, user_id=None):  # noqa: ANN001
        del job_id, user_id
        return {
            "job_id": "fpj_x",
            "status": "pending",
            "universe_digest": "digest-a",
            "reserve_expires_at": None,
            "deletion_state": None,
        }

    with pytest.raises(ResearchRunConflict) as exc:
        research_run_store.adopt_standalone_first_pass_job(
            "u_digest",
            job_id="fpj_x",
            confirm_universe_digest="digest-b",
            job_loader=_loader,
        )
    assert exc.value.reason == "first_pass_job_universe_digest_mismatch"
    research_run_store.disable_test_memory()
