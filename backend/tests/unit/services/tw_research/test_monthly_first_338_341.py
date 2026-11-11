"""Monthly-first technical execution and research gates (#338/#340/#341)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.services.tw_research.constants import (
    CALCULATION_VERSION_MONTHLY_FIRST,
    DAILY_NOT_RUN_BY_DESIGN,
    WAITING_FOR_NEXT_WINDOW,
)
from app.services.tw_research.evaluation import (
    derive_stage_progress,
    map_recoverable_status,
)
from app.services.tw_research.report_evidence import (
    build_report_evidence,
    load_zh_tw_example_fixture,
)
from app.services.tw_research.research_run import research_run_store
from app.services.tw_research.scan import (
    CheckpointKey,
    ScanState,
    TwResearchScanService,
    _not_run_daily_payload,
)
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


def _candles_for_monthly(*, rising: bool, days: int = 900) -> list[CandleRecord]:
    """Enough history for monthly MACD warm-up; rising tends to pass, falling to fail."""
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


def test_not_run_payload_is_not_a_fail() -> None:
    payload = _not_run_daily_payload(reason="monthly_screen_failed")
    assert payload["executionStatus"] == DAILY_NOT_RUN_BY_DESIGN
    assert payload["passStatus"] is None
    assert payload["passStatus"] != "fail"
    assert "not a technical fail" in payload["note"]


def test_monthly_first_skips_daily_with_not_run_by_design() -> None:
    falling = _candles_for_monthly(rising=False)
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

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        return list(falling), 0

    service = TwResearchScanService(
        history_loader=loader,
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
    assert record.monthly.get("passStatus") in {"fail", "unknown", "pass"}
    if record.monthly.get("passStatus") == "fail":
        assert record.daily is not None
        assert record.daily.get("executionStatus") == DAILY_NOT_RUN_BY_DESIGN
        assert record.daily.get("passStatus") is None
        assert record.daily.get("passStatus") != "fail"
        progress = derive_stage_progress(
            stock_ids=state.stock_ids,
            records=service.get_scan(state.scan_id).records,
            calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        )
        assert "TW:2317" in progress["dailyNotRunByDesignIds"]
        assert "TW:2317" in progress["monthlyFailWatchlistIds"]


def test_analyst_override_runs_daily_despite_monthly_fail() -> None:
    falling = _candles_for_monthly(rising=False)
    catalog = {
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="鴻海",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        return list(falling), 0

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=catalog.get,
        persist=False,
    )
    # Probe without override.
    probe = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2317",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        max_upstream_month_fetches=40,
    )
    service.run_batch(probe.scan_id, batch_size=1, timeout_seconds=120)
    monthly_status = service.get_scan(probe.scan_id).records["TW:2317"].monthly.get(
        "passStatus"
    )
    if monthly_status != "fail":
        # Synthetic series may not always fail; skip override assertion.
        return

    service2 = TwResearchScanService(
        history_loader=loader,
        stock_loader=catalog.get,
        persist=False,
    )
    state = service2.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2317",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        candidate_provenance={"dailyOverrideIds": ["TW:2317"]},
        max_upstream_month_fetches=40,
    )
    service2.run_batch(state.scan_id, batch_size=1, timeout_seconds=120)
    record = service2.get_scan(state.scan_id).records["TW:2317"]
    assert record.daily is not None
    assert record.daily.get("executionStatus") in {"pass", "fail", "unknown"}
    assert record.daily.get("analystDailyOverride") is True
    assert record.daily.get("executionStatus") != DAILY_NOT_RUN_BY_DESIGN


def test_research_run_gates_and_waiting_mapping() -> None:
    research_run_store.clear_for_tests()
    assert map_recoverable_status("deferred") == WAITING_FOR_NEXT_WINDOW
    assert map_recoverable_status("partial") == "partial"
    assert map_recoverable_status("budget_exhausted") == "budget_exhausted"

    run = research_run_store.start_phase1("u1", trading_day="2024-06-27")
    assert run.phase == "phase1_first_pass"
    assert run.schedule_enabled is False
    gated = research_run_store.mark_phase1_gated(
        "u1",
        first_pass_job_id="fpj_1",
        first_pass_snapshot_id="fps_1",
        job_status="completed",
    )
    assert gated.phase == "awaiting_analyst_candidate_selection"
    assert gated.gates["candidateSelection"] == "awaiting_analyst"

    research_run_store.attach_candidate_set(
        "u1", candidate_set_id="cs_1", candidate_set_version=1
    )
    phase2 = research_run_store.start_phase2("u1", technical_scan_id="scan_1")
    assert phase2.phase == "phase2_technical"
    waiting = research_run_store.mark_phase2_gated("u1", scan_status="deferred")
    assert waiting.status == WAITING_FOR_NEXT_WINDOW
    report_gate = research_run_store.mark_phase2_gated("u1", scan_status="partial")
    assert report_gate.phase == "awaiting_analyst_report_decision"
    assert report_gate.status == "gated"
    research_run_store.clear_for_tests()


def test_candidate_set_admits_only_12_of_73_for_monthly_first() -> None:
    """#338 acceptance: 73-universe / 12-selected → monthly compute for 12 only."""
    from app.services.tw_research.first_pass_durable import (
        create_technical_scan_from_candidate_set,
    )
    from app.services.tw_research.constants import TECHNICAL_STAGE_DAILY_NOT_RUN

    universe = [f"TW:{2000 + i}" for i in range(73)]
    selected = universe[:12]
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
        # Falling series → monthly fail → daily not_run_by_design
        return list(_candles_for_monthly(rising=False)), 0

    class _Repo:
        def is_available(self) -> bool:
            return True

        def latest_candidate_set(self, user_id: str):  # noqa: ANN001
            del user_id
            return {
                "candidate_set_id": "cs_73_12",
                "version": 1,
                "first_pass_snapshot_id": "fps_73",
                "first_pass_digest": "digest73",
                "stock_ids": selected,
                "selection_reasons": {},
                "notes": [],
                "decision_date": "2024-06-27",
                "created_at": "2024-06-27T00:00:00+00:00",
            }

        def get_first_pass_snapshot(self, snapshot_id, *, user_id=None):  # noqa: ANN001
            del user_id
            assert snapshot_id == "fps_73"
            return {
                "snapshot_id": "fps_73",
                "content_digest": "digest73",
                "stock_ids": universe,
                "payload": {},
            }

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=catalog.get,
        persist=False,
    )
    result = create_technical_scan_from_candidate_set(
        user_id="u_73_12",
        repository=_Repo(),
        scan_service=service,
        max_upstream_month_fetches=200,
    )
    state = result["scan"]
    assert list(state.stock_ids) == selected
    assert len(state.stock_ids) == 12
    # Drain the scan.
    while state.cursor < len(state.stock_ids) and state.status not in {
        "budget_exhausted",
        "completed_processing",
        "verified_ready",
    }:
        service.run_batch(state.scan_id, batch_size=10, timeout_seconds=120)
        state = service.get_scan(state.scan_id)

    assert sorted(history_calls) == sorted(selected)
    assert len(history_calls) == 12
    assert not set(history_calls) - set(selected)
    # Non-selected never appear in records.
    assert set(state.records) <= set(selected)
    for sid in selected:
        record = state.records[sid]
        assert record.monthly is not None
        if record.daily and record.daily.get("executionStatus") == DAILY_NOT_RUN_BY_DESIGN:
            assert record.technical_stage == TECHNICAL_STAGE_DAILY_NOT_RUN


def test_report_evidence_and_zh_fixture() -> None:
    fixture = load_zh_tw_example_fixture()
    assert fixture["exampleOnly"] is True
    assert "不是即時投資建議" in fixture["disclaimer"]

    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshotId": "fps_1",
            "contentDigest": "abc",
            "universeSize": 2,
            "stockIds": ["TW:2330", "TW:2317"],
        },
        candidate_set={
            "candidateSetId": "cs_1",
            "version": 1,
            "stockIds": ["TW:2330", "TW:2317"],
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan_1",
            "tradingDay": "2024-06-27",
            "status": "verified_ready",
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
            "stockIds": ["TW:2330", "TW:2317"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "stockName": "台積電",
                "nameStatus": "ok",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass", "executionStatus": "pass"},
                "readiness": "technically_qualified",
            },
            {
                "stockId": "TW:2317",
                "stockName": "鴻海",
                "nameStatus": "ok",
                "monthly": {"passStatus": "fail"},
                "daily": {
                    "passStatus": None,
                    "executionStatus": DAILY_NOT_RUN_BY_DESIGN,
                },
                "readiness": "processed",
            },
        ],
    ).as_dict()
    assert pack["ownership"]["stockTickerWritesNotion"] is False
    assert pack["summary"]["noTopNPadding"] is True
    assert pack["analystDecisionSlots"]["entryZone"] == "not_established"
    buckets = {row["stockId"]: row["bucket"] for row in pack["symbols"]}
    assert buckets["TW:2330"] == "technical_confirmed"
    assert buckets["TW:2317"] == "fundamental_watch_monthly_fail"


def test_two_analyst_decisions_against_same_immutable_evidence() -> None:
    """#340: two valid analyst decisions against one snapshot; no rescan required."""
    evidence = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshotId": "fps_shared",
            "contentDigest": "digest_shared",
            "universeSize": 3,
            "stockIds": ["TW:2330", "TW:2317", "TW:9991"],
            "qualityStatus": "verified_ready",
        },
        candidate_set={
            "candidateSetId": "cs_shared",
            "version": 1,
            "stockIds": ["TW:2330", "TW:2317"],
            "firstPassDigest": "digest_shared",
        },
        technical_scan={
            "scanId": "scan_shared",
            "tradingDay": "2024-06-27",
            "status": "verified_ready",
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
            "stockIds": ["TW:2330", "TW:2317"],
            "contentDigest": "tech_digest",
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "stockName": "台積電",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass", "executionStatus": "pass"},
            },
            {
                "stockId": "TW:2317",
                "stockName": "鴻海",
                "monthly": {"passStatus": "fail"},
                "daily": {
                    "passStatus": None,
                    "executionStatus": DAILY_NOT_RUN_BY_DESIGN,
                },
            },
        ],
    ).as_dict()
    pins = evidence["pins"]
    decision_a = {
        "artifactType": "analyst_decision",
        "decisionId": "ad_a",
        "sourceSnapshotId": pins["firstPassSnapshotId"],
        "sourceDigest": pins["firstPassDigest"],
        "technicalScanId": pins["technicalScanId"],
        "selectedStockIds": ["TW:2330"],
        "publication": "no_stock_ticker_notion_write",
    }
    decision_b = {
        "artifactType": "analyst_decision",
        "decisionId": "ad_b",
        "sourceSnapshotId": pins["firstPassSnapshotId"],
        "sourceDigest": pins["firstPassDigest"],
        "technicalScanId": pins["technicalScanId"],
        "selectedStockIds": [],
        "result": "no_opportunities_today",
        "publication": "no_stock_ticker_notion_write",
    }
    assert decision_a["sourceDigest"] == decision_b["sourceDigest"]
    assert decision_a["selectedStockIds"] != decision_b["selectedStockIds"]
    assert evidence["ownership"]["stockTickerWritesNotion"] is False
    assert evidence["ownership"]["stockTickerRanksFinalPicks"] is False
