"""PostgreSQL Phase-2 technical drain on the First-Pass CronJob path (#361).

Skipped automatically when DATABASE_URL is unset (integration conftest).
No live provider calls — history_loader / stock_loader are stubbed.
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
from app.repositories.tw_research_repository import TwResearchRepository
from app.services.tw_research.constants import HISTORY_BUDGET_EXHAUSTED_REASON
from app.services.tw_research.first_pass_drain import drain_authorized_first_pass
from app.services.tw_research.persistence import persist_symbol, scan_row_payload
from app.services.tw_research.research_run import research_run_store
from app.services.tw_research.scan import (
    SymbolScanRecord,
    TwResearchScanService,
)
from app.services.tw_research.scheduler import schedule_has_technical_work
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


def _make_budget_service() -> TwResearchScanService:
    """Loader that consumes month-fetch budget so scans can hit budget_exhausted."""

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit
        if max_month_fetches <= 0:
            return [], 0
        return _rising_history(80), min(3, max_month_fetches)

    return TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )


def _attach_phase2_run(user_id: str, scan_id: str, *, enabled: bool = True) -> None:
    store = research_run_store
    store.begin_phase1(user_id, trading_day="2024-06-28")
    with store._lock:  # noqa: SLF001
        locked = store._require_locked(user_id)
        locked.phase = "phase2_technical"
        locked.status = "running"
        locked.schedule_enabled = False
        locked.gates["candidateSelection"] = "approved"
        locked.technical_scan_id = scan_id
        locked.candidate_set_id = "cand_1"
        locked.candidate_set_version = 1
        locked.dimensions["phase2Drain"] = {"enabled": enabled}
        store._persist_locked(locked)


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
    _attach_phase2_run(user_id, state.scan_id)

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
    run = research_run_store.get(user_id)
    assert run is not None
    meta = (run.dimensions or {}).get("firstPassDrain") or {}
    assert isinstance(meta.get("lastTickAt"), str)

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


def test_phase2_budget_exhausted_resumes_across_ticks(db_session, monkeypatch) -> None:
    """(a) budget_exhausted + technical work: tick1 resumes+advances; tick2 advances further."""
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"p2budg-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)

    # Stubbed loader only — no live providers. Force durable budget_exhausted with
    # remaining technical work (same pattern as coordination #329 PG tests).
    service = _make_budget_service()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service",
        service,
    )
    state = service.create_scan(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2330", "TW:2454", "TW:2303", "TW:2317"),
        ),
        trading_day="2024-06-28",
        user_id=user_id,
        max_upstream_month_fetches=1,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    repo = TwResearchRepository()
    persist_symbol(
        state,
        SymbolScanRecord(
            stock_id="TW:2330",
            stage="rejected",
            readiness="rejected",
            rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        ),
        repository=repo,
    )
    payload = scan_row_payload(state, user_id=user_id)
    payload["status"] = "budget_exhausted"
    payload["cursor"] = 1
    payload["max_upstream_month_fetches"] = 1
    repo.upsert_scan(payload)
    state = service.get_scan(state.scan_id)
    assert state is not None
    assert state.status == "budget_exhausted"
    assert schedule_has_technical_work(state)
    windows_before = int(getattr(state, "budget_windows_opened", 0) or 0)
    cursor_before = int(state.cursor or 0)

    _attach_phase2_run(user_id, state.scan_id)

    tick1 = drain_authorized_first_pass(
        user_id=user_id,
        enabled=True,
        dry_run=False,
        max_batches=1,
        batch_size=2,
        time_budget_seconds=60,
    )
    assert tick1.outcome in {"phase2_batch_ran", "phase2_already_complete"}
    assert "budget_window_resumed" in tick1.notes
    after1 = service.get_scan(state.scan_id)
    assert after1 is not None
    assert int(getattr(after1, "budget_windows_opened", 0) or 0) > windows_before
    assert int(after1.cursor or 0) > cursor_before
    cursor_mid = int(after1.cursor or 0)

    tick2 = drain_authorized_first_pass(
        user_id=user_id,
        enabled=True,
        dry_run=False,
        max_batches=1,
        batch_size=2,
        time_budget_seconds=60,
    )
    assert tick2.outcome in {
        "phase2_batch_ran",
        "phase2_already_complete",
        "phase2_lease_held",
        "phase2_no_progress",
        "phase2_budget_cap_reached",
    }
    after2 = service.get_scan(state.scan_id)
    assert after2 is not None
    if tick2.outcome == "phase2_batch_ran":
        assert int(after2.cursor or 0) > cursor_mid
    else:
        assert int(after2.cursor or 0) >= cursor_mid


def test_phase2_foreign_scan_untouched_integration(db_session, monkeypatch) -> None:
    """(b) Foreign-owned technical scan is refused; persisted rows unchanged."""
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    owner = f"p2own-{uuid4().hex[:8]}"
    foreign = f"p2for-{uuid4().hex[:8]}"
    _ensure_user(owner)
    _ensure_user(foreign)
    _arm_drain(monkeypatch, owner)

    service = _make_service()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service",
        service,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        user_id=foreign,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    cursor_before = state.cursor
    _attach_phase2_run(owner, state.scan_id)

    result = drain_authorized_first_pass(
        user_id=owner, enabled=True, dry_run=False, batch_size=2
    )
    assert result.outcome == "skipped_phase2_foreign_scan"
    after = service.get_scan(state.scan_id)
    assert after is not None
    assert after.cursor == cursor_before
    assert after.user_id == foreign


def test_phase2_cancelled_run_untouched_integration(db_session, monkeypatch) -> None:
    """(c) Cancelled research run does not advance the attached technical scan."""
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"p2can-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)

    service = _make_service()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service",
        service,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    _attach_phase2_run(user_id, state.scan_id)
    with research_run_store._lock:  # noqa: SLF001
        locked = research_run_store._require_locked(user_id)
        locked.status = "cancelled"
        research_run_store._persist_locked(locked)

    result = drain_authorized_first_pass(
        user_id=user_id, enabled=True, dry_run=False, batch_size=2
    )
    assert result.outcome == "skipped_run_status"
    after = service.get_scan(state.scan_id)
    assert after is not None
    assert after.cursor == 0


def test_phase2_pause_then_resume_integration(db_session, monkeypatch) -> None:
    """(d) enabled=false skips Phase-2; enabled=true resumes and advances."""
    del db_session
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = f"p2paus-{uuid4().hex[:8]}"
    _ensure_user(user_id)
    _arm_drain(monkeypatch, user_id)

    service = _make_service()
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_drain.tw_research_scan_service",
        service,
    )
    state = service.create_scan(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2330", "TW:2454", "TW:2303", "TW:2317"),
        ),
        trading_day="2024-06-28",
        user_id=user_id,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    _attach_phase2_run(user_id, state.scan_id, enabled=False)

    paused = drain_authorized_first_pass(
        user_id=user_id, enabled=True, dry_run=False, batch_size=2
    )
    assert paused.outcome == "skipped_phase2_paused"
    mid = service.get_scan(state.scan_id)
    assert mid is not None
    assert mid.cursor == 0

    research_run_store.set_first_pass_drain_enabled(user_id, enabled=True)
    resumed = drain_authorized_first_pass(
        user_id=user_id,
        enabled=True,
        dry_run=False,
        max_batches=1,
        batch_size=2,
        time_budget_seconds=60,
    )
    assert resumed.outcome in {"phase2_batch_ran", "phase2_already_complete"}
    after = service.get_scan(state.scan_id)
    assert after is not None
    assert after.cursor > 0


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
