"""Durable reads stay on committed rows, and the page can list them (#327)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_effective_user_id
from app.db.init_ids import DEFAULT_USER_ID
from app.main import app
from app.services.tw_research.scan import (
    ScanState,
    TwResearchScanService,
    describe_worker_activity,
)
from app.services.tw_research.universe import UniverseSelector
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
        history_loader=lambda stock, limit, *, max_month_fetches: (_unexpected_history(), 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )


def _unexpected_history():
    raise AssertionError("scan reads must not fetch price history")


def _state(scan_id: str, *, status: str = "queued", cursor: int = 0) -> ScanState:
    return ScanState(
        scan_id=scan_id,
        trading_day="2026-11-10",
        universe_version="explicit-v1",
        calculation_version="calc",
        selector=UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        stock_ids=("TW:2330",),
        status=status,  # type: ignore[arg-type]
        cursor=cursor,
        user_id=DEFAULT_USER_ID,
        updated_at="2026-11-10T08:00:00+08:00",
    )


def test_running_without_a_lease_is_not_treated_as_a_live_worker() -> None:
    assert describe_worker_activity("running", None, None) == "unknown"
    assert describe_worker_activity("queued", None, None) == "unknown"
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    assert describe_worker_activity("running", future, None) == "active"
    assert describe_worker_activity("budget_exhausted", None, None) == "idle"
    assert describe_worker_activity("verified_ready", None, None) == "idle"
    assert describe_worker_activity("completed_processing", None, None) == "idle"


def test_durable_get_scan_ignores_a_stale_process_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    service = TwResearchScanService(persist=True, stock_loader=lambda stock_id: None)
    stale = _state("scan-stale", status="queued", cursor=0)
    fresh = _state("scan-stale", status="running", cursor=4)
    service._scans[stale.scan_id] = stale

    monkeypatch.setattr(
        "app.services.tw_research.persistence.load_scan_state",
        lambda scan_id: fresh if scan_id == fresh.scan_id else None,
    )

    loaded = service.get_scan(stale.scan_id)
    assert loaded is fresh
    assert loaded is not stale
    assert loaded is not None
    assert loaded.cursor == 4
    assert loaded.status == "running"


def test_memory_mode_still_reads_its_own_cache() -> None:
    service = _service()
    state = _state("scan-memory")
    service._scans[state.scan_id] = state
    assert service.get_scan(state.scan_id) is state


def test_list_and_get_are_user_scoped_and_do_not_fetch_history(monkeypatch: pytest.MonkeyPatch) -> None:
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
    scan = created.json()["scan"]
    assert scan["workerActivity"] == "unknown"
    assert scan["qualityStatus"] != "verified_ready"

    listed = client.get("/api/tw-research/scans")
    assert listed.status_code == 200
    assert [item["scanId"] for item in listed.json()["scans"]] == [scan["scanId"]]

    fetched = client.get(f"/api/tw-research/scans/{scan['scanId']}")
    assert fetched.status_code == 200
    assert fetched.json()["scan"]["scanId"] == scan["scanId"]
    assert fetched.json()["scan"]["status"] == "queued"

    app.dependency_overrides[get_effective_user_id] = lambda: "other-user"
    try:
        assert client.get(f"/api/tw-research/scans/{scan['scanId']}").status_code == 404
        hidden = client.get("/api/tw-research/scans")
        assert hidden.status_code == 200
        assert hidden.json()["scans"] == []
    finally:
        app.dependency_overrides.pop(get_effective_user_id, None)
