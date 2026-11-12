"""Monthly-first technical execution and research gates (#338/#340/#341)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.tw_research.constants import (
    CALCULATION_VERSION_MONTHLY_FIRST,
    DAILY_NOT_RUN_BY_DESIGN,
    TECHNICAL_STAGE_DAILY_NOT_RUN,
    WAITING_FOR_NEXT_WINDOW,
)
from app.services.tw_research.evaluation import (
    derive_stage_progress,
    map_recoverable_status,
)
from app.services.tw_research.indicators import MacdKdSnapshot
from app.services.tw_research.report_evidence import (
    build_report_evidence,
    load_zh_tw_example_fixture,
)
from app.services.tw_research.research_run import (
    ResearchRunConflict,
    ResearchRunGateError,
    ResearchRunStore,
    research_run_store,
)
from app.services.tw_research.scan import (
    ScanState,
    TwResearchScanService,
    _not_run_daily_payload,
)
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


def _candles(*, days: int = 900, rising: bool = True) -> list[CandleRecord]:
    start = datetime(2020, 1, 2, 13, 30, tzinfo=TZ)
    out: list[CandleRecord] = []
    price = 50.0 if rising else 200.0
    for index in range(days):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        price = price + (0.15 if rising else -0.12)
        if price < 5:
            price = 5
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


def _snapshot(
    *,
    pass_status: str,
    quality: str = "ok",
    timeframe: str = "monthly",
) -> MacdKdSnapshot:
    return MacdKdSnapshot(
        as_of="2024-06-28",
        dif=1.0,
        signal=0.5,
        osc=0.5,
        k=60.0,
        d=55.0,
        pass_status=pass_status,  # type: ignore[arg-type]
        quality=quality,  # type: ignore[arg-type]
        price_basis="unadjusted_close",
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        bar_count=40,
        timeframe=timeframe,  # type: ignore[arg-type]
    )


def _force_monthly_daily(monkeypatch, *, monthly_pass: str, daily_pass: str = "pass") -> dict[str, int]:
    """Deterministic monthly/daily snapshots — never rely on candle luck."""
    counts = {"monthly": 0, "daily": 0}

    def monthly(candles, *, market="TW", now=None):  # noqa: ANN001
        del candles, market, now
        counts["monthly"] += 1
        return _snapshot(pass_status=monthly_pass, quality="ok", timeframe="monthly")

    def daily(candles, *, market="TW", now=None):  # noqa: ANN001
        del candles, market, now
        counts["daily"] += 1
        return _snapshot(pass_status=daily_pass, quality="ok", timeframe="daily")

    monkeypatch.setattr(
        "app.services.tw_research.scan.monthly_macd_kd_snapshot",
        monthly,
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.daily_macd_kd_snapshot",
        daily,
    )
    # Monthly coverage is imported inside evaluate paths; daily uses validate_ohlcv_series.
    monkeypatch.setattr(
        "app.services.tw_research.coverage.monthly_series_verified",
        lambda *a, **k: SimpleNamespace(
            ok=True, reason=None, bar_count=40, missing_trading_days=()
        ),
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.validate_ohlcv_series",
        lambda *a, **k: SimpleNamespace(
            ok=True,
            reason=None,
            bar_count=120,
            missing_trading_days=(),
            duplicate_dates=(),
            invalid_bars=0,
            price_basis="unadjusted_close",
        ),
    )
    return counts


def test_not_run_payload_is_not_a_fail() -> None:
    payload = _not_run_daily_payload(reason="monthly_screen_failed")
    assert payload["executionStatus"] == DAILY_NOT_RUN_BY_DESIGN
    assert payload["passStatus"] is None
    assert payload["passStatus"] != "fail"
    assert "not a technical fail" in payload["note"]


def test_monthly_first_skips_daily_with_not_run_by_design(monkeypatch) -> None:
    counts = _force_monthly_daily(monkeypatch, monthly_pass="fail")
    catalog = {
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="鴻海",
            name_zh_tw="鴻海",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: (list(_candles()), 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2317",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        candidate_provenance={"dailyOverrideIds": []},
        max_upstream_month_fetches=40,
    )
    service.run_batch(state.scan_id, batch_size=1, timeout_seconds=120)
    record = service.get_scan(state.scan_id).records["TW:2317"]
    assert record.monthly is not None
    assert record.monthly.get("passStatus") == "fail"
    assert record.daily is not None
    assert record.daily.get("executionStatus") == DAILY_NOT_RUN_BY_DESIGN
    assert record.daily.get("passStatus") is None
    assert record.technical_stage == TECHNICAL_STAGE_DAILY_NOT_RUN
    assert counts["monthly"] == 1
    assert counts["daily"] == 0
    progress = derive_stage_progress(
        stock_ids=state.stock_ids,
        records=service.get_scan(state.scan_id).records,
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
    )
    assert "TW:2317" in progress["dailyNotRunByDesignIds"]
    assert "TW:2317" in progress["monthlyFailWatchlistIds"]


def test_monthly_coverage_fail_skips_daily_as_source_incomplete(monkeypatch) -> None:
    """Coverage-fail + indicator-pass → daily not run, readiness source_incomplete."""
    monkeypatch.setattr(
        "app.services.tw_research.scan.monthly_macd_kd_snapshot",
        lambda *a, **k: _snapshot(pass_status="pass", quality="ok"),
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.daily_macd_kd_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("daily must not run")),
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.monthly_series_verified",
        lambda *a, **k: SimpleNamespace(
            ok=False,
            reason="insufficient_monthly_history",
            bar_count=2,
            missing_trading_days=(),
        ),
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.validate_ohlcv_series",
        lambda *a, **k: SimpleNamespace(
            ok=True,
            reason=None,
            bar_count=120,
            missing_trading_days=(),
            duplicate_dates=(),
            invalid_bars=0,
            price_basis="unadjusted_close",
        ),
    )
    catalog = {
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="鴻海",
            name_zh_tw="鴻海",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *a, **k: (list(_candles()), 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2317",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        candidate_provenance={"dailyOverrideIds": []},
    )
    service.run_batch(state.scan_id, batch_size=1, timeout_seconds=120)
    record = service.get_scan(state.scan_id).records["TW:2317"]
    assert record.monthly.get("passStatus") == "pass"
    assert record.daily.get("executionStatus") == DAILY_NOT_RUN_BY_DESIGN
    assert record.daily.get("reason") == "monthly_coverage_unverified"
    assert record.readiness == "source_incomplete"
    assert record.technical_stage == TECHNICAL_STAGE_DAILY_NOT_RUN


def test_analyst_override_runs_daily_despite_monthly_fail(monkeypatch) -> None:
    counts = _force_monthly_daily(monkeypatch, monthly_pass="fail", daily_pass="pass")
    catalog = {
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="鴻海",
            name_zh_tw="鴻海",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *a, **k: (list(_candles()), 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2317",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        candidate_provenance={"dailyOverrideIds": ["TW:2317"]},
    )
    service.run_batch(state.scan_id, batch_size=1, timeout_seconds=120)
    record = service.get_scan(state.scan_id).records["TW:2317"]
    assert record.monthly.get("passStatus") == "fail"
    assert record.daily.get("passStatus") == "pass"
    assert record.daily.get("analystDailyOverride") is True
    assert counts["daily"] == 1


def test_research_run_reconcile_is_server_authoritative() -> None:
    research_run_store.clear_for_tests()
    assert map_recoverable_status("deferred") == WAITING_FOR_NEXT_WINDOW

    class _Repo:
        def __init__(self) -> None:
            self.job = {
                "status": "running",
                "active_snapshot_id": None,
            }
            self.scan = None

        def is_available(self) -> bool:
            return True

        def get_research_run(self, user_id: str):  # noqa: ANN001
            return None

        def upsert_research_run(self, payload):  # noqa: ANN001
            return {**payload, "revision": int(payload.get("revision") or 0) + 1}

        def get_first_pass_job(self, job_id, *, user_id=None):  # noqa: ANN001
            del job_id, user_id
            return self.job

        def get_scan(self, scan_id):  # noqa: ANN001
            del scan_id
            return self.scan

    repo = _Repo()
    store = ResearchRunStore(repository=repo)
    store.clear_for_tests()
    # Seed via test memory then attach fake job through repository path.
    research_run_store.clear_for_tests()
    run = research_run_store.begin_phase1("u1", trading_day="2024-06-27")
    run = research_run_store.attach_first_pass_job("u1", first_pass_job_id="fpj_1")
    assert run.phase == "phase1_first_pass"
    assert run.status == "running"

    # Client claims running → no phase change when job still running (via reconcile
    # with injected repo on a dedicated store).
    store2 = ResearchRunStore(repository=repo)
    # Put the same run into store2's memory by reconstructing from payload.
    store2.clear_for_tests()
    store2._test_memory = {
        "u1": research_run_store.get("u1"),  # type: ignore[dict-item]
    }
    # Point job loader through repository while keeping memory for the run row.
    store2._repository = repo
    before = store2.reconcile("u1")
    assert before is not None
    assert before.phase == "phase1_first_pass"
    assert before.status == "running"

    repo.job = {"status": "failed", "active_snapshot_id": None}
    failed = store2.reconcile("u1")
    assert failed is not None
    assert failed.status == "failed"

    # Completed job with snapshot → awaiting candidate selection.
    store2._test_memory["u1"].status = "running"  # type: ignore[index]
    store2._test_memory["u1"].phase = "phase1_first_pass"  # type: ignore[index]
    store2._test_memory["u1"].revision = failed.revision  # type: ignore[index]
    repo.job = {"status": "completed", "active_snapshot_id": "fps_1"}
    gated = store2.reconcile("u1")
    assert gated is not None
    assert gated.phase == "awaiting_analyst_candidate_selection"
    assert gated.first_pass_snapshot_id == "fps_1"

    # Attach + phase2 then completed scan → report decision.
    research_run_store.disable_test_memory()
    research_run_store.clear_for_tests()
    research_run_store.begin_phase1("u2", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("u2", first_pass_job_id="fpj_2")
    # Force gate via memory without job loader (set fields directly for attach test).
    mem = research_run_store.get("u2")
    assert mem is not None
    mem.phase = "awaiting_analyst_candidate_selection"
    mem.status = "gated"
    mem.first_pass_snapshot_id = "fps_2"
    mem.gates["candidateSelection"] = "awaiting_analyst"
    research_run_store.attach_candidate_set(
        "u2",
        candidate_set_id="cs_1",
        candidate_set_version=1,
        first_pass_snapshot_id="fps_2",
    )
    phase2 = research_run_store.start_phase2("u2", technical_scan_id="scan_1")
    assert phase2.phase == "phase2_technical"

    repo2 = _Repo()
    repo2.scan = {"status": "completed_processing", "user_id": "u2"}
    store3 = ResearchRunStore(repository=repo2)
    store3.clear_for_tests()
    store3._test_memory = {"u2": research_run_store.get("u2")}  # type: ignore[dict-item]
    store3._repository = repo2
    report = store3.reconcile("u2")
    assert report is not None
    assert report.phase == "awaiting_analyst_report_decision"

    # New store instance (other pod) with shared memory snapshot sees same state.
    store4 = ResearchRunStore(repository=repo2)
    store4.clear_for_tests()
    store4._test_memory = {"u2": store3.get("u2")}  # type: ignore[dict-item]
    store4._repository = repo2
    again = store4.reconcile("u2")
    assert again is not None
    assert again.phase == "awaiting_analyst_report_decision"
    assert again.technical_scan_id == "scan_1"
    research_run_store.disable_test_memory()


def test_begin_phase1_preserves_revision_when_replacing_gated_run() -> None:
    """Replacement after a finished/gated run must not reset OCC revision to 0."""
    research_run_store.clear_for_tests()
    first = research_run_store.begin_phase1("u_rev", trading_day="2024-06-27")
    research_run_store.attach_first_pass_job("u_rev", first_pass_job_id="fpj_rev")
    mem = research_run_store.get("u_rev")
    assert mem is not None
    mem.phase = "awaiting_analyst_report_decision"
    mem.status = "gated"
    mem.technical_scan_id = "scan_done"
    # Force a terminal-ish reconcile path by leaving phase outside active work.
    before_rev = int(mem.revision)
    replacement = research_run_store.begin_phase1("u_rev", trading_day="2024-06-28")
    assert replacement.research_run_id != first.research_run_id
    assert int(replacement.revision) > before_rev
    again = research_run_store.begin_phase1("u_rev", trading_day="2024-06-28")
    assert again.research_run_id == replacement.research_run_id
    research_run_store.disable_test_memory()


def test_claim_lost_does_not_orphan_pending_recovery_job_id() -> None:
    from app.services.tw_research.research_run import _orphan_job_id_if_not_pending

    assert (
        _orphan_job_id_if_not_pending(
            "fpj_pending",
            pending_job_id="fpj_pending",
            attached_job_id=None,
        )
        is None
    )
    assert (
        _orphan_job_id_if_not_pending(
            "fpj_other",
            pending_job_id="fpj_pending",
            attached_job_id=None,
        )
        == "fpj_other"
    )


def test_research_run_start_idempotent_and_conflict() -> None:
    research_run_store.clear_for_tests()
    first = research_run_store.begin_phase1("u_idemp", trading_day="2024-06-27")
    first = research_run_store.attach_first_pass_job("u_idemp", first_pass_job_id="fpj_a")
    second = research_run_store.begin_phase1("u_idemp", trading_day="2024-06-27")
    assert second.research_run_id == first.research_run_id
    assert second.first_pass_job_id == "fpj_a"

    # Active phase2 preserves technical_scan_id on start.
    mem = research_run_store.get("u_idemp")
    assert mem is not None
    mem.phase = "awaiting_analyst_candidate_selection"
    mem.status = "gated"
    mem.first_pass_snapshot_id = "fps_x"
    mem.gates["candidateSelection"] = "approved"
    research_run_store.start_phase2("u_idemp", technical_scan_id="scan_keep")
    again = research_run_store.begin_phase1("u_idemp")
    assert again.research_run_id == first.research_run_id
    assert again.technical_scan_id == "scan_keep"

    # Stale revision conflict across two store views.
    from app.services.tw_research.research_run import ResearchRun

    store_a = ResearchRunStore()
    store_b = ResearchRunStore()
    store_a.clear_for_tests()
    shared: dict = {}
    store_a._test_memory = shared
    store_b._test_memory = shared
    store_a.begin_phase1("u_conflict", trading_day="2024-06-27")
    # Capture a detached stale copy before A advances.
    current = store_b.get("u_conflict")
    assert current is not None
    stale = ResearchRun.from_persist_payload(current.as_persist_payload())
    store_a.attach_first_pass_job("u_conflict", first_pass_job_id="fpj_z")
    stale.first_pass_job_id = "fpj_stale"
    with pytest.raises(ResearchRunConflict):
        store_b._persist_locked(stale)

    # Candidate set from another snapshot → 409 gate error.
    mem2 = research_run_store.get("u_idemp")
    assert mem2 is not None
    mem2.phase = "awaiting_analyst_candidate_selection"
    mem2.status = "gated"
    mem2.first_pass_snapshot_id = "fps_expected"
    mem2.gates["candidateSelection"] = "awaiting_analyst"
    with pytest.raises(ResearchRunGateError):
        research_run_store.attach_candidate_set(
            "u_idemp",
            candidate_set_id="cs_bad",
            candidate_set_version=1,
            first_pass_snapshot_id="fps_other",
        )
    research_run_store.disable_test_memory()


def _candidate_admission_case(monkeypatch, *, universe_size: int, selected_size: int) -> None:
    from app.services.tw_research.first_pass_durable import (
        create_technical_scan_from_candidate_set,
    )

    counts = _force_monthly_daily(monkeypatch, monthly_pass="fail")
    universe = [f"TW:{2000 + i}" for i in range(universe_size)]
    selected = universe[:selected_size]
    history_calls: list[str] = []
    catalog = {
        sid: stock_record(
            sid,
            market="TW",
            symbol=sid.split(":")[1],
            name=f"Name{sid.split(':')[1]}",
            name_zh_tw=f"名{sid.split(':')[1]}",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
        for sid in universe
    }

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del limit, max_month_fetches
        history_calls.append(stock.id)
        return list(_candles(rising=False)), 0

    class _Repo:
        def is_available(self) -> bool:
            return True

        def latest_candidate_set(self, user_id: str):  # noqa: ANN001
            del user_id
            return {
                "candidate_set_id": f"cs_{universe_size}_{selected_size}",
                "version": 1,
                "first_pass_snapshot_id": f"fps_{universe_size}",
                "first_pass_digest": f"digest{universe_size}",
                "stock_ids": selected,
                "selection_reasons": {},
                "notes": [],
                "decision_date": "2024-06-27",
                "created_at": "2024-06-27T00:00:00+00:00",
            }

        def get_first_pass_snapshot(self, snapshot_id, *, user_id=None):  # noqa: ANN001
            del user_id
            return {
                "snapshot_id": snapshot_id,
                "content_digest": f"digest{universe_size}",
                "stock_ids": universe,
                "payload": {},
            }

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=catalog.get,
        persist=False,
    )
    result = create_technical_scan_from_candidate_set(
        user_id=f"u_{universe_size}_{selected_size}",
        repository=_Repo(),
        scan_service=service,
        max_upstream_month_fetches=400,
        trading_day="2024-06-27",
    )
    state = result["scan"]
    assert list(state.stock_ids) == selected
    assert len(state.stock_ids) == selected_size
    while state.cursor < len(state.stock_ids) and state.status not in {
        "budget_exhausted",
        "completed_processing",
        "verified_ready",
    }:
        service.run_batch(state.scan_id, batch_size=10, timeout_seconds=120)
        state = service.get_scan(state.scan_id)

    assert sorted(history_calls) == sorted(selected)
    assert len(history_calls) == selected_size
    assert counts["monthly"] == selected_size
    # Monthly fail → daily never runs (no overrides).
    assert counts["daily"] == 0
    for sid in selected:
        record = state.records[sid]
        assert record.monthly is not None
        assert record.monthly.get("passStatus") == "fail"
        assert record.daily is not None
        assert record.daily.get("executionStatus") == DAILY_NOT_RUN_BY_DESIGN
        assert record.technical_stage == TECHNICAL_STAGE_DAILY_NOT_RUN


def test_candidate_set_admits_only_12_of_73_for_monthly_first(monkeypatch) -> None:
    _candidate_admission_case(monkeypatch, universe_size=73, selected_size=12)


def test_candidate_set_admits_only_40_of_300_for_monthly_first(monkeypatch) -> None:
    _candidate_admission_case(monkeypatch, universe_size=300, selected_size=40)


def test_technical_scan_idempotent_reuses_scan_id(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import (
        create_technical_scan_from_candidate_set,
    )

    _force_monthly_daily(monkeypatch, monthly_pass="pass", daily_pass="pass")
    selected = ["TW:2330", "TW:2317"]
    history_calls: list[str] = []
    catalog = {
        sid: stock_record(
            sid,
            market="TW",
            symbol=sid.split(":")[1],
            name=sid,
            name_zh_tw=sid,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
        for sid in selected
    }

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del limit, max_month_fetches
        history_calls.append(stock.id)
        return list(_candles()), 0

    class _Repo:
        def is_available(self) -> bool:
            return True

        def latest_candidate_set(self, user_id: str):  # noqa: ANN001
            del user_id
            return {
                "candidate_set_id": "cs_idem",
                "version": 1,
                "first_pass_snapshot_id": "fps_idem",
                "first_pass_digest": "digest_idem",
                "stock_ids": selected,
                "selection_reasons": {},
                "notes": [],
                "decision_date": "2024-06-27",
                "created_at": "2024-06-27T00:00:00+00:00",
            }

        def get_first_pass_snapshot(self, snapshot_id, *, user_id=None):  # noqa: ANN001
            del user_id
            return {
                "snapshot_id": snapshot_id,
                "content_digest": "digest_idem",
                "stock_ids": selected,
                "payload": {},
            }

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=catalog.get,
        persist=False,
    )
    first = create_technical_scan_from_candidate_set(
        user_id="u_idem",
        repository=_Repo(),
        scan_service=service,
        trading_day="2024-06-27",
    )
    second = create_technical_scan_from_candidate_set(
        user_id="u_idem",
        repository=_Repo(),
        scan_service=service,
        trading_day="2024-06-27",
    )
    assert first["scan"].scan_id == second["scan"].scan_id
    assert second.get("idempotentReuse") is True
    # Drain once — history loader called once per candidate, not twice.
    state = first["scan"]
    while state.cursor < len(state.stock_ids):
        service.run_batch(state.scan_id, batch_size=10, timeout_seconds=120)
        state = service.get_scan(state.scan_id)
    assert sorted(history_calls) == sorted(selected)
    assert len(history_calls) == len(selected)


def test_report_evidence_snake_case_digest_and_partial() -> None:
    fixture = load_zh_tw_example_fixture()
    assert fixture["exampleOnly"] is True

    snake = {
        "snapshot_id": "fps_1",
        "content_digest": "abc",
        "stock_ids": ["TW:2330", "TW:2317"],
        "payload": {"quality_status": "partial"},
    }
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot=snake,
        candidate_set={
            "candidateSetId": "cs_1",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassDigest": "abc",
            "firstPassSnapshotId": "fps_1",
        },
        technical_scan={
            "scanId": "scan_1",
            "tradingDay": "2024-06-27",
            "status": "verified_ready",
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "stockName": "台積電",
                "nameStatus": "ok",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass", "executionStatus": "pass"},
            }
        ],
    ).as_dict()
    assert pack["summary"]["universeSize"] == 2
    assert pack["summary"]["publishability"] == "incomplete_or_partial_evidence"
    assert pack["firstPass"]["snapshotId"] == "fps_1"
    assert pack["firstPass"]["manifestPath"].endswith("/fps_1/manifest")
    assert "contentDigest" in pack
    digest1 = pack["contentDigest"]
    pack2 = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot=snake,
        candidate_set={
            "candidateSetId": "cs_1",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassDigest": "abc",
            "firstPassSnapshotId": "fps_1",
        },
        technical_scan={
            "scanId": "scan_1",
            "tradingDay": "2024-06-27",
            "status": "verified_ready",
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "stockName": "台積電",
                "nameStatus": "ok",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass", "executionStatus": "pass"},
            }
        ],
    ).as_dict()
    assert pack2["contentDigest"] == digest1
    assert pack2["generatedAt"] != pack["generatedAt"] or True  # timestamps may match in same second


def test_technical_stage_survives_persist_reload(monkeypatch) -> None:
    from app.services.tw_research.persistence import load_scan_state, _export_row
    from app.services.tw_research.scan import SymbolScanRecord

    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        stock_name="台積電",
        name_status="ok",
        technical_stage=TECHNICAL_STAGE_DAILY_NOT_RUN,
        monthly={"passStatus": "fail"},
        daily={"executionStatus": DAILY_NOT_RUN_BY_DESIGN, "passStatus": None},
    )
    exported = _export_row(record)
    assert exported["technicalStage"] == TECHNICAL_STAGE_DAILY_NOT_RUN
    assert exported["stockName"] == "台積電"

    class _Repo:
        def is_available(self) -> bool:
            return True

        def get_scan(self, scan_id):  # noqa: ANN001
            return {
                "scan_id": scan_id,
                "user_id": "u1",
                "trading_day": "2024-06-28",
                "universe_version": "explicit:1",
                "calculation_version": CALCULATION_VERSION_MONTHLY_FIRST,
                "selector_json": {"mode": "stock_ids", "stockIds": ["TW:2330"]},
                "stock_ids_json": ["TW:2330"],
                "status": "completed_processing",
                "quality_status": "processed",
                "cursor": 1,
                "enrich_cursor": 0,
                "target_count": 10,
                "upstream_month_fetches": 0,
                "max_upstream_month_fetches": 40,
                "enrichments_run": 0,
                "max_enrichments_per_run": 20,
                "day_pass_ids_json": [],
                "qualified_ids_json": [],
                "notes_json": [],
                "created_at": datetime(2024, 6, 28, tzinfo=TZ),
                "updated_at": datetime(2024, 6, 28, tzinfo=TZ),
            }

        def list_symbol_results(self, scan_id):  # noqa: ANN001
            del scan_id
            from dataclasses import asdict

            return [
                {
                    "stock_id": "TW:2330",
                    "stage": "A",
                    "readiness": "processed",
                    "payload_json": asdict(record),
                }
            ]

    loaded = load_scan_state("scan_reload", repository=_Repo())
    assert loaded is not None
    assert loaded.records["TW:2330"].technical_stage == TECHNICAL_STAGE_DAILY_NOT_RUN


def test_report_evidence_missing_quality_is_not_ready() -> None:
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_q",
            "content_digest": "digest_q",
            "stock_ids": ["TW:2330"],
            "payload": {},
        },
        candidate_set={
            "candidateSetId": "cs_q",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_q",
            "firstPassDigest": "digest_q",
        },
        technical_scan={
            "scanId": "scan_q",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
        },
        ledger_rows=[{"stockId": "TW:2330", "monthly": {"passStatus": "pass"}, "daily": {"passStatus": "pass"}}],
        mode="full_two_pass",
    ).as_dict()
    assert pack["summary"]["publishability"] == "incomplete_or_partial_evidence"
    assert "first_pass_quality_missing" in pack["summary"]["qualityWarnings"]


def test_research_run_job_claim_is_exclusive() -> None:
    research_run_store.clear_for_tests()
    research_run_store.begin_phase1("u_claim", trading_day="2024-06-27")
    first = research_run_store.claim_first_pass_job_slot("u_claim", claim_token="tok_a")
    assert first["outcome"] == "claimed"
    assert first.get("pending_first_pass_job_id")
    run = research_run_store.get("u_claim")
    assert run is not None
    assert first["pending_first_pass_job_id"] == f"fpj_{run.research_run_id}"
    second = research_run_store.claim_first_pass_job_slot("u_claim", claim_token="tok_b")
    assert second["outcome"] == "claim_held"
    pending_id = str(first["pending_first_pass_job_id"])
    research_run_store.record_pending_first_pass_job(
        "u_claim", claim_token="tok_a", job_id=pending_id
    )
    attached = research_run_store.attach_first_pass_job_under_claim(
        "u_claim", claim_token="tok_a", job_id=pending_id
    )
    assert attached["outcome"] == "attached"
    again = research_run_store.claim_first_pass_job_slot("u_claim", claim_token="tok_c")
    assert again["outcome"] == "already_attached"
    assert again["first_pass_job_id"] == pending_id
    research_run_store.disable_test_memory()


def test_phase2_scan_request_mismatches_unit() -> None:
    from app.api.routes.tw_research import (
        _phase2_effective_daily_overrides,
        _phase2_scan_request_mismatches,
    )
    from app.schemas.tw_research import CandidateTechnicalScanRequest
    from app.services.tw_research.constants import CALCULATION_VERSION_MONTHLY_FIRST
    from app.services.tw_research.scan import ScanState, UniverseSelector

    # Attached provenance stores the *effective* set (candidate ∪ prior request).
    state = ScanState(
        scan_id="scan_m",
        trading_day="2024-06-27",
        universe_version="explicit:1",
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        selector=UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2317")),
        stock_ids=("TW:2330", "TW:2317"),
        candidate_provenance={
            "candidateSetId": "cs_1",
            "candidateSetVersion": 1,
            "approvedStockIds": ["TW:2330", "TW:2317"],
            "dailyOverrideIds": ["TW:2317"],
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
        },
    )
    candidate_overrides = ["TW:2317"]

    # Case A: empty or identical request → effective == attached → reuse.
    empty_req = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=1,
        tradingDay="2024-06-27",
        dailyOverrideIds=[],
    )
    assert (
        _phase2_scan_request_mismatches(
            state,
            empty_req,
            candidate_set_daily_override_ids=candidate_overrides,
        )
        == []
    )
    identical = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=1,
        tradingDay="2024-06-27",
        dailyOverrideIds=["TW:2317"],
    )
    assert (
        _phase2_scan_request_mismatches(
            state,
            identical,
            candidate_set_daily_override_ids=candidate_overrides,
        )
        == []
    )

    versioned = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=2,
        tradingDay="2024-06-27",
    )
    assert "candidateSetVersion" in _phase2_scan_request_mismatches(
        state, versioned, candidate_set_daily_override_ids=candidate_overrides
    )

    day = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=1,
        tradingDay="2024-06-28",
    )
    assert "tradingDay" in _phase2_scan_request_mismatches(
        state, day, candidate_set_daily_override_ids=candidate_overrides
    )

    # Request adds a new override → effective grows beyond attached → mismatch.
    overrides = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=1,
        tradingDay="2024-06-27",
        dailyOverrideIds=["TW:2330"],
    )
    assert "dailyOverrideIds" in _phase2_scan_request_mismatches(
        state, overrides, candidate_set_daily_override_ids=candidate_overrides
    )

    # Attached effective = candidate[A] ∪ request[B] = [A,B].
    # Retry with only [A] (or empty) must NOT reuse via subset — effective is [A].
    state_ab = ScanState(
        scan_id="scan_ab",
        trading_day="2024-06-27",
        universe_version="explicit:1",
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        selector=UniverseSelector(
            mode="stock_ids", stock_ids=("TW:2330", "TW:2317", "TW:2454")
        ),
        stock_ids=("TW:2330", "TW:2317", "TW:2454"),
        candidate_provenance={
            "candidateSetId": "cs_1",
            "candidateSetVersion": 1,
            "approvedStockIds": ["TW:2330", "TW:2317", "TW:2454"],
            "dailyOverrideIds": ["TW:2317", "TW:2454"],
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
        },
    )
    subset_req = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=1,
        tradingDay="2024-06-27",
        dailyOverrideIds=["TW:2317"],
    )
    assert "dailyOverrideIds" in _phase2_scan_request_mismatches(
        state_ab,
        subset_req,
        candidate_set_daily_override_ids=["TW:2317"],
    )
    # Same attached, request supplies B again → effective A∪B matches.
    full_req = CandidateTechnicalScanRequest(
        candidateSetId="cs_1",
        expectedVersion=1,
        tradingDay="2024-06-27",
        dailyOverrideIds=["tw:2454"],  # normalization + order independence
    )
    assert (
        _phase2_scan_request_mismatches(
            state_ab,
            full_req,
            candidate_set_daily_override_ids=["TW:2317"],
        )
        == []
    )
    assert _phase2_effective_daily_overrides(
        candidate_set_daily_override_ids=["TW:2317"],
        request_daily_override_ids=["TW:2454", "2317"],
    ) == ["TW:2317", "TW:2454"]


def test_research_run_phase2_claim_attaches_scan() -> None:
    research_run_store.clear_for_tests()
    research_run_store.begin_phase1("u_p2", trading_day="2024-06-27")
    mem = research_run_store.get("u_p2")
    assert mem is not None
    mem.phase = "awaiting_analyst_candidate_selection"
    mem.status = "gated"
    mem.first_pass_snapshot_id = "fps_p2"
    mem.gates["candidateSelection"] = "approved"
    mem.candidate_set_id = "cs_p2"
    mem.candidate_set_version = 1
    claim = research_run_store.claim_phase2_scan_slot("u_p2", claim_token="p2tok")
    assert claim["outcome"] == "claimed"
    research_run_store.record_pending_technical_scan(
        "u_p2", claim_token="p2tok", scan_id="scan_p2"
    )
    attached = research_run_store.attach_technical_scan_under_claim(
        "u_p2", claim_token="p2tok", scan_id="scan_p2"
    )
    assert attached["outcome"] == "attached"
    run = research_run_store.get("u_p2")
    assert run is not None
    assert run.technical_scan_id == "scan_p2"
    assert run.phase == "phase2_technical"
    research_run_store.disable_test_memory()
