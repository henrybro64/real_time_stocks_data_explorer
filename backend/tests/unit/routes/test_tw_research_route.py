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
