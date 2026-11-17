"""Unit tests for Stage-4 technical results mapping (#361)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.init_ids import DEFAULT_USER_ID
from app.main import app
from app.services.tw_research.constants import (
    DAILY_NOT_RUN_BY_DESIGN,
    HISTORY_BUDGET_EXHAUSTED_REASON,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
    TECHNICAL_STAGE_DAILY_NOT_RUN,
    TECHNICAL_STAGE_DAILY_PASS,
)
from app.services.tw_research.research_run import research_run_store
from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
from app.services.tw_research.technical_results import (
    build_technical_results_payload,
    map_symbol_technical_result,
    resolve_technical_results_research_run_id,
)
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record

client = TestClient(app)


def test_map_ok_dual_pass_row() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        stock_name="台積電",
        name_status="ok",
        monthly={
            "asOf": "2024-06",
            "dif": 1.2345,
            "signal": 0.9876,
            "osc": 0.2469,
            "k": 72.15,
            "d": 65.4,
            "barCount": 48,
            "quality": "ok",
            "passStatus": "pass",
            "priceBasis": "unadjusted_close",
            "calculationVersion": "macd12-26-9_kd9-3-3_monthly_first_v1",
            "timeframe": "monthly",
        },
        daily={
            "asOf": "2024-06-28",
            "dif": 2.5,
            "signal": 1.1,
            "osc": 1.4,
            "k": 80.0,
            "d": 70.0,
            "barCount": 120,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "daily",
        },
    )
    row = map_symbol_technical_result(record)
    assert row["stockId"] == "TW:2330"
    assert row["name"] == "台積電"
    assert row["overallStageResult"] == "pass"
    assert row["monthly"]["dif"] == 1.2345
    assert row["daily"]["asOf"] == "2024-06-28"
    assert row["insufficientHistory"] is None
    assert row["dailyNotRunReason"] is None


def test_map_insufficient_history() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2454",
        stage="rejected",
        readiness="rejected",
        technical_stage="monthly_unknown",
        stock_name="聯發科",
        monthly={
            "asOf": None,
            "dif": None,
            "signal": None,
            "osc": None,
            "k": None,
            "d": None,
            "barCount": 12,
            "quality": "insufficient_history",
            "passStatus": "unknown",
            "timeframe": "monthly",
        },
        daily=None,
        coverage={
            "monthly": {"reason": "insufficient_history", "barCount": 12},
        },
    )
    row = map_symbol_technical_result(record)
    assert row["overallStageResult"] == "insufficient_history"
    assert row["insufficientHistory"] == {
        "reason": "insufficient_history",
        "requiredBars": MIN_MONTHLY_BARS_FOR_MACD_KD,
        "availableBars": 12,
        "timeframe": "monthly",
    }
    assert row["daily"] is None


def test_map_insufficient_history_unknown_available_bars() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2303",
        stage="rejected",
        readiness="rejected",
        technical_stage="monthly_unknown",
        monthly={
            "quality": "insufficient_history",
            "passStatus": "unknown",
            "timeframe": "monthly",
        },
        daily=None,
    )
    row = map_symbol_technical_result(record)
    assert row["overallStageResult"] == "insufficient_history"
    assert row["insufficientHistory"]["availableBars"] is None
    assert row["insufficientHistory"]["requiredBars"] == MIN_MONTHLY_BARS_FOR_MACD_KD


def test_map_daily_not_run_by_design() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2303",
        stage="A",
        readiness="processed",
        technical_stage=TECHNICAL_STAGE_DAILY_NOT_RUN,
        stock_name="聯電",
        monthly={
            "asOf": "2024-05",
            "dif": -0.5,
            "signal": -0.2,
            "osc": -0.3,
            "k": 20.0,
            "d": 25.0,
            "barCount": 40,
            "quality": "ok",
            "passStatus": "fail",
            "timeframe": "monthly",
        },
        daily={
            "asOf": None,
            "dif": None,
            "signal": None,
            "osc": None,
            "k": None,
            "d": None,
            "passStatus": None,
            "executionStatus": DAILY_NOT_RUN_BY_DESIGN,
            "quality": "unavailable",
            "barCount": 0,
            "reason": "monthly_gate_failed",
            "timeframe": "daily",
        },
    )
    row = map_symbol_technical_result(record)
    assert row["overallStageResult"] == "daily_not_run"
    assert row["daily"] is None
    assert row["dailyNotRunReason"] == "monthly_gate_failed"
    assert row["monthly"]["passStatus"] == "fail"


def test_map_symbol_error_is_error_not_pending() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2303",
        stage="A",
        readiness="processed",
        rejection_reason="symbol_error:TimeoutError",
        technical_stage=None,
        monthly=None,
        daily=None,
    )
    row = map_symbol_technical_result(record)
    assert row["overallStageResult"] == "error"
    assert row["rejectionReason"] == "symbol_error:TimeoutError"
    assert row["insufficientHistory"] is None


def test_budget_exhausted_waiting_while_phase2_active() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="rejected",
        readiness="rejected",
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        technical_stage="deferred",
        monthly=None,
        daily=None,
    )
    row = map_symbol_technical_result(
        record,
        phase2_execution={"state": "waiting_for_budget_window", "blocker": None},
    )
    assert row["overallStageResult"] == "waiting_for_history"
    assert row["insufficientHistory"] is None


def test_budget_exhausted_insufficient_when_phase2_completed() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="rejected",
        readiness="rejected",
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        technical_stage="deferred",
        monthly=None,
        daily=None,
    )
    row = map_symbol_technical_result(
        record, phase2_execution={"state": "completed"}
    )
    assert row["overallStageResult"] == "insufficient_history"
    assert row["insufficientHistory"]["reason"] == HISTORY_BUDGET_EXHAUSTED_REASON
    assert row["insufficientHistory"]["availableBars"] is None


def test_budget_exhausted_insufficient_when_daily_cap_reached() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="rejected",
        readiness="rejected",
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        monthly=None,
        daily=None,
    )
    row = map_symbol_technical_result(
        record,
        phase2_execution={
            "state": "waiting_for_budget_window",
            "blocker": "phase2_budget_cap_reached",
        },
    )
    assert row["overallStageResult"] == "insufficient_history"
    assert row["insufficientHistory"]["reason"] == HISTORY_BUDGET_EXHAUSTED_REASON


def test_counts_prefer_rows_over_phase2_execution() -> None:
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2454": stock_record(
            "TW:2454",
            market="TW",
            symbol="2454",
            name="聯發科",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
    )
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="rejected",
        readiness="rejected",
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
    )
    state.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="rejected",
        readiness="rejected",
        monthly={
            "quality": "insufficient_history",
            "barCount": 10,
            "passStatus": "unknown",
            "timeframe": "monthly",
        },
    )
    class _Run:
        research_run_id = "rr_counts"

    payload = build_technical_results_payload(
        run=_Run(),
        state=state,
        phase2_execution={
            "state": "actively_analyzing",
            "analyzedCount": 99,
            "insufficientHistoryCount": 99,
        },
    )
    # Waiting / insufficient are separate — not successful indicator analysis.
    assert payload["analyzedCount"] == 0
    assert payload["waitingForHistoryCount"] == 1
    assert payload["insufficientHistoryCount"] == 1
    assert payload["errorCount"] == 0


def test_technical_results_endpoint_does_not_bump_revision(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )
    research_run_store.begin_phase1(DEFAULT_USER_ID, trading_day="2024-06-28")
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked(DEFAULT_USER_ID)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = state.scan_id
        research_run_store._persist_locked(locked)

    before = research_run_store.get(DEFAULT_USER_ID)
    assert before is not None
    revision_before = int(before.revision)

    # Spy: get must be used (read-only); reconcile would bump revision.
    reconcile_calls = {"n": 0}
    real_reconcile = research_run_store.reconcile

    def counting_reconcile(user_id: str):
        reconcile_calls["n"] += 1
        return real_reconcile(user_id)

    monkeypatch.setattr(research_run_store, "reconcile", counting_reconcile)

    first = client.get("/api/tw-research/research-runs/technical-results")
    assert first.status_code == 200, first.text
    second = client.get("/api/tw-research/research-runs/technical-results")
    assert second.status_code == 200, second.text
    assert reconcile_calls["n"] == 0

    after = research_run_store.get(DEFAULT_USER_ID)
    assert after is not None
    assert int(after.revision) == revision_before
    research_run_store.disable_test_memory()


def test_pass_evaluation_components_from_persisted_snapshot() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        monthly={
            "asOf": "2024-05-31",
            "dif": 1.2,
            "signal": 0.5,
            "osc": 0.7,
            "k": 72.0,
            "d": 65.0,
            "barCount": 48,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "monthly",
        },
        daily={
            "asOf": "2024-06-27",
            "dif": -0.1,
            "signal": 0.2,
            "osc": -0.3,
            "k": 40.0,
            "d": 45.0,
            "barCount": 120,
            "quality": "ok",
            "passStatus": "fail",
            "timeframe": "daily",
        },
    )
    row = map_symbol_technical_result(record)
    monthly_eval = row["monthlyPassEvaluation"]
    assert monthly_eval["passStatus"] == "pass"
    by_name = {c["component"]: c for c in monthly_eval["components"]}
    assert by_name["dif"]["passed"] is True
    assert by_name["osc"]["passed"] is True
    assert by_name["k"]["passed"] is True
    assert by_name["d"]["passed"] is True
    daily_eval = row["dailyPassEvaluation"]
    assert daily_eval["passStatus"] == "fail"
    daily_by = {c["component"]: c for c in daily_eval["components"]}
    assert daily_by["dif"]["passed"] is False
    assert daily_by["osc"]["passed"] is False


def test_historical_scan_id_query_returns_requested_scan(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2454": stock_record(
            "TW:2454",
            market="TW",
            symbol="2454",
            name="聯發科",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    current = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    historical = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2454",)),
        trading_day="2024-06-27",
        user_id=DEFAULT_USER_ID,
    )
    historical.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        monthly={
            "asOf": "2024-05-31",
            "dif": 9.99,
            "signal": 1.0,
            "osc": 8.99,
            "k": 80.0,
            "d": 70.0,
            "barCount": 40,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "monthly",
        },
        daily={
            "asOf": "2024-06-26",
            "dif": 3.0,
            "signal": 1.0,
            "osc": 2.0,
            "k": 60.0,
            "d": 55.0,
            "barCount": 100,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "daily",
        },
    )
    current.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="rejected",
        readiness="rejected",
        monthly={
            "asOf": "2024-05-31",
            "dif": 0.1,
            "signal": 0.05,
            "osc": 0.05,
            "k": 51.0,
            "d": 51.0,
            "barCount": 40,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "monthly",
        },
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )
    research_run_store.begin_phase1(DEFAULT_USER_ID, trading_day="2024-06-28")
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked(DEFAULT_USER_ID)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = current.scan_id
        research_run_store._persist_locked(locked)

    hist = client.get(
        f"/api/tw-research/research-runs/technical-results?scanId={historical.scan_id}"
    )
    assert hist.status_code == 200, hist.text
    body = hist.json()
    assert body["technicalScanId"] == historical.scan_id
    assert body["viewingHistorical"] is True
    assert body["isCurrentRunScan"] is False
    assert body["phase2Execution"] is None
    assert body["candidates"][0]["stockId"] == "TW:2454"
    assert body["candidates"][0]["monthly"]["dif"] == 9.99

    cur = client.get("/api/tw-research/research-runs/technical-results")
    assert cur.status_code == 200, cur.text
    cur_body = cur.json()
    assert cur_body["technicalScanId"] == current.scan_id
    assert cur_body["viewingHistorical"] is False
    assert cur_body["isCurrentRunScan"] is True
    assert cur_body["candidates"][0]["stockId"] == "TW:2330"
    assert cur_body["candidates"][0]["monthly"]["dif"] == 0.1

    # Historical lookup must not mutate the run attachment.
    run_after = research_run_store.get(DEFAULT_USER_ID)
    assert run_after is not None
    assert run_after.technical_scan_id == current.scan_id
    research_run_store.disable_test_memory()


def test_foreign_historical_scan_is_not_found(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    foreign = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id="other-user",
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )
    research_run_store.begin_phase1(DEFAULT_USER_ID, trading_day="2024-06-28")
    response = client.get(
        f"/api/tw-research/research-runs/technical-results?scanId={foreign.scan_id}"
    )
    # Same 404 as missing — do not leak scan existence across users.
    assert response.status_code == 404
    assert response.json()["detail"] == "technical_scan_not_found"
    research_run_store.disable_test_memory()


def test_ownerless_historical_scan_is_not_found(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    ownerless = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=None,
    )
    assert getattr(ownerless, "user_id", None) in {None, ""}
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )
    response = client.get(
        f"/api/tw-research/research-runs/technical-results?scanId={ownerless.scan_id}"
    )
    assert response.status_code == 404
    research_run_store.disable_test_memory()


def test_historical_scan_without_active_run(monkeypatch) -> None:
    research_run_store.clear_for_tests()
    catalog = {
        "TW:2454": stock_record(
            "TW:2454",
            market="TW",
            symbol="2454",
            name="聯發科",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    historical = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2454",)),
        trading_day="2024-06-20",
        user_id=DEFAULT_USER_ID,
    )
    historical.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        monthly={
            "asOf": "2024-05-31",
            "dif": 4.44,
            "signal": 1.0,
            "osc": 3.44,
            "k": 70.0,
            "d": 60.0,
            "barCount": 40,
            "quality": "ok",
            "passStatus": "pass",
            "timeframe": "monthly",
        },
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )
    assert research_run_store.get(DEFAULT_USER_ID) is None
    response = client.get(
        f"/api/tw-research/research-runs/technical-results?scanId={historical.scan_id}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["technicalScanId"] == historical.scan_id
    assert body["viewingHistorical"] is True
    assert body["candidates"][0]["monthly"]["dif"] == 4.44
    research_run_store.disable_test_memory()


def test_count_buckets_are_non_overlapping() -> None:
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2454": stock_record(
            "TW:2454",
            market="TW",
            symbol="2454",
            name="聯發科",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2303": stock_record(
            "TW:2303",
            market="TW",
            symbol="2303",
            name="聯電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="鴻海",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2330", "TW:2454", "TW:2303", "TW:2317"),
        ),
        trading_day="2024-06-28",
    )
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        monthly={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "signal": 0.5,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 40,
            "timeframe": "monthly",
        },
        daily={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "signal": 0.5,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 100,
            "timeframe": "daily",
        },
    )
    state.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="rejected",
        readiness="processed",
        rejection_reason="symbol_error:TimeoutError",
    )
    state.records["TW:2303"] = SymbolScanRecord(
        stock_id="TW:2303",
        stage="rejected",
        readiness="rejected",
        technical_stage="deferred",
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
    )
    # TW:2317 left pending (no record)

    class _Run:
        research_run_id = "rr_buckets"

    payload = build_technical_results_payload(
        run=_Run(),
        state=state,
        phase2_execution={"state": "actively_analyzing"},
    )
    assert payload["verifiedCount"] == 1
    assert payload["calculatedCount"] == 1
    assert payload["analyzedCount"] == 1
    assert payload["dailySkippedCount"] == 0
    assert payload["errorCount"] == 1
    assert payload["waitingForHistoryCount"] == 1
    assert payload["pendingCount"] == 1
    assert (
        payload["calculatedCount"]
        + payload["dailySkippedCount"]
        + payload["errorCount"]
        + payload["waitingForHistoryCount"]
        + payload["insufficientHistoryCount"]
        + payload["deferredCount"]
        + payload["unknownCount"]
        + payload["pendingCount"]
        == payload["universeSize"]
    )


def test_tw_default_source_provider_projected_as_unavailable() -> None:
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        monthly={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "signal": 0.5,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 40,
            "sourceProvider": "tw_default",
            "firstBarAsOf": "2020-01-01",
            "lastBarAsOf": "2024-05-31",
            "timeframe": "monthly",
        },
        daily={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "signal": 0.5,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 100,
            "sourceProvider": "twse",
            "historyCacheLocation": "postgresql_daily_price_bars",
            "historyOrigin": "postgresql_cache",
            "firstBarAsOf": "2023-01-01",
            "lastBarAsOf": "2024-06-28",
            "timeframe": "daily",
        },
    )
    row = map_symbol_technical_result(record)
    assert row["monthly"]["sourceProvider"] is None
    assert row["monthly"]["sourceProviderStatus"] == "unavailable"
    assert row["monthly"]["historyRangeStatus"] == "available"
    assert row["daily"]["sourceProvider"] == "twse"
    assert row["daily"]["historyCacheLocation"] == "postgresql_daily_price_bars"
    assert row["daily"]["historyOrigin"] == "postgresql_cache"
    assert row["daily"]["sourceProviderStatus"] == "available"


def test_daily_not_run_excluded_from_calculated_and_verified() -> None:
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2303": stock_record(
            "TW:2303",
            market="TW",
            symbol="2303",
            name="聯電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2454": stock_record(
            "TW:2454",
            market="TW",
            symbol="2454",
            name="聯發科",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2330", "TW:2303", "TW:2454"),
        ),
        trading_day="2024-06-28",
    )
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        technical_stage=TECHNICAL_STAGE_DAILY_PASS,
        monthly={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 40,
            "timeframe": "monthly",
        },
        daily={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 100,
            "timeframe": "daily",
        },
    )
    state.records["TW:2303"] = SymbolScanRecord(
        stock_id="TW:2303",
        stage="A",
        readiness="processed",
        technical_stage=TECHNICAL_STAGE_DAILY_NOT_RUN,
        monthly={
            "quality": "ok",
            "passStatus": "fail",
            "dif": -1.0,
            "osc": -0.5,
            "k": 20.0,
            "d": 25.0,
            "barCount": 40,
            "timeframe": "monthly",
        },
        daily={
            "executionStatus": DAILY_NOT_RUN_BY_DESIGN,
            "reason": "monthly_gate_failed",
            "quality": "unavailable",
            "barCount": 0,
            "timeframe": "daily",
        },
    )
    state.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="B",
        readiness="processed",
        technical_stage="daily_fail",
        monthly={
            "quality": "ok",
            "passStatus": "pass",
            "dif": 1.0,
            "osc": 0.5,
            "k": 60.0,
            "d": 55.0,
            "barCount": 40,
            "timeframe": "monthly",
        },
        daily={
            "quality": "ok",
            "passStatus": "fail",
            "dif": -0.2,
            "osc": -0.1,
            "k": 40.0,
            "d": 45.0,
            "barCount": 100,
            "timeframe": "daily",
        },
    )

    class _Run:
        research_run_id = "rr_skip"

    payload = build_technical_results_payload(run=_Run(), state=state, phase2_execution=None)
    assert payload["verifiedCount"] == 2  # pass + fail
    assert payload["calculatedCount"] == 2
    assert payload["dailySkippedCount"] == 1
    assert payload["analyzedCount"] == payload["calculatedCount"]
    assert (
        payload["calculatedCount"]
        + payload["dailySkippedCount"]
        + payload["errorCount"]
        + payload["waitingForHistoryCount"]
        + payload["insufficientHistoryCount"]
        + payload["deferredCount"]
        + payload["unknownCount"]
        + payload["pendingCount"]
        == payload["universeSize"]
    )
    by_id = {row["stockId"]: row for row in payload["candidates"]}
    assert by_id["TW:2330"]["overallStageResult"] == "pass"
    assert by_id["TW:2454"]["overallStageResult"] == "fail"
    assert by_id["TW:2303"]["overallStageResult"] == "daily_not_run"


def _empty_state(*, provenance: dict | None = None):
    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":", 1)[1],
            name=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id="u_run_id",
        candidate_provenance=provenance,
    )
    return state


def test_current_run_scan_uses_active_research_run_id() -> None:
    class _Run:
        research_run_id = "rr_current"

    state = _empty_state(provenance={"candidateSetId": "cs_1"})
    assert (
        resolve_technical_results_research_run_id(
            run=_Run(), state=state, is_current_run_scan=True
        )
        == "rr_current"
    )
    payload = build_technical_results_payload(
        run=_Run(),
        state=state,
        phase2_execution=None,
        is_current_run_scan=True,
        viewing_historical=False,
    )
    assert payload["researchRunId"] == "rr_current"
    assert payload["technicalScanId"] == state.scan_id


def test_historical_scan_uses_durable_lineage_not_active_run() -> None:
    class _Run:
        research_run_id = "rr_active_now"

    state = _empty_state(
        provenance={
            "candidateSetId": "cs_hist",
            "researchRunId": "rr_historical_owner",
        }
    )
    assert (
        resolve_technical_results_research_run_id(
            run=_Run(), state=state, is_current_run_scan=False
        )
        == "rr_historical_owner"
    )
    payload = build_technical_results_payload(
        run=_Run(),
        state=state,
        phase2_execution={"state": "actively_analyzing"},
        is_current_run_scan=False,
        viewing_historical=True,
    )
    assert payload["researchRunId"] == "rr_historical_owner"
    assert payload["viewingHistorical"] is True
    assert payload["isCurrentRunScan"] is False
    assert payload["phase2Execution"] is None


def test_historical_scan_without_lineage_returns_null_run_id() -> None:
    class _Run:
        research_run_id = "rr_active_now"

    state = _empty_state(provenance={"candidateSetId": "cs_orphan"})
    assert (
        resolve_technical_results_research_run_id(
            run=_Run(), state=state, is_current_run_scan=False
        )
        is None
    )
    payload = build_technical_results_payload(
        run=_Run(),
        state=state,
        phase2_execution=None,
        is_current_run_scan=False,
        viewing_historical=True,
    )
    assert payload["researchRunId"] is None


def test_historical_with_no_current_run_still_uses_lineage() -> None:
    state = _empty_state(provenance={"researchRunId": "rr_only_lineage"})
    payload = build_technical_results_payload(
        run=None,
        state=state,
        phase2_execution=None,
        is_current_run_scan=False,
        viewing_historical=True,
    )
    assert payload["researchRunId"] == "rr_only_lineage"


def test_no_current_run_and_no_lineage_is_null() -> None:
    state = _empty_state()
    payload = build_technical_results_payload(
        run=None,
        state=state,
        phase2_execution=None,
        is_current_run_scan=False,
        viewing_historical=True,
    )
    assert payload["researchRunId"] is None
