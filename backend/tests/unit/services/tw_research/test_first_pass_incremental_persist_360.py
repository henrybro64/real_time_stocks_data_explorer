"""Incremental per-symbol persist and lease-expiry fencing (#360)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.tw_research.first_pass import (
    DEFAULT_SYMBOL_DEADLINE_SECONDS,
    FirstPassSymbolRecord,
    collect_first_pass_batch,
)
from tests.unit.services.tw_research.test_first_pass_budget_circuit_348_349 import (
    _base_collectors,
    _install_job_repo,
)
from app.services.tw_research.first_pass_job import FirstPassJobService


def test_default_symbol_deadline_is_under_batch_budget() -> None:
    assert DEFAULT_SYMBOL_DEADLINE_SECONDS == 45.0
    assert DEFAULT_SYMBOL_DEADLINE_SECONDS < 60.0


def test_on_symbol_collected_persists_before_next_symbol() -> None:
    seen: list[str] = []

    def _on_symbol(record: FirstPassSymbolRecord) -> bool:
        seen.append(record.stock_id)
        # Stop after first symbol — second must remain unstarted.
        return len(seen) < 1

    collectors = _base_collectors()
    result = collect_first_pass_batch(
        ["TW:2330", "TW:2454"],
        time_budget_seconds=90,
        symbol_deadline_seconds=5,
        on_symbol_collected=_on_symbol,
        **collectors,
    )
    assert seen == ["TW:2330"]
    assert [r.stock_id for r in result.records] == ["TW:2330"]
    assert result.released_unstarted == ["TW:2454"]
    assert result.lease_lost is True


def test_lease_renew_false_skips_on_symbol_collected() -> None:
    seen: list[str] = []

    def _on_symbol(record: FirstPassSymbolRecord) -> bool:
        seen.append(record.stock_id)
        return True

    collectors = _base_collectors()
    result = collect_first_pass_batch(
        ["TW:2330", "TW:2454"],
        lease_renew=lambda: False,
        on_symbol_collected=_on_symbol,
        **collectors,
    )
    assert seen == []
    assert [r.stock_id for r in result.records] == ["TW:2330"]
    assert result.lease_lost is True
    assert result.released_unstarted == ["TW:2454"]


def test_upsert_rejects_expired_lease(monkeypatch) -> None:
    session_local, repo = _install_job_repo(monkeypatch, "owner-lease-exp")
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-lease-exp",
        stock_ids=["TW:2330"],
        use_membership=False,
    )
    claim = repo.claim_first_pass_batch(job["job_id"], batch_size=1)
    gen = int(claim["lease_generation"])
    from app.db.models import TwResearchFirstPassJob

    with session_local() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        assert row is not None
        row.reserve_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    assert (
        repo.upsert_first_pass_symbol(
            job_id=job["job_id"],
            stock_id="TW:2330",
            status="ok",
            payload={"stockId": "TW:2330"},
            lease_generation=gen,
        )
        is False
    )
    repo.clear_session_factory_override()
