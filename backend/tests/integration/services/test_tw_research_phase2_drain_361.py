"""PostgreSQL Phase-2 technical drain on the First-Pass CronJob path (#361).

Skipped automatically when DATABASE_URL is unset (integration conftest).
No live provider calls — history_loader is stubbed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.db.models import User
from app.db.session import SessionLocal
from app.models.quote import CandleRecord
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.research_run import research_run_store
from app.services.tw_research.scan import TwResearchScanService
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


def _rising_history(n: int = 120) -> list[CandleRecord]:
    end = datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
    dates: list[datetime] = []
    offset = 0
    while len(dates) < n:
        day = end - timedelta(days=offset)
        offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day.replace(hour=13, minute=30, second=0, microsecond=0))
    dates.reverse()
    out: list[CandleRecord] = []
    for index, stamp in enumerate(dates):
        close = 100.0 + index
        out.append(
            CandleRecord(
                time=stamp.isoformat(),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000 + index,
            )
        )
    return out


def _arm_drain(monkeypatch, user_id: str) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )
    monkeypatch.setattr(
        "app.services.tw_research.research_run.settings.tw_research_first_pass_drain_token",
        "integration-phase2-drain-token",
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_user_id",
        user_id,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.settings.tw_research_first_pass_drain_token",
        "integration-phase2-drain-token",
    )


def _make_service() -> TwResearchScanService:
    history = _rising_history()
    return TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: (
            history[: min(limit, len(history))],
            0,
        ),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )


def test_phase2_drain_advances_persisted_scan_rows(db_session, monkeypatch) -> None:
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"p2drain-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)

    service = _make_service()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service",
        service,
    )
    # Reconcile loads scans via research_run_store._load_scan — keep durable path.
    state = service.create_scan(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2330", "TW:2454", "TW:2303", "TW:2317"),
        ),
        target_count=10,
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.cursor == 0

    store = research_run_store
    store.begin_phase1(user_id, trading_day="2024-06-28")
    with store._lock:  # noqa: SLF001
        locked = store._require_locked(user_id)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.schedule_enabled = False
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = state.scan_id
        store._persist_locked(locked)

    before = service.get_scan(state.scan_id)
    assert before is not None
    cursor_before = before.cursor

    result = drain_authorized_first_pass(
        user_id=user_id,
        enabled=True,
        dry_run=False,
        max_batches=1,
        batch_size=2,
        time_budget_seconds=60,
    )
    assert result.outcome in {"phase2_batch_ran", "phase2_already_complete"}
    assert result.technical_scan_id == state.scan_id

    after = service.get_scan(state.scan_id)
    assert after is not None
    assert after.cursor > cursor_before
    run = store.get(user_id)
    assert run is not None
    meta = (run.dimensions or {}).get("firstPassDrain") or {}
    assert isinstance(meta.get("lastTickAt"), str)

    # Second tick is idempotent under lease fencing / remaining work.
    again = drain_authorized_first_pass(
        user_id=user_id,
        enabled=True,
        dry_run=False,
        max_batches=1,
        batch_size=2,
        time_budget_seconds=60,
    )
    assert again.outcome in {
        "phase2_batch_ran",
        "phase2_lease_held",
        "phase2_already_complete",
    }


def test_phase2_unapproved_integration_no_work(db_session, monkeypatch) -> None:
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"p2unapp-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)
    service = _make_service()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service",
        service,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        target_count=10,
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    store = research_run_store
    store.begin_phase1(user_id, trading_day="2024-06-28")
    with store._lock:  # noqa: SLF001
        locked = store._require_locked(user_id)
        locked.phase = "awaiting_analyst_candidate_selection"
        locked.status = "gated"
        locked.schedule_enabled = False
        locked.gates["candidateSelection"] = "awaiting_analyst"
        locked.technical_scan_id = state.scan_id
        store._persist_locked(locked)

    result = drain_authorized_first_pass(
        user_id=user_id, enabled=True, dry_run=False, batch_size=2
    )
    assert result.outcome == "skipped_phase2_not_approved"
    after = service.get_scan(state.scan_id)
    assert after is not None
    assert after.cursor == 0
