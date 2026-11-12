"""HTTP surface for Taiwan research routes (#310–#312)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_contract_endpoint_is_industry_agnostic() -> None:
    response = client.get("/api/tw-research/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["industryAgnostic"] is True
    assert payload["starterPresetIsOptional"] is True
    assert payload["noHardUniverseCap"] is True
    assert payload["fieldMap"]


def test_get_scan_does_not_create_state(monkeypatch) -> None:
    from app.services.tw_research.scan import TwResearchScanService

    # Memory-only service: missing IDs are 404. A broken durable store is 503 (#327).
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        TwResearchScanService(persist=False),
    )
    response = client.get("/api/tw-research/scans/does-not-exist")
    assert response.status_code == 404


def test_create_and_get_scan_round_trip_with_explicit_ids(monkeypatch) -> None:
    from app.services.tw_research import scan as scan_module
    from tests.support import stock_record

    catalog = {
        "TW:2727": stock_record(
            "TW:2727",
            market="TW",
            symbol="2727",
            name="王品",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }

    service = scan_module.TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        service,
    )

    created = client.post(
        "/api/tw-research/scans",
        json={
            "universe": {"mode": "stock_ids", "stockIds": ["TW:2727"]},
            "targetCount": 10,
            "tradingDay": "2024-06-28",
        },
    )
    assert created.status_code == 200
    scan_id = created.json()["scan"]["scanId"]

    fetched = client.get(f"/api/tw-research/scans/{scan_id}")
    assert fetched.status_code == 200
    assert fetched.json()["scan"]["scanId"] == scan_id
    assert fetched.json()["scan"]["checkpointsEphemeral"] is False
    assert fetched.json()["scan"]["checkpointsDurable"] is True
    assert fetched.json()["scan"]["status"] == "queued"


def test_resolve_universe_endpoint_accepts_out_of_preset_ids(monkeypatch) -> None:
    from tests.support import stock_record

    catalog = {
        "TW:2727": stock_record(
            "TW:2727",
            market="TW",
            symbol="2727",
            name="王品",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2618": stock_record(
            "TW:2618",
            market="TW",
            symbol="2618",
            name="長榮航",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    monkeypatch.setattr(
        "app.services.tw_research.universe.quote_service.get_catalog_stock",
        lambda stock_id: catalog.get(stock_id),
    )
    response = client.post(
        "/api/tw-research/universe/resolve",
        json={"mode": "stock_ids", "stockIds": ["TW:2727", "TW:2618"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["uniqueCount"] == 2
    assert payload["stockIds"] == ["TW:2727", "TW:2618"]


def test_feasibility_matrix_endpoint() -> None:
    response = client.get("/api/tw-research/feasibility-matrix")
    assert response.status_code == 200
    payload = response.json()
    assert "matrix" in payload
    assert isinstance(payload["matrix"], list)
    assert len(payload["matrix"]) >= 10


def test_first_pass_batch_dedupes_before_batch_limit(monkeypatch) -> None:
    from app.services.tw_research import first_pass as first_pass_mod
    from tests.support import stock_record

    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        name_zh_tw="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    calls = {"n": 0}

    def fake_collect(stock_ids, **kwargs):
        calls["n"] += 1
        assert list(stock_ids) == ["TW:2330", "TW:2317"]
        result = first_pass_mod.FirstPassBatchResult(
            first_pass_version="first-pass-v1",
            universe_version="explicit:2",
            requested_count=len(stock_ids),
            collected_count=len(stock_ids),
            deduped_count=len(stock_ids),
            records=[
                first_pass_mod.FirstPassSymbolRecord(
                    stock_id=sid,
                    stock_name="x",
                    name_status="ok",
                    name_source="catalog",
                    exchange="TWSE",
                    market="TW",
                    sector_theme_keys=[],
                )
                for sid in stock_ids
            ],
            coverage_summary={},
            notes=[],
        )
        return result

    monkeypatch.setattr(
        "app.api.routes.tw_research.collect_first_pass_batch",
        fake_collect,
    )
    monkeypatch.setattr(
        "app.services.tw_research.universe.quote_service.get_catalog_stock",
        lambda sid: stock if sid.startswith("TW:") else None,
    )

    # 25 duplicate-heavy IDs would underfill if batchLimit applied before dedupe.
    ids = ["TW:2330", "2330", "TW:2330", "TW:2317"] * 6 + ["TW:2330"]
    response = client.post(
        "/api/tw-research/first-pass/batch",
        json={"stockIds": ids, "batchLimit": 2, "persist": False},
    )
    assert response.status_code == 200
    payload = response.json()["result"]
    assert payload["dedupedCount"] == 2
    assert payload["durable"] is False
    assert calls["n"] == 1


def test_membership_fail_closed_without_db(monkeypatch) -> None:
    from app.services.tw_research.fundamentals_watchlist import ResearchMembershipStore

    store = ResearchMembershipStore(persist=True)
    monkeypatch.setattr(
        "app.api.routes.tw_research.research_membership_store",
        store,
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.is_available",
        lambda: False,
    )
    monkeypatch.setattr(store, "_repository", type("R", (), {"is_available": lambda self: False})())
    response = client.get("/api/tw-research/membership")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "durable_store_unavailable"


def test_membership_save_conflict_and_round_trip(monkeypatch) -> None:
    from app.services.tw_research.fundamentals_watchlist import ResearchMembershipStore
    from tests.support import stock_record

    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="TSMC",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    store = ResearchMembershipStore(persist=False, stock_resolver=catalog.get)
    monkeypatch.setattr(
        "app.api.routes.tw_research.research_membership_store",
        store,
    )
    ok = client.put(
        "/api/tw-research/membership",
        json={
            "expectedVersion": 0,
            "extraStockIds": ["TW:2330", "2330"],
            "computeSubsetIds": ["TW:2330"],
        },
    )
    assert ok.status_code == 200
    assert ok.json()["membership"]["version"] == 1
    assert ok.json()["membership"]["durable"] is False

    conflict = client.put(
        "/api/tw-research/membership",
        json={"expectedVersion": 0, "extraStockIds": ["TW:2330"]},
    )
    assert conflict.status_code == 409


def test_relative_price_basis_endpoint(monkeypatch) -> None:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.models.quote import CandleRecord
    from tests.support import stock_record

    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    tz = ZoneInfo("Asia/Taipei")
    start = datetime(2024, 1, 2, 13, 30, tzinfo=tz)
    candles = []
    for index in range(80):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        close = 100.0 - 0.3 * index
        candles.append(
            CandleRecord(
                time=day.isoformat(),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000,
            )
        )

    monkeypatch.setattr(
        "app.api.routes.tw_research.quote_service.get_catalog_stock",
        lambda sid: stock if sid == "TW:2330" else None,
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.daily_price_history_service.peek_daily_history",
        lambda *_a, **_k: (candles, 0),
    )
    response = client.post(
        "/api/tw-research/relative-price-basis",
        json={"stockId": "TW:2330", "officialPe": 12.0},
    )
    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence["category"] in {
        "low_relative_price_basis",
        "mid_relative_price_basis",
        "high_relative_price_basis",
        "insufficient_data",
    }


def test_report_evidence_returns_all_40_symbol_rows_with_names(monkeypatch) -> None:
    """Persisted 40-candidate scan → 200 with all rows (no chunk-0 truncation)."""
    from app.db.init_ids import DEFAULT_USER_ID
    from app.services.tw_research.constants import (
        CALCULATION_VERSION_MONTHLY_FIRST,
        TECHNICAL_STAGE_DAILY_PASS,
    )
    from app.services.tw_research.scan import (
        SymbolScanRecord,
        TwResearchScanService,
        UniverseSelector,
    )
    from tests.support import stock_record

    stock_ids = tuple(f"TW:{2000 + i}" for i in range(40))
    catalog = {
        sid: stock_record(
            sid,
            market="TW",
            symbol=sid.split(":")[1],
            name=f"公司{sid.split(':')[1]}",
            name_zh_tw=f"公司{sid.split(':')[1]}",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
        for sid in stock_ids
    }
    service = TwResearchScanService(
        history_loader=lambda *a, **k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=stock_ids),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        trading_day="2024-06-28",
        candidate_provenance={
            "candidateSetId": "cs_40",
            "candidateSetVersion": 1,
            "firstPassSnapshotId": "fps_40",
            "firstPassDigest": "digest_40",
            "dailyOverrideIds": [],
        },
    )
    state.user_id = DEFAULT_USER_ID
    state.status = "completed_processing"
    for sid in stock_ids:
        state.records[sid] = SymbolScanRecord(
            stock_id=sid,
            stage="B",
            readiness="processed",
            technical_stage=TECHNICAL_STAGE_DAILY_PASS,
            stock_name=f"公司{sid.split(':')[1]}",
            name_status="ok",
            monthly={"passStatus": "pass", "quality": "ok"},
            daily={"passStatus": "pass", "quality": "ok"},
        )
    state.cursor = len(stock_ids)
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        service,
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.get_candidate_set",
        lambda **kwargs: {
            "candidateSetId": "cs_40",
            "version": 1,
            "firstPassSnapshotId": "fps_40",
            "firstPassDigest": "digest_40",
            "stockIds": list(stock_ids),
        },
        raising=False,
    )
    # Route imports get_candidate_set inside the handler from first_pass_durable.
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_durable.get_candidate_set",
        lambda **kwargs: {
            "candidateSetId": "cs_40",
            "version": 1,
            "firstPassSnapshotId": "fps_40",
            "firstPassDigest": "digest_40",
            "stockIds": list(stock_ids),
        },
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.get_first_pass_snapshot",
        lambda snapshot_id, user_id=None: {
            "snapshot_id": snapshot_id,
            "content_digest": "digest_40",
            "stock_ids": list(stock_ids),
            "payload": {"qualityStatus": "verified_ready"},
        },
    )

    response = client.post(
        "/api/tw-research/report-evidence",
        json={
            "scanId": state.scan_id,
            "firstPassSnapshotId": "fps_40",
            "tradingDay": "2024-06-28",
        },
    )
    assert response.status_code == 200, response.text
    evidence = response.json()["evidence"]
    symbols = evidence["symbols"]
    assert len(symbols) == 40
    assert all(row.get("stockName") for row in symbols)
    assert all(row.get("nameStatus") == "ok" for row in symbols)
    assert all(row.get("technicalStage") for row in symbols)
    assert evidence["firstPass"]["snapshotId"] == "fps_40"
    assert evidence["contentDigest"]


def test_report_evidence_mismatched_candidate_snapshot_returns_409(monkeypatch) -> None:
    from app.db.init_ids import DEFAULT_USER_ID
    from app.services.tw_research.constants import CALCULATION_VERSION_MONTHLY_FIRST
    from app.services.tw_research.scan import (
        SymbolScanRecord,
        TwResearchScanService,
        UniverseSelector,
    )
    from tests.support import stock_record

    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            name_zh_tw="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    service = TwResearchScanService(
        history_loader=lambda *a, **k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        trading_day="2024-06-28",
        candidate_provenance={
            "candidateSetId": "cs_mismatch",
            "candidateSetVersion": 1,
            "firstPassSnapshotId": "fps_scan",
            "firstPassDigest": "digest_scan",
            "dailyOverrideIds": [],
        },
    )
    state.user_id = DEFAULT_USER_ID
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        stock_name="台積電",
        name_status="ok",
        monthly={"passStatus": "fail", "quality": "ok"},
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        service,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_durable.get_candidate_set",
        lambda **kwargs: {
            "candidateSetId": "cs_mismatch",
            "version": 1,
            "firstPassSnapshotId": "fps_other",
            "firstPassDigest": "digest_other",
            "stockIds": ["TW:2330"],
        },
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.get_first_pass_snapshot",
        lambda snapshot_id, user_id=None: {
            "snapshot_id": snapshot_id,
            "content_digest": "digest_scan",
            "stock_ids": ["TW:2330"],
            "payload": {"qualityStatus": "verified_ready"},
        },
    )

    response = client.post(
        "/api/tw-research/report-evidence",
        json={
            "scanId": state.scan_id,
            "firstPassSnapshotId": "fps_scan",
            "tradingDay": "2024-06-28",
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "evidence_snapshot_mismatch"


def test_research_run_start_idempotent_single_job(monkeypatch) -> None:
    from app.services.tw_research.research_run import research_run_store

    research_run_store.clear_for_tests()
    jobs: list[str] = []

    def fake_create_job(**kwargs):  # noqa: ANN003
        job_id = str(kwargs.get("job_id") or f"fpj_{len(jobs) + 1}")
        jobs.append(job_id)
        return {"job_id": job_id, "status": "queued"}

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.create_job",
        fake_create_job,
    )
    first = client.post(
        "/api/tw-research/research-runs/start",
        json={"phase": "phase1_first_pass", "tradingDay": "2024-06-27"},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/tw-research/research-runs/start",
        json={"phase": "phase1_first_pass", "tradingDay": "2024-06-27"},
    )
    assert second.status_code == 200, second.text
    assert first.json()["researchRun"]["researchRunId"] == second.json()["researchRun"][
        "researchRunId"
    ]
    assert len(jobs) == 1
    run_id = first.json()["researchRun"]["researchRunId"]
    assert jobs[0] == f"fpj_{run_id}"
    assert second.json()["researchRun"]["firstPassJobId"] == jobs[0]
    research_run_store.disable_test_memory()


def test_phase2_already_attached_rejects_semantic_mismatch(monkeypatch) -> None:
    from app.db.init_ids import DEFAULT_USER_ID
    from app.services.tw_research.constants import CALCULATION_VERSION_MONTHLY_FIRST
    from app.services.tw_research.research_run import research_run_store
    from app.services.tw_research.scan import TwResearchScanService, UniverseSelector
    from tests.support import stock_record

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
        history_loader=lambda *a, **k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        trading_day="2024-06-27",
        candidate_provenance={
            "candidateSetId": "cs_a",
            "candidateSetVersion": 1,
            "approvedStockIds": ["TW:2330"],
            "dailyOverrideIds": [],
            "calculationVersion": CALCULATION_VERSION_MONTHLY_FIRST,
        },
    )
    state.user_id = DEFAULT_USER_ID
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        service,
    )
    # Exact pinned candidate-set rows (empty overrides are valid persisted provenance).
    candidate_rows = {
        ("cs_a", 1): {
            "candidate_set_id": "cs_a",
            "version": 1,
            "selection_reasons": {"dailyOverrideIds": []},
        }
    }

    def fake_get_candidate_set(user_id, *, candidate_set_id, version=None):  # noqa: ANN001
        del user_id
        if version is None:
            return None
        return candidate_rows.get((str(candidate_set_id), int(version)))

    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_repository.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_repository.get_candidate_set",
        fake_get_candidate_set,
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_repository.latest_candidate_set",
        lambda user_id: (_ for _ in ()).throw(
            AssertionError("must not fall back to latest candidate set")
        ),
    )

    research_run_store.begin_phase1(DEFAULT_USER_ID, trading_day="2024-06-27")
    run = research_run_store.get(DEFAULT_USER_ID)
    assert run is not None
    run.phase = "phase2_technical"
    run.status = "running"
    run.gates["candidateSelection"] = "approved"
    run.candidate_set_id = "cs_a"
    run.candidate_set_version = 1
    run.technical_scan_id = state.scan_id

    # Identical retry → reuse.
    ok = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={
            "candidateSetId": "cs_a",
            "expectedVersion": 1,
            "tradingDay": "2024-06-27",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["scan"]["scanId"] == state.scan_id

    # Omitted id/version/day inherit pinned context → same reuse.
    omit = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={},
    )
    assert omit.status_code == 200, omit.text
    assert omit.json()["scan"]["scanId"] == state.scan_id

    # Run advanced to v2 while an incompatible v1 scan remains attached.
    run.candidate_set_version = 2
    bad_version = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={
            "candidateSetId": "cs_a",
            "expectedVersion": 2,
            "tradingDay": "2024-06-27",
        },
    )
    assert bad_version.status_code == 409
    assert (
        bad_version.json()["detail"]["error"]
        == "research_run_scan_candidate_set_mismatch"
    )

    # Restore run pin for subsequent scan-level checks.
    run.candidate_set_version = 1

    # Changed trading day → 409.
    bad_day = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={
            "candidateSetId": "cs_a",
            "expectedVersion": 1,
            "tradingDay": "2024-06-28",
        },
    )
    assert bad_day.status_code == 409
    assert bad_day.json()["detail"]["error"] == "phase2_scan_request_mismatch"
    assert "tradingDay" in bad_day.json()["detail"]["mismatches"]

    # Changed daily overrides → 409 (effective grows beyond attached []).
    bad_overrides = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={
            "candidateSetId": "cs_a",
            "expectedVersion": 1,
            "tradingDay": "2024-06-27",
            "dailyOverrideIds": ["TW:2330"],
        },
    )
    assert bad_overrides.status_code == 409
    assert bad_overrides.json()["detail"]["error"] == "phase2_scan_request_mismatch"
    assert "dailyOverrideIds" in bad_overrides.json()["detail"]["mismatches"]

    # Additive semantics: candidate [2317] ∪ request [2330] == attached → reuse.
    candidate_rows[("cs_a", 1)] = {
        "candidate_set_id": "cs_a",
        "version": 1,
        "selection_reasons": {"dailyOverrideIds": ["TW:2317"]},
    }
    state.candidate_provenance = {
        **dict(state.candidate_provenance or {}),
        "dailyOverrideIds": ["TW:2317", "TW:2330"],
        "approvedStockIds": list(state.stock_ids),
    }
    shrink = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={
            "candidateSetId": "cs_a",
            "expectedVersion": 1,
            "tradingDay": "2024-06-27",
            "dailyOverrideIds": ["TW:2317"],
        },
    )
    assert shrink.status_code == 409
    assert "dailyOverrideIds" in shrink.json()["detail"]["mismatches"]

    match_union = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={
            "candidateSetId": "cs_a",
            "expectedVersion": 1,
            "tradingDay": "2024-06-27",
            "dailyOverrideIds": ["TW:2330"],
        },
    )
    assert match_union.status_code == 200, match_union.text
    assert match_union.json()["scan"]["scanId"] == state.scan_id
    research_run_store.disable_test_memory()


def test_report_evidence_full_two_pass_requires_candidate_provenance(monkeypatch) -> None:
    from app.db.init_ids import DEFAULT_USER_ID
    from app.services.tw_research.constants import CALCULATION_VERSION_MONTHLY_FIRST
    from app.services.tw_research.scan import (
        SymbolScanRecord,
        TwResearchScanService,
        UniverseSelector,
    )
    from tests.support import stock_record

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
        history_loader=lambda *a, **k: ([], 0),
        stock_loader=catalog.get,
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        calculation_version=CALCULATION_VERSION_MONTHLY_FIRST,
        trading_day="2024-06-28",
        # Missing candidateSetId/version on purpose.
        candidate_provenance={"firstPassSnapshotId": "fps_x", "firstPassDigest": "d"},
    )
    state.user_id = DEFAULT_USER_ID
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        stock_name="台積電",
        name_status="ok",
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        service,
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.get_first_pass_snapshot",
        lambda snapshot_id, user_id=None: {
            "snapshot_id": snapshot_id,
            "content_digest": "d",
            "stock_ids": ["TW:2330"],
            "payload": {"qualityStatus": "verified_ready"},
        },
    )
    response = client.post(
        "/api/tw-research/report-evidence",
        json={
            "scanId": state.scan_id,
            "firstPassSnapshotId": "fps_x",
            "tradingDay": "2024-06-28",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == (
        "full_two_pass_requires_candidate_set_provenance"
    )


def test_candidate_technical_scan_store_unavailable_returns_503_without_scan(
    monkeypatch,
) -> None:
    from app.services.tw_research.research_run import (
        ResearchRunStoreUnavailable,
        research_run_store,
    )

    research_run_store.clear_for_tests()
    created: list[str] = []

    def boom_get(user_id: str):  # noqa: ANN001
        del user_id
        raise ResearchRunStoreUnavailable("research_run_store_unavailable")

    def should_not_create(**kwargs):  # noqa: ANN003
        created.append("called")
        raise AssertionError("scan must not be created when run store is unavailable")

    monkeypatch.setattr(research_run_store, "get", boom_get)
    monkeypatch.setattr(
        "app.api.routes.tw_research.create_technical_scan_from_candidate_set",
        should_not_create,
    )
    response = client.post(
        "/api/tw-research/candidate-sets/technical-scan",
        json={"tradingDay": "2024-06-27"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "research_run_store_unavailable"
    assert created == []
    research_run_store.disable_test_memory()


def test_chip_evidence_returns_rows_for_owner(monkeypatch) -> None:
    """Frozen first-pass chip-evidence is ownership-scoped (#346/#347)."""
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.is_available",
        lambda: True,
    )

    def _snap(snapshot_id, user_id=None):  # noqa: ANN001
        del user_id
        return {
            "snapshot_id": snapshot_id,
            "content_digest": "digest_chip",
            "payload": {
                "records": [
                    {
                        "stockId": "TW:2330",
                        "stockName": "台積電",
                        "nameStatus": "ok",
                        "fields": {
                            "institutional_5d": {
                                "status": "ok",
                                "source_as_of": "2024-06-24",
                                "value": [
                                    {
                                        "date": f"2024-06-{20 + i:02d}",
                                        "foreignNetLots": 10 + i,
                                        "trustNetLots": 1,
                                        "dealerNetLots": -1,
                                        "totalNetLots": 10 + i,
                                    }
                                    for i in range(5)
                                ],
                            },
                            "margin": {
                                "status": "ok",
                                "source_as_of": "2024-06-24",
                                "value": {
                                    "balanceLots": 100,
                                    "balanceDeltaLotsApprox5Sessions": 5,
                                },
                            },
                            "margin_utilization": {
                                "status": "unavailable",
                                "reason": "unverified",
                            },
                            "tdcc_holders_ge_400_lots": {
                                "status": "ok",
                                "source_as_of": "2024-06-21",
                                "value": {
                                    "percent": 41.0,
                                    "weeklyChangePercentPoints": 0.5,
                                    "priorWeekDate": "2024-06-14",
                                },
                            },
                        },
                    }
                ]
            },
        }

    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.get_first_pass_snapshot",
        _snap,
    )
    ok = client.get("/api/tw-research/first-pass/snapshots/fps_chip/chip-evidence")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["snapshotId"] == "fps_chip"
    assert body["total"] == 1
    assert body["rows"][0]["institutional"]["fiveSessionTotalsStatus"] == "ok"
    assert body["notBuySignal"] is True


def test_chip_evidence_cross_user_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.get_first_pass_snapshot",
        lambda snapshot_id, user_id=None: None,
    )
    from fastapi import HTTPException
    from app.api.routes import tw_research as tw_routes

    try:
        tw_routes.get_first_pass_chip_evidence(
            "fps_secret",
            user_id="intruder",
            offset=0,
            limit=25,
            q=None,
        )
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "first_pass_snapshot_not_found"
    else:
        raise AssertionError("Expected HTTPException for cross-user chip evidence")
