"""PostgreSQL coordination for admission, history flight, and resume (#329)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import pytest

from app.db.init_ids import DEFAULT_USER_ID
from app.repositories.tw_research_repository import TwResearchRepository
from app.services.tw_research.constants import (
    DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
    HISTORY_BUDGET_EXHAUSTED_REASON,
)
from app.services.tw_research.persistence import persist_symbol, scan_row_payload
from app.services.tw_research.scan import (
    SymbolScanRecord,
    TwResearchAdmissionError,
    TwResearchConflictError,
    TwResearchScanService,
    history_flight_identity,
)
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record


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
    )


def test_postgres_atomic_admission_denies_over_capacity() -> None:
    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    holders = []
    for index in range(DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES):
        state = service.create_scan(
            UniverseSelector(
                mode="stock_ids",
                stock_ids=(f"TW:233{index}", f"TW:245{index}"),
            ),
            trading_day="2024-06-28",
            user_id=DEFAULT_USER_ID,
        )
        claimed = repo.claim_scan_batch(
            state.scan_id,
            batch_size=1,
            terminal_statuses={"completed_processing", "verified_ready"},
            max_in_flight=DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
        )
        assert claimed is not None
        assert claimed["admission_denied"] is False
        assert claimed["batch_ids"]
        holders.append(state.scan_id)

    overflow = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2303", "TW:2317")),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    denied = repo.claim_scan_batch(
        overflow.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
    )
    assert denied is not None
    assert denied["admission_denied"] is True
    assert denied["in_flight"] >= DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES

    with pytest.raises(TwResearchAdmissionError):
        service.run_batch(overflow.scan_id, batch_size=1)


def test_postgres_simultaneous_admission_respects_limit() -> None:
    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    scan_ids = []
    for index in range(4):
        state = service.create_scan(
            UniverseSelector(
                mode="stock_ids",
                stock_ids=(f"TW:11{index:02d}", f"TW:12{index:02d}"),
            ),
            trading_day="2024-06-28",
            user_id=DEFAULT_USER_ID,
        )
        scan_ids.append(state.scan_id)

    def _claim(scan_id: str) -> dict:
        return repo.claim_scan_batch(
            scan_id,
            batch_size=1,
            terminal_statuses={"completed_processing", "verified_ready"},
            max_in_flight=2,
            lease_seconds=120,
        )

    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_claim, scan_id) for scan_id in scan_ids]
        for future in as_completed(futures):
            results.append(future.result())

    acquired = [row for row in results if row and row.get("batch_ids")]
    denied = [row for row in results if row and row.get("admission_denied")]
    assert len(acquired) == 2
    assert len(denied) == 2


def test_postgres_history_flight_single_owner_and_waiter_reuses() -> None:
    repo = TwResearchRepository()
    flight_key, compat = history_flight_identity("TW:2330", limit=800)
    first = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2330",
        compatibility_key=compat,
        owner_token="owner-a",
        lease_seconds=60,
    )
    assert first["status"] == "acquired"
    second = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2330",
        compatibility_key=compat,
        owner_token="owner-b",
        lease_seconds=60,
    )
    assert second["status"] == "busy"
    done = repo.complete_history_flight(
        flight_key=flight_key,
        owner_token="owner-a",
        generation=int(first["generation"]),
        success=True,
    )
    assert done["ok"] is True
    stale = repo.complete_history_flight(
        flight_key=flight_key,
        owner_token="owner-a",
        generation=int(first["generation"]),
        success=True,
    )
    # Same generation still owns ready marker until TTL; not stale for complete.
    assert stale["ok"] is True or stale["stale"] is True
    ready = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2330",
        compatibility_key=compat,
        owner_token="owner-b",
        lease_seconds=60,
    )
    assert ready["status"] in {"ready", "acquired"}


def test_postgres_history_flight_recovers_after_lease_expiry() -> None:
    repo = TwResearchRepository()
    flight_key, compat = history_flight_identity("TW:2454", limit=800)
    first = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2454",
        compatibility_key=compat,
        owner_token="crash-pod",
        lease_seconds=1,
    )
    assert first["status"] == "acquired"
    from app.db.models import TwResearchHistoryFlight
    from app.db.session import SessionLocal

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchHistoryFlight, flight_key)
        assert row is not None
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        session.commit()

    recovered = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2454",
        compatibility_key=compat,
        owner_token="recovery-pod",
        lease_seconds=60,
    )
    assert recovered["status"] == "acquired"
    assert int(recovered["generation"]) > int(first["generation"])
    # Stale crashed owner cannot fence-complete.
    stale = repo.complete_history_flight(
        flight_key=flight_key,
        owner_token="crash-pod",
        generation=int(first["generation"]),
        success=True,
    )
    assert stale["stale"] is True


def test_postgres_resume_budget_is_atomic_and_rejects_active_lease() -> None:
    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
        max_upstream_month_fetches=3,
    )
    persist_symbol(
        state,
        SymbolScanRecord(
            stock_id="TW:2330",
            stage="A",
            readiness="processed",
            daily={"passStatus": "fail"},
        ),
        repository=repo,
    )
    persist_symbol(
        state,
        SymbolScanRecord(
            stock_id="TW:2454",
            stage="rejected",
            readiness="rejected",
            rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        ),
        repository=repo,
    )
    row = scan_row_payload(state, user_id=DEFAULT_USER_ID)
    row["status"] = "budget_exhausted"
    row["cursor"] = 1
    row["max_upstream_month_fetches"] = 3
    repo.upsert_scan(row)

    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
    )
    assert claimed is not None
    assert claimed["admission_denied"] is False

    blocked = repo.resume_budget_window(
        state.scan_id,
        additional_month_fetches=40,
        max_cap=200,
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        allowed_statuses={"budget_exhausted", "partial", "running"},
    )
    assert blocked is not None
    assert blocked["ok"] is False
    assert blocked["code"] == "active_batch_lease"

    repo.complete_scan_batch(
        state.scan_id,
        status="budget_exhausted",
        lease_generation=int(claimed["lease_generation"]),
    )

    first = repo.resume_budget_window(
        state.scan_id,
        additional_month_fetches=40,
        max_cap=200,
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        allowed_statuses={"budget_exhausted", "partial", "running"},
    )
    assert first is not None and first["ok"] is True
    assert "TW:2454" in first["cleared"]
    assert first["new_max"] == 43
    # Completed symbol remains.
    symbols = {row["stock_id"] for row in repo.list_symbol_results(state.scan_id)}
    assert "TW:2330" in symbols
    assert "TW:2454" not in symbols

    # After first resume the scan is partial with no exhausted rows left — a
    # concurrent/duplicate resume must not stack another budget window.
    second = repo.resume_budget_window(
        state.scan_id,
        additional_month_fetches=40,
        max_cap=200,
        rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
        allowed_statuses={"budget_exhausted", "partial", "running"},
    )
    assert second is not None
    assert second["ok"] is False
    assert second["code"] == "resume_not_applicable"
    assert int(first["scan"]["budget_windows_opened"]) == 1


def test_postgres_stale_scan_lease_cannot_complete() -> None:
    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    first = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert first is not None
    stale_generation = int(first["lease_generation"])

    from app.db.models import TwResearchScan
    from app.db.session import SessionLocal

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.reserve_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

    reclaimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert reclaimed is not None
    assert int(reclaimed["lease_generation"]) == stale_generation + 1

    stale_complete = repo.complete_scan_batch(
        state.scan_id,
        status="partial",
        lease_generation=stale_generation,
    )
    assert stale_complete is not None
    assert stale_complete["stale_lease"] is True
    assert stale_complete["reserve_expires_at"] is not None


def test_postgres_resume_via_service_conflict_on_active_batch() -> None:
    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
        max_upstream_month_fetches=1,
    )
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
    payload = scan_row_payload(state, user_id=DEFAULT_USER_ID)
    payload["status"] = "budget_exhausted"
    repo.upsert_scan(payload)
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
    )
    assert claimed is not None and not claimed.get("admission_denied")
    with pytest.raises(TwResearchConflictError) as exc:
        service.resume_budget_window(state.scan_id)
    assert exc.value.code == "active_batch_lease"
