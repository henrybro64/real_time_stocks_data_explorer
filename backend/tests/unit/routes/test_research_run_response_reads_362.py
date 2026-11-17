"""Isolate First-Pass status reads in _research_run_response (#361/#362 P1)."""

from __future__ import annotations

from app.api.routes import tw_research as routes
from app.services.tw_research.research_run import ResearchRun


def _run(**overrides) -> ResearchRun:
    base = dict(
        research_run_id="rr_iso",
        user_id="u1",
        phase="phase1_first_pass",
        status="running",
        first_pass_job_id="fpj_iso",
        schedule_enabled=False,
    )
    base.update(overrides)
    return ResearchRun(**base)


def _patch_repo(monkeypatch, *, get_job=None, counts=None, progress=None):
    """Install repository stubs; callables raise when provided as exceptions."""

    class _Repo:
        def is_available(self) -> bool:
            return True

        def get_first_pass_job(self, job_id: str, user_id: str | None = None):
            if isinstance(get_job, Exception):
                raise get_job
            if callable(get_job):
                return get_job(job_id, user_id)
            return get_job

        def count_first_pass_symbols(self, job_id: str):
            if isinstance(counts, Exception):
                raise counts
            if callable(counts):
                return counts(job_id)
            return counts

        def latest_first_pass_symbol_data_progress_at(self, job_id: str):
            if isinstance(progress, Exception):
                raise progress
            if callable(progress):
                return progress(job_id)
            return progress

    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )
    monkeypatch.setattr(
        "app.services.tw_research.execution_status.first_pass_drain_availability",
        lambda user_id: (False, "first_pass_drain_globally_disabled"),
    )


def test_progress_read_failure_keeps_job_and_counts(monkeypatch) -> None:
    job = {
        "job_id": "fpj_iso",
        "status": "pending",
        "universe_size": 73,
        "ok_count": 5,
        "partial_count": 1,
        "failed_count": 0,
        "updated_at": "2026-11-17T04:00:00+00:00",
    }
    counts = {"ok": 5, "partial": 1, "failed": 0, "total": 6}
    _patch_repo(
        monkeypatch,
        get_job=job,
        counts=counts,
        progress=RuntimeError("progress_boom"),
    )
    response = routes._research_run_response(_run())  # noqa: SLF001
    execution = response.execution
    assert execution["jobId"] == "fpj_iso"
    assert execution["jobStatus"] == "pending"
    assert execution["processedCount"] == 6
    assert execution["pendingCount"] == 67
    assert execution["universeSize"] == 73
    assert execution["lastDataProgressAt"] is None
    assert execution["jobUpdatedAt"] == "2026-11-17T04:00:00+00:00"


def test_count_read_failure_does_not_fabricate_zero_of_universe(monkeypatch) -> None:
    job = {
        "job_id": "fpj_iso",
        "status": "pending",
        "universe_size": 73,
        # Deliberately omit ok/partial/failed so a naive fallback would invent 0/73.
        "updated_at": "2026-11-17T04:00:00+00:00",
    }
    _patch_repo(
        monkeypatch,
        get_job=job,
        counts=RuntimeError("count_boom"),
        progress="2026-11-17T03:00:00+00:00",
    )
    response = routes._research_run_response(_run())  # noqa: SLF001
    execution = response.execution
    assert execution["jobStatus"] == "pending"
    assert execution["universeSize"] == 73
    assert execution["processedCount"] is None
    assert execution["pendingCount"] is None
    assert execution["lastDataProgressAt"] == "2026-11-17T03:00:00+00:00"


def test_job_read_failure_still_attempts_counts_and_progress(monkeypatch) -> None:
    _patch_repo(
        monkeypatch,
        get_job=RuntimeError("job_boom"),
        counts={"ok": 2, "partial": 0, "failed": 0, "total": 2},
        progress="2026-11-17T03:11:00+00:00",
    )
    response = routes._research_run_response(_run())  # noqa: SLF001
    execution = response.execution
    # Without a job row, lease/status/universe come from job only.
    assert execution["jobStatus"] is None
    assert execution["processedCount"] is None
    assert execution["universeSize"] is None
    # Progress timestamp is independent and still returned.
    assert execution["lastDataProgressAt"] == "2026-11-17T03:11:00+00:00"


def test_all_reads_ok_returns_full_execution(monkeypatch) -> None:
    job = {
        "job_id": "fpj_iso",
        "status": "pending",
        "universe_size": 73,
        "updated_at": "2026-11-17T04:00:00+00:00",
    }
    _patch_repo(
        monkeypatch,
        get_job=job,
        counts={"ok": 3, "partial": 0, "failed": 0, "total": 3},
        progress="2026-11-17T03:10:00+00:00",
    )
    response = routes._research_run_response(_run())  # noqa: SLF001
    execution = response.execution
    assert execution["processedCount"] == 3
    assert execution["pendingCount"] == 70
    assert execution["lastDataProgressAt"] == "2026-11-17T03:10:00+00:00"


def test_classify_symbol_counts_unavailable_skips_job_aggregate_fallback() -> None:
    from app.services.tw_research.execution_status import classify_first_pass_execution

    result = classify_first_pass_execution(
        _run(),
        job={
            "status": "pending",
            "universe_size": 73,
            "ok_count": 10,
            "partial_count": 0,
            "failed_count": 0,
        },
        symbol_counts=None,
        symbol_counts_available=False,
        last_data_progress_at=None,
    )
    assert result["universeSize"] == 73
    assert result["processedCount"] is None
    assert result["pendingCount"] is None
    assert result["lastDataProgressAt"] is None
