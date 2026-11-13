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
