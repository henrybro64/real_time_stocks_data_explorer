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
