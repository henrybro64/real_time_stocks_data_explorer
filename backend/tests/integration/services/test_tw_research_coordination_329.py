"""PostgreSQL coordination for admission, history flight, and resume (#329)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
    flight_key, compat = history_flight_identity(
        "TW:2330", limit=800, expect_through="2024-06-28"
    )
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
    incomplete = repo.complete_history_flight(
        flight_key=flight_key,
        owner_token="owner-a",
        generation=int(first["generation"]),
        success=True,
        coverage_complete=False,
    )
    assert incomplete["ok"] is True
    assert incomplete["state"] == "incomplete"

    # Incomplete marker must not look like validated ready — reclaim allowed.
    reclaimed = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2330",
        compatibility_key=compat,
        owner_token="owner-b",
        lease_seconds=60,
    )
    assert reclaimed["status"] == "acquired"

    done = repo.complete_history_flight(
        flight_key=flight_key,
        owner_token="owner-b",
        generation=int(reclaimed["generation"]),
        success=True,
        coverage_complete=True,
    )
    assert done["ok"] is True
    assert done["state"] == "ready"
    ready = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2330",
        compatibility_key=compat,
        owner_token="owner-c",
        lease_seconds=60,
    )
    assert ready["status"] == "ready"


def test_postgres_history_flight_recovers_after_lease_expiry() -> None:
    repo = TwResearchRepository()
    flight_key, compat = history_flight_identity(
        "TW:2454", limit=800, expect_through="2024-06-28"
    )
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


def test_postgres_history_flight_renew_keeps_ownership() -> None:
    repo = TwResearchRepository()
    flight_key, compat = history_flight_identity(
        "TW:2303", limit=800, expect_through="2024-06-28"
    )
    first = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2303",
        compatibility_key=compat,
        owner_token="leader",
        lease_seconds=2,
    )
    assert first["status"] == "acquired"
    renewed = repo.renew_history_flight(
        flight_key=flight_key,
        owner_token="leader",
        generation=int(first["generation"]),
        lease_seconds=60,
    )
    assert renewed["ok"] is True
    blocked = repo.acquire_history_flight(
        flight_key=flight_key,
        stock_id="TW:2303",
        compatibility_key=compat,
        owner_token="other",
        lease_seconds=60,
    )
    assert blocked["status"] == "busy"
    stale_renew = repo.renew_history_flight(
        flight_key=flight_key,
        owner_token="other",
        generation=int(first["generation"]),
        lease_seconds=60,
    )
    assert stale_renew["stale"] is True


def test_postgres_schedule_tick_lock_blocks_second_owner() -> None:
    repo = TwResearchRepository()
    lock_name = f"schedule-test-{uuid4().hex[:8]}"
    first = repo.claim_schedule_tick_lock(
        lock_name=lock_name, owner_token="tick-a", lease_seconds=60
    )
    assert first["acquired"] is True
    second = repo.claim_schedule_tick_lock(
        lock_name=lock_name, owner_token="tick-b", lease_seconds=60
    )
    assert second["acquired"] is False
    renewed = repo.renew_schedule_tick_lock(
        lock_name=lock_name, owner_token="tick-a", lease_seconds=60
    )
    assert renewed["ok"] is True
    lost = repo.renew_schedule_tick_lock(
        lock_name=lock_name, owner_token="tick-b", lease_seconds=60
    )
    assert lost["stale"] is True
    repo.release_schedule_tick_lock(lock_name=lock_name, owner_token="tick-a")
    third = repo.claim_schedule_tick_lock(
        lock_name=lock_name, owner_token="tick-b", lease_seconds=60
    )
    assert third["acquired"] is True
    repo.release_schedule_tick_lock(lock_name=lock_name, owner_token="tick-b")


def test_postgres_backlog_prefers_older_incomplete_day() -> None:
    from app.services.tw_research.constants import CALCULATION_VERSION, UNIVERSE_PRESET_VERSION

    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    older = service.create_scan(
        UniverseSelector(mode="starter_preset"),
        trading_day="2024-06-27",
        user_id=DEFAULT_USER_ID,
        target_count=10,
    )
    newer = service.create_scan(
        UniverseSelector(mode="starter_preset"),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
        target_count=10,
    )
    backlog = repo.find_incomplete_backlog_scans(
        user_id=DEFAULT_USER_ID,
        universe_version=UNIVERSE_PRESET_VERSION,
        calculation_version=CALCULATION_VERSION,
        selector_mode="starter_preset",
        target_count=10,
        min_trading_day="2024-06-01",
        max_trading_day="2024-06-28",
    )
    assert backlog
    assert backlog[0]["scan_id"] == older.scan_id
    assert backlog[0]["trading_day"] == "2024-06-27"
    assert any(item["scan_id"] == newer.scan_id for item in backlog)


def test_postgres_stale_technical_write_cannot_overwrite_newer_result() -> None:
    """Test A: expired worker A cannot overwrite Worker B's technical result."""
    from app.db.models import TwResearchScan
    from app.db.session import SessionLocal
    from app.services.tw_research.persistence import load_scan_state

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
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert first is not None
    assert first["batch_ids"] == ["TW:2330"]
    stale_generation = int(first["lease_generation"])

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.reserve_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

    reclaimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert reclaimed is not None
    assert int(reclaimed["lease_generation"]) == stale_generation + 1
    winner_generation = int(reclaimed["lease_generation"])

    winner = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        daily={"passStatus": "pass", "source": "winner"},
        monthly={"passStatus": "pass"},
    )
    assert (
        persist_symbol(
            state,
            winner,
            repository=repo,
            scan_lease_generation=winner_generation,
        )
        is True
    )

    loser = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        daily={"passStatus": "fail", "source": "loser"},
    )
    assert (
        persist_symbol(
            state,
            loser,
            repository=repo,
            scan_lease_generation=stale_generation,
        )
        is False
    )
    assert "scan_lease_stale_write_rejected" in state.notes

    durable = load_scan_state(state.scan_id, repository=repo)
    assert durable is not None
    assert durable.records["TW:2330"].daily == {
        "passStatus": "pass",
        "source": "winner",
    }
    assert durable.records["TW:2330"].readiness == "technically_qualified"

    completed = repo.complete_scan_batch(
        state.scan_id,
        status="partial",
        lease_generation=winner_generation,
    )
    assert completed is not None
    assert completed["stale_lease"] is False
    assert int(completed["cursor"]) == 1


def test_postgres_active_technical_write_persists_and_completes() -> None:
    """Test B: valid worker persists under its active generation."""
    from app.services.tw_research.persistence import load_scan_state

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
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert claimed is not None
    generation = int(claimed["lease_generation"])
    for stock_id in claimed["batch_ids"]:
        record = SymbolScanRecord(
            stock_id=stock_id,
            stage="A",
            readiness="processed",
            daily={"passStatus": "fail"},
        )
        state.records[stock_id] = record
        assert (
            persist_symbol(
                state,
                record,
                repository=repo,
                scan_lease_generation=generation,
            )
            is True
        )
    completed = repo.complete_scan_batch(
        state.scan_id,
        status="completed_processing",
        lease_generation=generation,
    )
    assert completed is not None
    assert completed["stale_lease"] is False
    assert int(completed["cursor"]) == 2
    durable = load_scan_state(state.scan_id, repository=repo)
    assert durable is not None
    assert set(durable.records) == {"TW:2330", "TW:2454"}


def test_postgres_expired_lease_without_replacement_rejects_technical_write() -> None:
    """Test C: expired lease blocks writes even before a replacement claim."""
    from app.db.models import TwResearchScan
    from app.db.session import SessionLocal

    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert claimed is not None
    generation = int(claimed["lease_generation"])

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.reserve_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        daily={"passStatus": "fail"},
    )
    assert (
        persist_symbol(
            state,
            record,
            repository=repo,
            scan_lease_generation=generation,
        )
        is False
    )
    rows = repo.list_symbol_results(state.scan_id)
    assert rows == []


def test_postgres_technical_fencing_preserves_enrichment_and_rejects_stale_enrich() -> None:
    """Test D: technical/enrichment fencing remain independent."""
    from app.db.models import TwResearchScan
    from app.db.session import SessionLocal
    from app.services.tw_research.persistence import load_scan_state, persist_scan

    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert claimed is not None
    scan_generation = int(claimed["lease_generation"])
    technical = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    assert (
        persist_symbol(
            state,
            technical,
            repository=repo,
            scan_lease_generation=scan_generation,
        )
        is True
    )
    # Technical update must preserve any pre-existing enrichment (none yet).
    repo.complete_scan_batch(
        state.scan_id,
        status="completed_processing",
        lease_generation=scan_generation,
    )
    state.qualified_ids = ["TW:2330"]
    persist_scan(state, repository=repo, user_id=DEFAULT_USER_ID)

    enrich_claim = repo.claim_enrichment_batch(
        state.scan_id, batch_size=1, lease_seconds=60
    )
    assert enrich_claim is not None
    stale_enrich_generation = int(enrich_claim["lease_generation"])

    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.enrich_reserve_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

    reclaimed = repo.claim_enrichment_batch(
        state.scan_id, batch_size=1, lease_seconds=60
    )
    assert reclaimed is not None
    assert int(reclaimed["lease_generation"]) == stale_enrich_generation + 1
    winner = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
        enrichment={"qualified": True, "source": "winner"},
    )
    assert (
        persist_symbol(
            state,
            winner,
            repository=repo,
            enrich_lease_generation=int(reclaimed["lease_generation"]),
        )
        is True
    )
    loser = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
        enrichment={"qualified": True, "source": "loser"},
    )
    assert (
        persist_symbol(
            state,
            loser,
            repository=repo,
            enrich_lease_generation=stale_enrich_generation,
        )
        is True
    )
    assert loser.enrichment is None
    durable = load_scan_state(state.scan_id, repository=repo)
    assert durable is not None
    assert durable.records["TW:2330"].enrichment == {
        "qualified": True,
        "source": "winner",
    }
    assert durable.records["TW:2330"].daily["passStatus"] == "pass"

    # Active technical generation after complete is inactive — late technical
    # overwrite with the old scan generation must be rejected.
    assert (
        persist_symbol(
            state,
            SymbolScanRecord(
                stock_id="TW:2330",
                stage="A",
                readiness="processed",
                daily={"passStatus": "fail", "source": "late-stale"},
            ),
            repository=repo,
            scan_lease_generation=scan_generation,
        )
        is False
    )
    durable = load_scan_state(state.scan_id, repository=repo)
    assert durable is not None
    assert durable.records["TW:2330"].enrichment == {
        "qualified": True,
        "source": "winner",
    }
    assert durable.records["TW:2330"].daily.get("source") != "late-stale"


def test_postgres_crash_reclaim_retry_continues_safely() -> None:
    """Test E: replacement worker continues after crash/expiry without corruption."""
    from app.db.models import TwResearchScan
    from app.db.session import SessionLocal
    from app.services.tw_research.persistence import load_scan_state

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
    )
    first = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready"},
        max_in_flight=10,
        lease_seconds=60,
    )
    assert first is not None
    # Crash before persisting any symbol results: expire lease.
    assert SessionLocal is not None
    with SessionLocal() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.reserve_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

    replacement = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    # Service path: reclaim via run_batch after expiry.
    monkey_state = replacement.get_scan(state.scan_id)
    assert monkey_state is not None
    from zoneinfo import ZoneInfo

    finished = replacement.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=ZoneInfo("Asia/Taipei")),
    )
    assert finished.cursor >= 2
    assert len(finished.records) >= 2
    durable = load_scan_state(state.scan_id, repository=repo)
    assert durable is not None
    assert len(durable.records) >= 2
    # Stale first generation cannot complete.
    stale_complete = repo.complete_scan_batch(
        state.scan_id,
        status="partial",
        lease_generation=int(first["lease_generation"]),
    )
    assert stale_complete is not None
    assert stale_complete["stale_lease"] is True
