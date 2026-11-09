"""PostgreSQL concurrency / restart coverage for TW research (#318–#320)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from app.db.init_ids import DEFAULT_USER_ID
from app.repositories.tw_research_repository import TwResearchRepository
from app.services.tw_research.persistence import (
    finalize_verified_snapshot,
    load_scan_state,
    persist_scan,
    persist_symbol,
    scan_row_payload,
)
from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
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


def test_postgres_stale_upsert_does_not_regress_claimed_cursor() -> None:
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
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
    )
    assert claimed is not None
    for stock_id in claimed["batch_ids"]:
        persist_symbol(
            state,
            SymbolScanRecord(stock_id=stock_id, stage="A", readiness="processed"),
            repository=repo,
        )
    completed = repo.complete_scan_batch(state.scan_id, status="running")
    assert completed is not None
    assert completed["cursor"] == 2

    state.cursor = 0
    stale = scan_row_payload(state, user_id=DEFAULT_USER_ID)
    repo.upsert_scan(stale)
    row = repo.get_scan(state.scan_id)
    assert row is not None
    assert row["cursor"] == 2


def test_postgres_lease_expiry_recovers_unfinished_batch() -> None:
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
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
        lease_seconds=60,
    )
    assert first is not None
    assert first["batch_ids"] == ["TW:2330", "TW:2454"]

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
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
    )
    assert reclaimed is not None
    assert reclaimed["batch_ids"] == ["TW:2330", "TW:2454"]


def test_postgres_restart_reloads_scan_and_finalizes_zero_qualifiers() -> None:
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
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        daily={"passStatus": "fail"},
    )
    state.cursor = 1
    state.status = "completed_processing"
    state.quality_status = "technically_qualified"
    persist_scan(state, repository=repo, user_id=DEFAULT_USER_ID)
    persist_symbol(state, state.records["TW:2330"], repository=repo)

    service._scans.clear()
    reloaded = service.get_scan(state.scan_id)
    assert reloaded is not None
    assert reloaded.records["TW:2330"].readiness == "processed"

    finalized = finalize_verified_snapshot(
        reloaded,
        repository=repo,
        user_id=DEFAULT_USER_ID,
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=timezone.utc),
    )
    assert finalized is not None
    assert finalized["manifest"]["counts"]["verifiedReady"] == 0


def test_postgres_enrichment_lease_recovers_and_avoids_overlap() -> None:
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
    for stock_id in state.stock_ids:
        record = SymbolScanRecord(
            stock_id=stock_id,
            stage="B",
            readiness="verified_ready",
            daily={"passStatus": "pass"},
            monthly={"passStatus": "pass"},
        )
        state.records[stock_id] = record
        persist_symbol(state, record, repository=repo)
    state.qualified_ids = list(state.stock_ids)
    persist_scan(state, repository=repo, user_id=DEFAULT_USER_ID)

    first = repo.claim_enrichment_batch(state.scan_id, batch_size=2, lease_seconds=120)
    assert first is not None
    assert len(first["batch_ids"]) == 2
    assert first["lease_generation"] == 1
    blocked = repo.claim_enrichment_batch(state.scan_id, batch_size=2)
    assert blocked is not None
    assert blocked["lease_held"] is True
    assert blocked["lease_generation"] == 1

    for stock_id in first["batch_ids"]:
        record = state.records[stock_id]
        record.enrichment = {"qualified": True, "fields": {}}
        persist_symbol(
            state,
            record,
            repository=repo,
            enrich_lease_generation=first["lease_generation"],
        )
    completed = repo.complete_enrichment_batch(
        state.scan_id, lease_generation=first["lease_generation"]
    )
    assert completed is not None
    assert completed["enrichments_run"] == 2
    assert completed["stale_lease"] is False

    second = repo.claim_enrichment_batch(state.scan_id, batch_size=2)
    assert second is not None
    assert second["batch_ids"] == ["TW:2303"]
    assert second["lease_generation"] == 2


def test_postgres_stale_enrichment_lease_cannot_overwrite() -> None:
    from app.db.models import TwResearchScan

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
    record = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    state.records["TW:2330"] = record
    state.qualified_ids = ["TW:2330"]
    persist_scan(state, repository=repo, user_id=DEFAULT_USER_ID)
    persist_symbol(state, record, repository=repo)

    first = repo.claim_enrichment_batch(state.scan_id, batch_size=1, lease_seconds=120)
    assert first is not None
    stale_generation = int(first["lease_generation"])

    with repo._session_factory()() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.enrich_reserve_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        session.commit()

    reclaimed = repo.claim_enrichment_batch(state.scan_id, batch_size=1, lease_seconds=120)
    assert reclaimed is not None
    assert reclaimed["lease_generation"] == stale_generation + 1

    winner = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
        enrichment={"qualified": True, "source": "winner"},
    )
    persist_symbol(
        state,
        winner,
        repository=repo,
        enrich_lease_generation=reclaimed["lease_generation"],
    )
    loser = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
        enrichment={"qualified": True, "source": "loser"},
    )
    persist_symbol(
        state,
        loser,
        repository=repo,
        enrich_lease_generation=stale_generation,
    )
    assert loser.enrichment is None

    stale_complete = repo.complete_enrichment_batch(
        state.scan_id, lease_generation=stale_generation
    )
    assert stale_complete is not None
    assert stale_complete["stale_lease"] is True
    scan = repo.get_scan(state.scan_id)
    assert scan is not None
    assert scan["enrich_reserve_ids_json"] == ["TW:2330"]

    durable = load_scan_state(state.scan_id, repository=repo)
    assert durable is not None
    assert durable.records["TW:2330"].enrichment == {
        "qualified": True,
        "source": "winner",
    }


def test_postgres_concurrent_claims_do_not_overlap() -> None:
    repo = TwResearchRepository()
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    ids = tuple(f"TW:{2300 + index}" for index in range(20))
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=ids),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )

    def _claim(_: int) -> tuple[str, ...]:
        claimed = repo.claim_scan_batch(
            state.scan_id,
            batch_size=5,
            terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
            lease_seconds=120,
        )
        if claimed is None or claimed.get("lease_held"):
            return tuple()
        batch = tuple(claimed.get("batch_ids") or ())
        for stock_id in batch:
            persist_symbol(
                state,
                SymbolScanRecord(stock_id=stock_id, stage="A", readiness="processed"),
                repository=repo,
            )
        repo.complete_scan_batch(state.scan_id, status="running")
        return batch

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_claim, index) for index in range(8)]
        batches = [future.result() for future in as_completed(futures)]

    claimed_ids = [stock_id for batch in batches for stock_id in batch]
    assert len(claimed_ids) == len(set(claimed_ids))
    reloaded = load_scan_state(state.scan_id, repository=repo)
    assert reloaded is not None
    assert reloaded.cursor == len(set(claimed_ids))
