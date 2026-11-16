"""PostgreSQL Stage-4 technical-results read model (#361).

Skipped when DATABASE_URL is unset. No live provider calls.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.db.models import User
from app.db.session import SessionLocal
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.constants import (
    DAILY_NOT_RUN_BY_DESIGN,
    TECHNICAL_STAGE_DAILY_NOT_RUN,
    TECHNICAL_STAGE_DAILY_PASS,
)
from app.services.tw_research.persistence import persist_symbol, scan_row_payload
from app.services.tw_research.research_run import research_run_store
from app.services.tw_research.scan import (
    SymbolScanRecord,
    TwResearchScanService,
)
from app.services.tw_research.technical_results import build_technical_results_payload
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record


pytestmark = pytest.mark.integration
TZ = ZoneInfo("Asia/Taipei")


def _ensure_user(user_id: str) -> None:
    assert SessionLocal is not None
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, username=user_id, password_hash=None))
            session.commit()


def _tw(stock_id: str):
    symbol = stock_id.split(":", 1)[1]
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=symbol,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_zh_tw=symbol,
    )


def test_technical_results_reads_persisted_records(db_session) -> None:
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"p2res-{uuid4().hex[:8]}"
    _ensure_user(user_id)

    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    persist_symbol(
        state,
        SymbolScanRecord(
            stock_id="TW:2330",
            stage="B",
            readiness="technically_qualified",
            technical_stage=TECHNICAL_STAGE_DAILY_PASS,
            stock_name="2330",
            name_status="ok",
            monthly={
                "asOf": "2024-06",
                "dif": 1.25,
                "signal": 0.9,
                "osc": 0.35,
                "k": 70.0,
                "d": 60.0,
                "barCount": 48,
                "quality": "ok",
                "passStatus": "pass",
                "priceBasis": "unadjusted_close",
                "timeframe": "monthly",
            },
            daily={
                "asOf": "2024-06-28",
                "dif": 2.0,
                "signal": 1.0,
                "osc": 1.0,
                "k": 75.0,
                "d": 65.0,
                "barCount": 100,
                "quality": "ok",
                "passStatus": "pass",
                "timeframe": "daily",
            },
        ),
        repository=tw_research_repository,
    )
    persist_symbol(
        state,
        SymbolScanRecord(
            stock_id="TW:2454",
            stage="A",
            readiness="processed",
            technical_stage=TECHNICAL_STAGE_DAILY_NOT_RUN,
            stock_name="2454",
            name_status="ok",
            monthly={
                "asOf": "2024-05",
                "dif": -1.0,
                "signal": -0.5,
                "osc": -0.5,
                "k": 15.0,
                "d": 20.0,
                "barCount": 40,
                "quality": "ok",
                "passStatus": "fail",
                "timeframe": "monthly",
            },
            daily={
                "asOf": None,
                "executionStatus": DAILY_NOT_RUN_BY_DESIGN,
                "quality": "unavailable",
                "passStatus": None,
                "barCount": 0,
                "reason": "monthly_gate_failed",
                "timeframe": "daily",
            },
        ),
        repository=tw_research_repository,
    )
    payload = scan_row_payload(state, user_id=user_id)
    payload["status"] = "verified_ready"
    payload["cursor"] = 2
    tw_research_repository.upsert_scan(payload)

    loaded = service.get_scan(state.scan_id)
    assert loaded is not None
    assert "TW:2330" in loaded.records
    assert "TW:2454" in loaded.records

    store = research_run_store
    store.begin_phase1(user_id, trading_day="2024-06-28")
    with store._lock:  # noqa: SLF001
        locked = store._require_locked(user_id)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = state.scan_id
        store._persist_locked(locked)
    run = store.get(user_id)
    assert run is not None

    body = build_technical_results_payload(
        run=run, state=loaded, phase2_execution={"state": "completed"}
    )
    assert body["technicalScanId"] == state.scan_id
    assert body["scanStatus"] == "verified_ready"
    assert body["priceBasis"] == "unadjusted_close"
    by_id = {row["stockId"]: row for row in body["candidates"]}
    assert by_id["TW:2330"]["overallStageResult"] == "pass"
    assert by_id["TW:2330"]["monthly"]["dif"] == 1.25
    assert by_id["TW:2454"]["daily"] is None
    assert by_id["TW:2454"]["dailyNotRunReason"] == "monthly_gate_failed"


def test_technical_results_get_twice_leaves_revision_unchanged(
    db_session, monkeypatch
) -> None:
    """GET technical-results is read-only: no reconcile persist / revision bump."""
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    from app.api.deps import get_effective_user_id
    from app.main import app
    from fastapi.testclient import TestClient

    user_id = f"p2rev-{uuid4().hex[:8]}"
    _ensure_user(user_id)

    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )

    store = research_run_store
    store.begin_phase1(user_id, trading_day="2024-06-28")
    with store._lock:  # noqa: SLF001
        locked = store._require_locked(user_id)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = state.scan_id
        store._persist_locked(locked)

    before = store.get(user_id)
    assert before is not None
    revision_before = int(before.revision)

    app.dependency_overrides[get_effective_user_id] = lambda: user_id
    try:
        client = TestClient(app)
        first = client.get("/api/tw-research/research-runs/technical-results")
        assert first.status_code == 200, first.text
        second = client.get("/api/tw-research/research-runs/technical-results")
        assert second.status_code == 200, second.text
    finally:
        app.dependency_overrides.pop(get_effective_user_id, None)

    after = store.get(user_id)
    assert after is not None
    assert int(after.revision) == revision_before


def test_historical_scan_results_do_not_leak_current_scan_values(
    db_session, monkeypatch
) -> None:
    """Two owned scans with different MACD values must not cross-display."""
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    from app.api.deps import get_effective_user_id
    from app.main import app
    from fastapi.testclient import TestClient

    user_id = f"p2hist-{uuid4().hex[:8]}"
    _ensure_user(user_id)

    service = TwResearchScanService(
        history_loader=lambda *_a, **_k: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    current = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    historical = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2454",)),
        trading_day="2024-06-20",
        user_id=user_id,
        now=datetime(2024, 6, 20, 16, 0, tzinfo=TZ),
    )
    persist_symbol(
        current,
        SymbolScanRecord(
            stock_id="TW:2330",
            stage="B",
            readiness="technically_qualified",
            technical_stage=TECHNICAL_STAGE_DAILY_PASS,
            monthly={
                "asOf": "2024-05-31",
                "dif": 1.11,
                "signal": 0.5,
                "osc": 0.61,
                "k": 60.0,
                "d": 55.0,
                "barCount": 40,
                "quality": "ok",
                "passStatus": "pass",
                "timeframe": "monthly",
            },
        ),
    )
    persist_symbol(
        historical,
        SymbolScanRecord(
            stock_id="TW:2454",
            stage="B",
            readiness="technically_qualified",
            technical_stage=TECHNICAL_STAGE_DAILY_PASS,
            monthly={
                "asOf": "2024-04-30",
                "dif": 7.77,
                "signal": 1.0,
                "osc": 6.77,
                "k": 80.0,
                "d": 70.0,
                "barCount": 40,
                "quality": "ok",
                "passStatus": "pass",
                "timeframe": "monthly",
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.tw_research_scan_service",
        service,
    )
    store = research_run_store
    store.begin_phase1(user_id, trading_day="2024-06-28")
    with store._lock:  # noqa: SLF001
        locked = store._require_locked(user_id)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = current.scan_id
        store._persist_locked(locked)

    app.dependency_overrides[get_effective_user_id] = lambda: user_id
    try:
        client = TestClient(app)
        hist = client.get(
            f"/api/tw-research/research-runs/technical-results?scanId={historical.scan_id}"
        )
        assert hist.status_code == 200, hist.text
        hist_body = hist.json()
        assert hist_body["technicalScanId"] == historical.scan_id
        assert hist_body["viewingHistorical"] is True
        assert hist_body["candidates"][0]["monthly"]["dif"] == 7.77
        assert hist_body["phase2Execution"] is None

        cur = client.get("/api/tw-research/research-runs/technical-results")
        assert cur.status_code == 200, cur.text
        cur_body = cur.json()
        assert cur_body["technicalScanId"] == current.scan_id
        assert cur_body["candidates"][0]["monthly"]["dif"] == 1.11

        # Must not reattach historical scan onto the run.
        after = store.get(user_id)
        assert after is not None
        assert after.technical_scan_id == current.scan_id
    finally:
        app.dependency_overrides.pop(get_effective_user_id, None)
