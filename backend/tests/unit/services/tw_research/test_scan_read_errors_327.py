"""Durable reads must not masquerade database failures as empty ledgers (#327)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_effective_user_id
from app.db.init_ids import DEFAULT_USER_ID
from app.main import app
from app.services.tw_research.scan import TwResearchDurableStoreError, TwResearchScanService
from tests.support import stock_record

client = TestClient(app)


def _service() -> TwResearchScanService:
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
    return TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )


def test_load_scan_state_raises_when_scan_row_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.tw_research import persistence as persistence_module

    class BoomRepo:
        def is_available(self) -> bool:
            return True

        def get_scan(self, scan_id: str):
            raise RuntimeError("db down")

        def list_symbol_results(self, scan_id: str):
            raise AssertionError("symbol results must not run after scan-row failure")

    with pytest.raises(TwResearchDurableStoreError) as raised:
        persistence_module.load_scan_state("scan-1", repository=BoomRepo())  # type: ignore[arg-type]
    assert raised.value.operation == "load_scan"
    assert raised.value.scan_id == "scan-1"


def test_load_scan_state_raises_when_symbol_rows_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.tw_research import persistence as persistence_module

    class PartialRepo:
        def is_available(self) -> bool:
            return True

        def get_scan(self, scan_id: str):
            return {
                "scan_id": scan_id,
                "user_id": DEFAULT_USER_ID,
                "trading_day": "2026-11-10",
                "universe_version": "explicit-v1",
                "calculation_version": "calc",
                "selector_json": {"mode": "stock_ids", "themeKeys": [], "stockIds": ["TW:2330"]},
                "stock_ids_json": ["TW:2330"],
                "status": "running",
                "quality_status": "processed",
                "cursor": 1,
                "enrich_cursor": 0,
                "target_count": 10,
                "day_pass_ids_json": [],
                "qualified_ids_json": [],
                "upstream_month_fetches": 0,
                "max_upstream_month_fetches": 40,
                "enrichments_run": 0,
                "max_enrichments_per_run": 20,
                "enrich_lease_generation": 0,
                "notes_json": [],
                "active_snapshot_id": None,
                "reserve_expires_at": None,
                "enrich_reserve_expires_at": None,
                "created_at": "2026-11-10T08:00:00+08:00",
                "updated_at": "2026-11-10T08:01:00+08:00",
            }

        def list_symbol_results(self, scan_id: str):
            raise RuntimeError("symbol query failed")

    with pytest.raises(TwResearchDurableStoreError) as raised:
        persistence_module.load_scan_state("scan-partial", repository=PartialRepo())  # type: ignore[arg-type]
    assert raised.value.operation == "load_symbol_results"


def test_missing_scan_still_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.tw_research import persistence as persistence_module

    class EmptyRepo:
        def is_available(self) -> bool:
            return True

        def get_scan(self, scan_id: str):
            return None

        def list_symbol_results(self, scan_id: str):
            raise AssertionError("should not list symbols for a missing scan")

    assert persistence_module.load_scan_state("missing", repository=EmptyRepo()) is None  # type: ignore[arg-type]


def test_get_scan_route_maps_store_failure_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    service = TwResearchScanService(persist=True, stock_loader=lambda stock_id: None)

    def _boom(scan_id: str):
        raise TwResearchDurableStoreError(
            "Failed to load scan",
            scan_id=scan_id,
            operation="load_scan",
        )

    monkeypatch.setattr(service, "get_scan", _boom)
    monkeypatch.setattr("app.api.routes.tw_research.tw_research_scan_service", service)
    response = client.get("/api/tw-research/scans/scan-db-down")
    assert response.status_code == 503
    payload = response.json()["detail"]
    assert payload["error"] == "durable_store_unavailable"
    assert payload["retryable"] is True
    assert payload["operation"] == "load_scan"


def test_progress_route_is_counters_only_and_user_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service()
    monkeypatch.setattr("app.api.routes.tw_research.tw_research_scan_service", service)
    created = client.post(
        "/api/tw-research/scans",
        json={
            "universe": {"mode": "stock_ids", "stockIds": ["TW:2330"]},
            "targetCount": 10,
            "tradingDay": "2026-11-10",
        },
    )
    assert created.status_code == 200
    scan_id = created.json()["scan"]["scanId"]

    progress = client.get(f"/api/tw-research/scans/{scan_id}/progress")
    assert progress.status_code == 200
    body = progress.json()["progress"]
    assert body["scanId"] == scan_id
    assert body["includesLedger"] is False
    assert "ledger" not in body
    assert body["ledgerRevision"]
    assert body["processedCount"] == 0

    app.dependency_overrides[get_effective_user_id] = lambda: "other-user"
    try:
        assert client.get(f"/api/tw-research/scans/{scan_id}/progress").status_code == 404
    finally:
        app.dependency_overrides.pop(get_effective_user_id, None)
