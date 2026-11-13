"""#348/#349: per-batch wall-clock budget + programming_error circuit breaker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.repositories.tw_research_repository import BATCH_LEASE_SECONDS
from app.services.tw_research.first_pass import (
    classify_field_reason,
    collect_first_pass_batch,
    log_isolated_fault,
    provider_exception_reason,
    sanitize_exception_text,
)
from app.services.tw_research.research_run import research_run_store
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class _InstRow:
    date: str
    foreign_net_shares: int = 1000
    investment_trust_net_shares: int = 0
    dealer_net_shares: int = 0
    total_net_shares: int = 1000


@dataclass(frozen=True)
class _InstResult:
    history: tuple = ()
    latest_date: str | None = None
    source: str = "fixture"


@dataclass(frozen=True)
class _MarginRow:
    date: str
    margin_balance_lots: float | None = 12.0


@dataclass(frozen=True)
class _MarginResult:
    history: tuple = ()
    summary: object | None = None
    latest_date: str | None = None
    source: str = "fixture"
    empty_reason: str | None = None


@dataclass(frozen=True)
class _OwnPoint:
    date: str
    holders_400_lots_or_more_percent: float | None


@dataclass(frozen=True)
class _OwnResult:
    points: tuple
    as_of: str | None
    source: str
    stale: bool = False


def _candles(days: int = 40) -> list[CandleRecord]:
    start = datetime(2024, 1, 2, 13, 30, tzinfo=TZ)
    out: list[CandleRecord] = []
    price = 100.0
    for index in range(days):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        price += 0.1
        out.append(
            CandleRecord(
                time=day.isoformat(),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000,
            )
        )
    return out


def _good_inst() -> _InstResult:
    rows = tuple(_InstRow(date=f"2024-06-{day:02d}") for day in range(20, 25))
    return _InstResult(history=rows, latest_date="2024-06-24", source="fixture")


def _good_margin() -> _MarginResult:
    history = tuple(
        _MarginRow(date=f"2024-06-{day:02d}", margin_balance_lots=10.0 + day)
        for day in range(20, 25)
    )
    return _MarginResult(
        history=history,
        summary=history[-1],
        latest_date="2024-06-24",
        source="fixture",
    )


def _good_own() -> _OwnResult:
    return _OwnResult(
        points=(
            _OwnPoint("2024-06-14", 38.0),
            _OwnPoint("2024-06-21", 40.0),
        ),
        as_of="2024-06-21",
        source="fixture",
    )


def _stock_loader(stock_id: str):
    return stock_record(
        stock_id,
        market="TW",
        symbol=stock_id.split(":")[-1],
        name=stock_id,
        name_zh_tw=stock_id,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _key_stats(_stock_id: str):
    return {
        "peRatio": 18.5,
        "peRatioType": "official_daily",
        "peRatioSource": "TWSE official daily P/E",
        "valuationAsOf": "2024-06-27",
        "epsTtm": None,
    }


class _FakeClock:
    """Monotonic clock that never sleeps — callers bump ``now`` explicitly."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _install_job_repo(monkeypatch, *user_ids: str):
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, *user_ids)
    from app.repositories.tw_research_repository import tw_research_repository

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)
    return session_local, tw_research_repository


def _base_collectors(*, ownership_loader=None, clock: _FakeClock | None = None):
    clock = clock or _FakeClock()

    def stock_loader(stock_id: str):
        if clock is not None:
            # Advance after the pre-symbol budget check has already passed.
            clock.advance(40.0)
        return _stock_loader(stock_id)

    return {
        "stock_loader": stock_loader,
        "candles_loader": lambda _stock: _candles(),
        "institutional_loader": lambda *_a, **_k: _good_inst(),
        "margin_loader": lambda *_a, **_k: _good_margin(),
        "ownership_loader": ownership_loader or (lambda _sid: _good_own()),
        "key_stats_loader": _key_stats,
    }


def test_sanitize_exception_text_strips_query_and_secrets() -> None:
    raw = (
        "GET https://provider.example/api/v1/tdcc?token=super-secret&x=1 "
        "Authorization: Bearer leaked-token-value"
    )
    cleaned = sanitize_exception_text(raw)
    assert "?" not in cleaned
    assert "super-secret" not in cleaned
    assert "leaked-token-value" not in cleaned
    assert "Authorization" not in cleaned
    assert "token=" not in cleaned.lower()
    assert "<redacted>" in cleaned


def test_budget_stops_after_three_symbols_and_releases_rest(monkeypatch) -> None:
    session_local, repo = _install_job_repo(monkeypatch, "user-budget")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    clock = _FakeClock()
    stock_ids = [f"TW:{1000 + i}" for i in range(8)]
    collectors = _base_collectors(clock=clock)
    # stock_loader advances 40s per symbol; budget 90 → finish 3, release 5.
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-budget",
        stock_ids=stock_ids,
        use_membership=False,
    )
    first = service.run_batch(
        job["job_id"],
        user_id="user-budget",
        batch_size=8,
        collectors=collectors,
        time_budget_seconds=90,
        monotonic_clock=clock,
    )
    assert first["budgetStopped"] is True
    assert first["processed"] == 3
    assert first["releasedUnstarted"] == 5
    assert first["durationMs"] < BATCH_LEASE_SECONDS * 1000
    assert first["batchCounts"]["ok"] + first["batchCounts"]["partial"] + first[
        "batchCounts"
    ]["failed"] == 3

    for sid in stock_ids[3:]:
        row = repo.get_first_pass_symbol(job["job_id"], sid)
        assert row is None  # never started — attempts unchanged (no row)

    # Next claim picks up the released ids (still unfinished).
    clock2 = _FakeClock()
    collectors2 = _base_collectors(clock=clock2)
    second = service.run_batch(
        job["job_id"],
        user_id="user-budget",
        batch_size=8,
        collectors=collectors2,
        time_budget_seconds=90,
        monotonic_clock=clock2,
    )
    assert second["processed"] == 3
    assert second["batchIds"][0] == stock_ids[3]
    assert set(stock_ids[3:6]).issubset(set(second["batchIds"]))
    repo.clear_session_factory_override()
    del session_local


def test_batch_counts_mixed_ok_partial_failed(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "user-counts")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    def ownership_loader(stock_id: str):
        if stock_id.endswith("1001"):
            raise TypeError("unexpected keyword argument 'range'")
        if stock_id.endswith("1002"):
            raise TimeoutError("upstream timed out")
        return _good_own()

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-counts",
        stock_ids=["TW:1000", "TW:1001", "TW:1002"],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-counts",
        batch_size=3,
        collectors={
            "stock_loader": _stock_loader,
            "candles_loader": lambda _s: _candles(),
            "institutional_loader": lambda *_a, **_k: _good_inst(),
            "margin_loader": lambda *_a, **_k: _good_margin(),
            "ownership_loader": ownership_loader,
            "key_stats_loader": _key_stats,
        },
        time_budget_seconds=90,
    )
    counts = result["batchCounts"]
    assert counts["ok"] >= 1
    assert counts["partial"] >= 1  # programming_error field → partial
    assert counts["failed"] >= 1  # timeout field → failed/retryable
    assert counts["ok"] + counts["partial"] + counts["failed"] == 3
    repo.clear_session_factory_override()


def test_programming_error_field_only_is_partial_with_peers_ok(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "user-prog")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    def ownership_loader(stock_id: str):
        if stock_id.endswith("2317"):
            raise TypeError("unexpected keyword argument 'range'")
        return _good_own()

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-prog",
        stock_ids=["TW:2330", "TW:2317"],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-prog",
        batch_size=2,
        collectors={
            "stock_loader": _stock_loader,
            "candles_loader": lambda _s: _candles(),
            "institutional_loader": lambda *_a, **_k: _good_inst(),
            "margin_loader": lambda *_a, **_k: _good_margin(),
            "ownership_loader": ownership_loader,
            "key_stats_loader": _key_stats,
        },
    )
    assert result.get("systemicProgrammingError") is None
    bad = repo.get_first_pass_symbol(job["job_id"], "TW:2317")
    good = repo.get_first_pass_symbol(job["job_id"], "TW:2330")
    assert bad is not None and good is not None
    assert bad["status"] == "partial"
    assert bad["payload"]["failureClass"] == "programming_error"
    assert good["status"] == "ok"
    tdcc = bad["payload"]["fields"]["tdcc_holders_ge_400_lots"]
    assert classify_field_reason(tdcc.get("reason")) == "programming_error"
    assert bad["payload"]["fields"]["institutional_5d"]["status"] == "ok"
    assert bad["payload"]["fields"]["margin"]["status"] in {"ok", "partial"}
    assert result["job"]["status"] != "failed"
    assert result["job"].get("active_snapshot_id") is None or result["job"][
        "status"
    ] in {"running", "partial", "completed", "deferred"}
    repo.clear_session_factory_override()


def _typeerror_ownership(_stock_id: str):
    raise TypeError("unexpected keyword argument 'range'")


def _typeerror_collectors() -> dict:
    return {
        "stock_loader": _stock_loader,
        "candles_loader": lambda _s: _candles(),
        "institutional_loader": lambda *_a, **_k: _good_inst(),
        "margin_loader": lambda *_a, **_k: _good_margin(),
        "ownership_loader": _typeerror_ownership,
        "key_stats_loader": _key_stats,
    }


def test_systemic_typeerror_fails_job_and_disarms_drain(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "owner")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "owner",
    )
    research_run_store.clear_for_tests()
    research_run_store.begin_phase1("owner", trading_day="2024-06-27")

    stock_ids = [f"TW:{2000 + i}" for i in range(10)]
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner",
        stock_ids=stock_ids,
        use_membership=False,
    )
    research_run_store.attach_first_pass_job("owner", first_pass_job_id=job["job_id"])
    assert research_run_store.get("owner").schedule_enabled is True
    monkeypatch.setattr(
        research_run_store,
        "_load_first_pass_job",
        lambda user, job_id: repo.get_first_pass_job(job_id, user_id=user),
    )

    result = service.run_batch(
        job["job_id"],
        user_id="owner",
        batch_size=10,
        collectors=_typeerror_collectors(),
    )
    assert result["job"]["status"] == "failed"
    assert result["job"].get("active_snapshot_id") is None
    systemic = result.get("systemicProgrammingError")
    assert systemic == "systemic_programming_error:tdcc_holders_ge_400_lots:TypeError"
    notes = " ".join(result["job"].get("notes") or [])
    assert systemic in notes

    run = research_run_store.reconcile("owner")
    assert run is not None
    assert run.status == "failed"
    assert run.schedule_enabled is False
    progress = (run.dimensions or {}).get("firstPassProgress") or {}
    assert progress.get("lastError") == systemic
    assert "first_pass_drain_disarmed_systemic_programming_error" in run.notes
    research_run_store.disable_test_memory()
    repo.clear_session_factory_override()


def test_failed_job_rerun_is_job_failed_without_snapshot(monkeypatch) -> None:
    """Final-batch breaker → next run_batch is terminal; _maybe_finalize early-outs."""
    _, repo = _install_job_repo(monkeypatch, "owner-rerun")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    stock_ids = [f"TW:{2100 + i}" for i in range(10)]
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-rerun",
        stock_ids=stock_ids,
        use_membership=False,
    )
    first = service.run_batch(
        job["job_id"],
        user_id="owner-rerun",
        batch_size=10,
        collectors=_typeerror_collectors(),
    )
    assert first["job"]["status"] == "failed"
    assert first.get("jobFailed") is True
    assert first["job"].get("active_snapshot_id") is None
    cursor_after = int(first["job"].get("cursor") or 0)
    gen_after = int(first["job"].get("lease_generation") or 0)
    reserve_after = int(first["job"].get("reserve_cursor") or 0)

    second = service.run_batch(
        job["job_id"],
        user_id="owner-rerun",
        batch_size=10,
        collectors=_typeerror_collectors(),
    )
    assert second.get("jobFailed") is True
    assert second["processed"] == 0
    assert second["job"]["status"] == "failed"
    assert second["job"].get("active_snapshot_id") is None
    assert second.get("batchIds") == []
    assert int(second["job"].get("cursor") or 0) == cursor_after
    assert int(second["job"].get("lease_generation") or 0) == gen_after
    assert int(second["job"].get("reserve_cursor") or 0) == reserve_after
    assert repo.get_first_pass_job(job["job_id"], user_id="owner-rerun")[
        "active_snapshot_id"
    ] is None
    repo.clear_session_factory_override()


def test_reconcile_last_error_note_dedup_after_systemic_fail(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "owner-dedup")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        "owner-dedup",
    )
    research_run_store.clear_for_tests()
    research_run_store.begin_phase1("owner-dedup", trading_day="2024-06-27")

    stock_ids = [f"TW:{2200 + i}" for i in range(10)]
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-dedup",
        stock_ids=stock_ids,
        use_membership=False,
    )
    research_run_store.attach_first_pass_job(
        "owner-dedup", first_pass_job_id=job["job_id"]
    )
    monkeypatch.setattr(
        research_run_store,
        "_load_first_pass_job",
        lambda user, job_id: repo.get_first_pass_job(job_id, user_id=user),
    )

    result = service.run_batch(
        job["job_id"],
        user_id="owner-dedup",
        batch_size=10,
        collectors=_typeerror_collectors(),
    )
    systemic = result.get("systemicProgrammingError")
    assert systemic is not None

    for _ in range(5):
        run = research_run_store.reconcile("owner-dedup")
        assert run is not None
        assert run.status == "failed"

    run = research_run_store.get("owner-dedup")
    assert run is not None
    last_error_notes = [n for n in run.notes if str(n).startswith("lastError:")]
    assert last_error_notes == [f"lastError:{systemic}"]
    assert (
        run.notes.count("first_pass_drain_disarmed_systemic_programming_error") == 1
    )
    research_run_store.disable_test_memory()
    repo.clear_session_factory_override()


def test_lease_renew_extends_and_blocks_concurrent_claim(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "user-lease")
    import app.repositories.tw_research_repository as repo_mod
    from app.services.tw_research.first_pass_job import FirstPassJobService

    clock = {"now": datetime(2024, 6, 27, 12, 0, 0, tzinfo=timezone.utc)}

    class _ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            current = clock["now"]
            return current if tz is None else current.astimezone(tz)

    monkeypatch.setattr(repo_mod, "datetime", _ClockDateTime)

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-lease",
        stock_ids=["TW:2330", "TW:2317"],
        use_membership=False,
    )
    claim = repo.claim_first_pass_batch(
        job["job_id"],
        batch_size=2,
        lease_seconds=BATCH_LEASE_SECONDS,
    )
    assert claim is not None
    assert claim.get("lease_held") is False
    gen = int(claim["lease_generation"])
    assert len(claim["batch_ids"]) == 2

    clock["now"] = clock["now"] + timedelta(seconds=250)
    assert repo.renew_first_pass_lease(job["job_id"], lease_generation=gen) is True
    held = repo.claim_first_pass_batch(job["job_id"], batch_size=2)
    assert held is not None
    assert held.get("lease_held") is True

    clock["now"] = clock["now"] + timedelta(seconds=250)
    assert repo.renew_first_pass_lease(job["job_id"], lease_generation=gen) is True
    held2 = repo.claim_first_pass_batch(job["job_id"], batch_size=2)
    assert held2 is not None
    assert held2.get("lease_held") is True
    repo.clear_session_factory_override()


def test_lease_renew_false_stops_batch_without_persist(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "user-lease-lost")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    renew_calls = {"n": 0}

    def fake_renew(*_a, **_k) -> bool:
        renew_calls["n"] += 1
        # Fail renew after the first finished symbol.
        return False

    monkeypatch.setattr(repo, "renew_first_pass_lease", fake_renew)

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-lease-lost",
        stock_ids=["TW:2330", "TW:2317", "TW:2454"],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-lease-lost",
        batch_size=3,
        collectors=_base_collectors(),
    )
    assert result.get("leaseLost") is True
    assert result["processed"] == 0
    assert renew_calls["n"] >= 1
    assert repo.list_first_pass_symbols(job["job_id"], limit=50) == []
    repo.clear_session_factory_override()


def test_one_of_ten_typeerror_does_not_trip_breaker(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "user-one")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    def ownership_loader(stock_id: str):
        if stock_id.endswith("2000"):
            raise TypeError("unexpected keyword argument 'range'")
        return _good_own()

    stock_ids = [f"TW:{2000 + i}" for i in range(10)]
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-one",
        stock_ids=stock_ids,
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-one",
        batch_size=10,
        collectors={
            "stock_loader": _stock_loader,
            "candles_loader": lambda _s: _candles(),
            "institutional_loader": lambda *_a, **_k: _good_inst(),
            "margin_loader": lambda *_a, **_k: _good_margin(),
            "ownership_loader": ownership_loader,
            "key_stats_loader": _key_stats,
        },
    )
    assert result.get("systemicProgrammingError") is None
    assert result["job"]["status"] != "failed"
    assert result["batchCounts"]["partial"] >= 1
    assert result["batchCounts"]["ok"] >= 9
    repo.clear_session_factory_override()


def test_log_isolated_fault_caplog_sanitizes_url_secrets(caplog) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="app.services.tw_research.first_pass")
    exc = TypeError(
        "failed GET https://chip.example/tdcc?token=abc123&q=1 "
        "Authorization: Bearer secret-header"
    )
    reason = provider_exception_reason(exc)
    log_isolated_fault(
        stock_id="TW:2330",
        stage="tdcc_holders_ge_400_lots",
        source="tw_ownership_concentration_service",
        exc=exc,
        reason=reason,
        job_id="fpj_test",
    )
    assert len(caplog.records) >= 1
    record = next(
        r for r in caplog.records if "first_pass_isolated_fault" in r.getMessage()
    )
    assert getattr(record, "stock_id", None) == "TW:2330"
    assert getattr(record, "stage", None) == "tdcc_holders_ge_400_lots"
    assert getattr(record, "source", None) == "tw_ownership_concentration_service"
    assert getattr(record, "exc_class", None) == "TypeError"
    assert getattr(record, "reason", None) == reason
    logged = f"{record.getMessage()} {getattr(record, 'fault_detail', '')}"
    assert "?" not in logged
    assert "Authorization" not in logged
    assert "token=" not in logged.lower()
    assert "abc123" not in logged
    assert "secret-header" not in logged


def test_collect_path_logs_isolated_fault_fields(caplog) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="app.services.tw_research.first_pass")

    def ownership_loader(_stock_id: str):
        raise TypeError(
            "bad call https://x.example/path?token=leak Authorization: Bearer z"
        )

    collect_first_pass_batch(
        ["TW:2330"],
        universe_version="uv",
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _good_inst(),
        margin_loader=lambda *_a, **_k: _good_margin(),
        ownership_loader=ownership_loader,
        key_stats_loader=_key_stats,
        job_id="fpj_log",
    )
    record = next(
        r for r in caplog.records if "first_pass_isolated_fault" in r.getMessage()
    )
    assert record.stock_id == "TW:2330"
    assert record.stage == "tdcc_holders_ge_400_lots"
    assert record.source == "tw_ownership_concentration_service"
    assert record.exc_class == "TypeError"
    assert "programming_error" in str(record.reason)
    logged = f"{record.getMessage()} {getattr(record, 'fault_detail', '')}"
    assert "?" not in logged
    assert "Authorization" not in logged
    assert "leak" not in logged


def test_malformed_null_institutional_and_margin_stay_unavailable() -> None:
    """Null/empty institutional + margin fixtures → unavailable with reason; peers ok."""

    result = collect_first_pass_batch(
        ["TW:2330"],
        universe_version="uv",
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _InstResult(
            history=(), latest_date=None, source="fixture"
        ),
        margin_loader=lambda *_a, **_k: _MarginResult(
            history=(),
            summary=None,
            latest_date=None,
            source="fixture",
            empty_reason="not_published",
        ),
        ownership_loader=lambda _sid: _good_own(),
        key_stats_loader=_key_stats,
    )
    row = result.records[0]
    inst = row.fields["institutional_5d"]
    margin = row.fields["margin"]
    tdcc = row.fields["tdcc_holders_ge_400_lots"]
    assert inst.status == "unavailable"
    assert inst.reason == "fewer_than_5_completed_sessions_in_store"
    assert margin.status == "unavailable"
    assert margin.reason == "not_published"
    assert tdcc.status in {"ok", "partial"}
    assert row.error is None


def test_malformed_institutional_rows_do_not_escape(monkeypatch) -> None:
    """Rows missing required attributes stay field-unavailable; batch continues."""

    @dataclass(frozen=True)
    class _BrokenRow:
        date: str
        # intentionally missing foreign_net_shares etc.

    def institutional_loader(_stock_id: str, _days: int):
        return _InstResult(
            history=tuple(_BrokenRow(date=f"2024-06-{d:02d}") for d in range(20, 25)),
            latest_date="2024-06-24",
            source="fixture",
        )

    result = collect_first_pass_batch(
        ["TW:2330", "TW:2317"],
        universe_version="uv",
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=institutional_loader,
        margin_loader=lambda *_a, **_k: _good_margin(),
        ownership_loader=lambda _sid: _good_own(),
        key_stats_loader=_key_stats,
    )
    assert len(result.records) == 2
    for row in result.records:
        inst = row.fields["institutional_5d"]
        assert inst.status == "unavailable"
        assert inst.reason is not None
        assert row.fields["margin"].status in {"ok", "partial"}
        assert row.fields["tdcc_holders_ge_400_lots"].status in {"ok", "partial"}


# ---------------------------------------------------------------------------
# Codex P2 follow-ups (C1 sanitized tracebacks, C2 atomic systemic failure,
# C3 real progress counts, C4 lease heartbeat + symbol soft deadline)
# ---------------------------------------------------------------------------

_SECRET_URL_MESSAGE = (
    "bad call GET https://chip.example/tdcc?token=abc&q=1 "
    "Authorization: Bearer xyz"
)


def _raise_secret_typeerror() -> None:
    raise TypeError(_SECRET_URL_MESSAGE)


def test_sanitize_log_text_and_traceback_helpers() -> None:
    from app.core.log_sanitize import sanitize_log_text, sanitize_traceback

    raw = _SECRET_URL_MESSAGE + " " + "x" * 400
    short = sanitize_log_text(raw)
    assert len(short) <= 200
    assert "abc" not in short and "xyz" not in short and "?" not in short
    full = sanitize_log_text(raw, limit=None)
    assert len(full) > 200
    assert "<redacted>" in full and "xyz" not in full

    tb = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "x.py", line 1, in f',
            f"TypeError: {_SECRET_URL_MESSAGE}",
        ]
    )
    cleaned = sanitize_traceback(tb)
    assert "TypeError" in cleaned and 'File "x.py"' in cleaned
    assert "abc" not in cleaned and "xyz" not in cleaned
    assert len(sanitize_traceback("a\n" * 5000, max_chars=100)) == 100


def test_log_isolated_fault_programming_error_never_logs_raw_exc_info(caplog) -> None:
    import json
    import logging

    from app.core.logging import ConsoleLogFormatter, JsonLogFormatter

    caplog.set_level(logging.WARNING, logger="app.services.tw_research.first_pass")
    try:
        _raise_secret_typeerror()
    except TypeError as exc:
        log_isolated_fault(
            stock_id="TW:2330",
            stage="tdcc_holders_ge_400_lots",
            source="tw_ownership_concentration_service",
            exc=exc,
            reason=provider_exception_reason(exc),
            job_id="fpj_tb",
        )
    record = next(
        r for r in caplog.records if "first_pass_isolated_fault" in r.getMessage()
    )
    # The original exception/traceback object must never reach logging.
    assert not record.exc_info
    traceback_text = getattr(record, "traceback_sanitized", "")
    assert "TypeError" in traceback_text
    assert 'File "' in traceback_text and "_raise_secret_typeerror" in traceback_text
    assert len(traceback_text) <= 4000

    json_line = JsonLogFormatter().format(record)
    console_line = ConsoleLogFormatter().format(record)
    for rendered in (json_line, console_line):
        assert "abc" not in rendered
        assert "xyz" not in rendered
        assert "token=" not in rendered.lower()
        assert "TypeError" in rendered
        assert "_raise_secret_typeerror" in rendered
    payload = json.loads(json_line)
    assert "TypeError" in payload["exception"]
    assert "traceback_sanitized" not in payload


def test_formatters_sanitize_raw_exc_info_from_other_loggers() -> None:
    import json
    import logging
    import sys

    from app.core.logging import ConsoleLogFormatter, JsonLogFormatter

    try:
        _raise_secret_typeerror()
    except TypeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "other.logger", logging.ERROR, __file__, 1, "boom", (), exc_info
    )
    json_line = JsonLogFormatter().format(record)
    console_line = ConsoleLogFormatter().format(record)
    for rendered in (json_line, console_line):
        assert "abc" not in rendered and "xyz" not in rendered
        assert "TypeError" in rendered
        assert "_raise_secret_typeerror" in rendered
    assert "TypeError" in json.loads(json_line)["exception"]


def test_systemic_batch_completes_atomically_with_terminal_error(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "owner-atomic")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    def _boom(*_a, **_k):
        raise AssertionError("fail_first_pass_job must not run on the systemic path")

    monkeypatch.setattr(repo, "fail_first_pass_job", _boom)
    seen: dict = {}
    real_complete = repo.complete_first_pass_batch

    def spy_complete(job_id, **kwargs):
        seen.update(kwargs)
        return real_complete(job_id, **kwargs)

    monkeypatch.setattr(repo, "complete_first_pass_batch", spy_complete)

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-atomic",
        stock_ids=[f"TW:{2300 + i}" for i in range(10)],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="owner-atomic",
        batch_size=10,
        collectors=_typeerror_collectors(),
    )
    systemic = "systemic_programming_error:tdcc_holders_ge_400_lots:TypeError"
    assert seen["terminal_error"] == systemic
    assert result["jobFailed"] is True
    assert result["leaseLost"] is False
    assert result["systemicProgrammingError"] == systemic
    assert result["job"]["status"] == "failed"
    assert result["job"]["notes"].count(f"error:{systemic}") == 1
    stored = repo.get_first_pass_job(job["job_id"], user_id="owner-atomic")
    assert stored["status"] == "failed"
    assert stored["active_snapshot_id"] is None
    repo.clear_session_factory_override()


def test_complete_with_terminal_error_is_single_locked_transition(monkeypatch) -> None:
    session_local, repo = _install_job_repo(monkeypatch, "owner-term")
    from app.db.models import TwResearchFirstPassJob
    from app.services.tw_research.first_pass_job import FirstPassJobService

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-term",
        stock_ids=["TW:2330", "TW:2317", "TW:2454"],
        use_membership=False,
    )
    claim = repo.claim_first_pass_batch(job["job_id"], batch_size=3)
    gen = int(claim["lease_generation"])
    assert repo.upsert_first_pass_symbol(
        job_id=job["job_id"],
        stock_id="TW:2330",
        status="ok",
        payload={"stockId": "TW:2330"},
        lease_generation=gen,
    )
    done = repo.complete_first_pass_batch(
        job["job_id"],
        lease_generation=gen,
        processed_count=1,
        terminal_error="systemic_programming_error:x:TypeError",
    )
    assert done is not None
    assert done["job"]["status"] == "failed"
    assert done["job"]["cursor"] == 1  # first missing index
    assert done["job"]["notes"][-1] == "error:systemic_programming_error:x:TypeError"
    with session_local() as session:
        row = session.get(TwResearchFirstPassJob, job["job_id"])
        assert row.reserve_expires_at is None
        assert row.reserve_cursor == 1
        assert row.status == "failed"
    # A later claim sees a terminal failed job — never re-leases it.
    again = repo.claim_first_pass_batch(job["job_id"], batch_size=3)
    assert again.get("terminal_failed") is True
    repo.clear_session_factory_override()


def test_systemic_generation_mismatch_reports_lease_lost_not_job_failed(
    monkeypatch,
) -> None:
    session_local, repo = _install_job_repo(monkeypatch, "owner-race")
    from app.db.models import TwResearchFirstPassJob
    from app.services.tw_research.first_pass_job import FirstPassJobService

    monkeypatch.setattr(
        repo,
        "fail_first_pass_job",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("not expected")),
    )
    real_complete = repo.complete_first_pass_batch

    def raced_complete(job_id, **kwargs):
        # Another worker re-claims (bumps generation) just before we complete.
        with session_local() as session:
            row = session.get(TwResearchFirstPassJob, job_id)
            row.lease_generation = int(row.lease_generation) + 1
            session.commit()
        return real_complete(job_id, **kwargs)

    monkeypatch.setattr(repo, "complete_first_pass_batch", raced_complete)
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-race",
        stock_ids=[f"TW:{2400 + i}" for i in range(10)],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="owner-race",
        batch_size=10,
        collectors=_typeerror_collectors(),
    )
    # Upserts happened before the race; counts stay reportable but the job is
    # NOT failed because the terminal write was fenced out.
    assert result["leaseLost"] is True
    assert result["jobFailed"] is False
    stored = repo.get_first_pass_job(job["job_id"], user_id="owner-race")
    assert stored["status"] != "failed"
    assert not any(str(n).startswith("error:") for n in stored["notes"])
    repo.clear_session_factory_override()


def test_reconcile_uses_real_symbol_counts_from_repository(monkeypatch) -> None:
    _, repo = _install_job_repo(monkeypatch, "owner-counts")
    from app.services.tw_research.first_pass_job import FirstPassJobService
    from app.services.tw_research.research_run import ResearchRunStore

    def ownership_loader(stock_id: str):
        if stock_id.endswith("1"):
            raise TimeoutError("upstream timed out")
        return _good_own()

    store = ResearchRunStore(repository=repo)
    store.clear_for_tests()
    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="owner-counts",
        stock_ids=[f"TW:{2500 + i}" for i in range(6)],
        use_membership=False,
    )
    store.begin_phase1("owner-counts", trading_day="2024-06-27")
    store.attach_first_pass_job("owner-counts", first_pass_job_id=job["job_id"])
    collectors = _base_collectors()
    collectors["ownership_loader"] = ownership_loader
    service.run_batch(
        job["job_id"],
        user_id="owner-counts",
        batch_size=3,
        collectors=collectors,
    )
    db_counts = repo.count_first_pass_symbols(job["job_id"])
    assert db_counts["total"] == 3
    run = store.reconcile("owner-counts")
    progress = run.dimensions["firstPassProgress"]
    assert progress["processed"] == db_counts["total"] == 3
    assert progress["total"] == 6
    assert progress["okCount"] == db_counts["ok"]
    assert progress["partialCount"] == db_counts["partial"]
    assert progress["failedCount"] == db_counts["failed"]
    assert progress["okCount"] + progress["partialCount"] + progress["failedCount"] == 3
    assert progress["failedCount"] >= 1
    store.disable_test_memory()
    repo.clear_session_factory_override()


@pytest.mark.parametrize("variant", ["missing", "raises"])
def test_reconcile_falls_back_to_job_dict_without_usable_counter(variant) -> None:
    from app.services.tw_research.research_run import ResearchRunStore

    class _Repo:
        def is_available(self) -> bool:
            return True

        def get_first_pass_job(self, job_id, *, user_id):
            return {
                "job_id": job_id,
                "status": "running",
                "universe_size": 9,
                "processed_count": 4,
                "ok_count": 2,
                "partial_count": 1,
                "failed_count": 1,
            }

    repo = _Repo()
    if variant == "raises":

        def _raise(_job_id):
            raise RuntimeError("db down")

        repo.count_first_pass_symbols = _raise  # type: ignore[attr-defined]
    store = ResearchRunStore(repository=repo)  # type: ignore[arg-type]
    store.clear_for_tests()
    store.begin_phase1("u1", trading_day="2024-06-27")
    store.attach_first_pass_job("u1", first_pass_job_id="fpj_stub")
    run = store.reconcile("u1")
    progress = run.dimensions["firstPassProgress"]
    assert (progress["processed"], progress["total"]) == (4, 9)
    assert (progress["okCount"], progress["partialCount"], progress["failedCount"]) == (
        2,
        1,
        1,
    )
    store.disable_test_memory()


def _wait_until(predicate, *, timeout: float = 5.0) -> bool:
    import time as _time

    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if predicate():
            return True
        _time.sleep(0.002)
    return False


def test_heartbeat_renews_lease_during_one_slow_symbol_and_cleans_up(
    monkeypatch,
) -> None:
    import threading
    import time

    _, repo = _install_job_repo(monkeypatch, "user-hb")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    baseline_threads = threading.active_count()
    heartbeat_renews = {"n": 0}

    def fake_renew(*_a, **_k) -> bool:
        if threading.current_thread().name.startswith("first-pass-lease-heartbeat"):
            heartbeat_renews["n"] += 1
        return True

    monkeypatch.setattr(repo, "renew_first_pass_lease", fake_renew)
    seen_at_loader_exit: dict = {}

    def slow_ownership(_sid: str):
        # Simulates a >400s symbol: it only finishes once the heartbeat has
        # renewed at least five times (no real 60s sleeps).
        assert _wait_until(lambda: heartbeat_renews["n"] >= 5)
        seen_at_loader_exit["n"] = heartbeat_renews["n"]
        return _good_own()

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-hb", stock_ids=["TW:2330"], use_membership=False
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-hb",
        batch_size=1,
        collectors=_base_collectors(ownership_loader=slow_ownership),
        lease_heartbeat_seconds=0.01,
        lease_heartbeat_sleep=lambda _s: time.sleep(0.001),
        symbol_deadline_seconds=None,
    )
    assert seen_at_loader_exit["n"] >= 5
    assert result["processed"] == 1
    assert result["leaseLost"] is False
    assert not any(
        t.name.startswith("first-pass-lease-heartbeat") for t in threading.enumerate()
    )
    assert threading.active_count() == baseline_threads
    repo.clear_session_factory_override()


def test_heartbeat_renew_false_sets_lease_lost_without_persist(monkeypatch) -> None:
    import threading
    import time

    _, repo = _install_job_repo(monkeypatch, "user-hb-lost")
    from app.services.tw_research.first_pass_job import FirstPassJobService

    baseline_threads = threading.active_count()
    heartbeat_false = {"seen": False}

    def fake_renew(*_a, **_k) -> bool:
        if threading.current_thread().name.startswith("first-pass-lease-heartbeat"):
            heartbeat_false["seen"] = True
            return False
        return True  # per-symbol renew alone would not notice

    monkeypatch.setattr(repo, "renew_first_pass_lease", fake_renew)

    def slow_ownership(_sid: str):
        assert _wait_until(lambda: heartbeat_false["seen"])
        return _good_own()

    service = FirstPassJobService(repository=repo)
    job = service.create_job(
        user_id="user-hb-lost",
        stock_ids=["TW:2330", "TW:2317", "TW:2454"],
        use_membership=False,
    )
    result = service.run_batch(
        job["job_id"],
        user_id="user-hb-lost",
        batch_size=3,
        collectors=_base_collectors(ownership_loader=slow_ownership),
        lease_heartbeat_seconds=0.01,
        lease_heartbeat_sleep=lambda _s: time.sleep(0.001),
        symbol_deadline_seconds=None,
    )
    assert result["leaseLost"] is True
    assert result["jobFailed"] is False
    assert result["processed"] == 0
    assert repo.list_first_pass_symbols(job["job_id"], limit=50) == []
    assert threading.active_count() == baseline_threads
    repo.clear_session_factory_override()


def test_collect_stops_on_lease_lost_check_before_and_after_symbols() -> None:
    calls: list[str] = []

    def stock_loader(stock_id: str):
        calls.append(stock_id)
        return _stock_loader(stock_id)

    state = {"lost": False}

    def lease_lost_check() -> bool:
        return state["lost"]

    def ownership_loader(_sid: str):
        state["lost"] = True  # heartbeat flags loss while symbol #1 is running
        return _good_own()

    result = collect_first_pass_batch(
        ["TW:2330", "TW:2317", "TW:2454"],
        universe_version="uv",
        stock_loader=stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _good_inst(),
        margin_loader=lambda *_a, **_k: _good_margin(),
        ownership_loader=ownership_loader,
        key_stats_loader=_key_stats,
        lease_renew=lambda: True,
        lease_lost_check=lease_lost_check,
    )
    assert result.lease_lost is True
    assert calls == ["TW:2330"]
    assert result.released_unstarted == ["TW:2317", "TW:2454"]

    # Already lost before the first symbol → nothing starts.
    calls.clear()
    pre = collect_first_pass_batch(
        ["TW:2330", "TW:2317"],
        universe_version="uv",
        stock_loader=stock_loader,
        lease_lost_check=lambda: True,
    )
    assert pre.lease_lost is True
    assert calls == []
    assert pre.released_unstarted == ["TW:2330", "TW:2317"]


def test_symbol_deadline_marks_only_ownership_field_unavailable() -> None:
    clock = _FakeClock()

    def slow_ownership(_sid: str):
        clock.advance(300.0)  # > 240s soft deadline
        return _good_own()

    def fast_ownership(_sid: str):
        clock.advance(1.0)
        return _good_own()

    result = collect_first_pass_batch(
        ["TW:2330"],
        universe_version="uv",
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _good_inst(),
        margin_loader=lambda *_a, **_k: _good_margin(),
        ownership_loader=slow_ownership,
        key_stats_loader=_key_stats,
        monotonic_clock=clock,
        symbol_deadline_seconds=240,
    )
    fields = result.records[0].fields
    tdcc = fields["tdcc_holders_ge_400_lots"]
    assert tdcc.status == "unavailable"
    assert tdcc.reason == "collector_deadline_exceeded"
    assert classify_field_reason(tdcc.reason) == "timeout"
    assert fields["institutional_5d"].status == "ok"
    assert fields["margin"].status in {"ok", "partial"}

    fast = collect_first_pass_batch(
        ["TW:2330"],
        universe_version="uv",
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _good_inst(),
        margin_loader=lambda *_a, **_k: _good_margin(),
        ownership_loader=fast_ownership,
        key_stats_loader=_key_stats,
        monotonic_clock=clock,
        symbol_deadline_seconds=240,
    )
    assert fast.records[0].fields["tdcc_holders_ge_400_lots"].status in {"ok", "partial"}


def test_collector_deadline_exception_becomes_retryable_field_reason() -> None:
    from app.services.tw_ownership_concentration_service import (
        CollectorDeadlineExceeded,
    )

    def ownership_loader(_sid: str):
        raise CollectorDeadlineExceeded("collector_deadline_exceeded")

    result = collect_first_pass_batch(
        ["TW:2330"],
        universe_version="uv",
        stock_loader=_stock_loader,
        candles_loader=lambda _s: _candles(),
        institutional_loader=lambda *_a, **_k: _good_inst(),
        margin_loader=lambda *_a, **_k: _good_margin(),
        ownership_loader=ownership_loader,
        key_stats_loader=_key_stats,
    )
    record = result.records[0]
    tdcc = record.fields["tdcc_holders_ge_400_lots"]
    assert tdcc.reason == "collector_deadline_exceeded"
    assert record.error == "collector_deadline_exceeded"  # retryable transient
    assert record.fields["institutional_5d"].status == "ok"


def test_portal_friday_loop_stops_at_soft_deadline(monkeypatch) -> None:
    from datetime import date

    import app.services.tw_ownership_concentration_service as svc

    clock = _FakeClock()
    posts = {"n": 0}

    class _Resp:
        text = "<html/>"

    def fake_request(_client, *, method, **_kwargs):
        if method == "POST":
            posts["n"] += 1
            clock.advance(100.0)
        return _Resp()

    fridays = [f"2024-06-{day:02d}" for day in (28, 21, 14, 7)] + [
        f"2024-05-{day:02d}" for day in (31, 24, 17, 10)
    ]
    monkeypatch.setattr(svc, "_portal_request_with_retries", fake_request)
    monkeypatch.setattr(svc, "extract_tdcc_synchronizer_token", lambda _t: "tok")
    monkeypatch.setattr(svc, "extract_tdcc_available_dates", lambda _t: fridays)
    monkeypatch.setattr(svc, "parse_tdcc_qry_stock_html", lambda *_a, **_k: None)

    with svc.collector_deadline_scope(clock() + 240.0, clock):
        with pytest.raises(svc.CollectorDeadlineExceeded):
            svc._portal_history_with_client(
                object(),  # type: ignore[arg-type]
                symbol="2330",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 30),
                max_weeks=8,
            )
    assert posts["n"] == 3  # 0s, 100s, 200s → at 300s the loop stops

    # No scope → unbounded as before.
    posts["n"] = 0
    points = svc._portal_history_with_client(
        object(),  # type: ignore[arg-type]
        symbol="2330",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 30),
        max_weeks=8,
    )
    assert points == [] and posts["n"] == 8
