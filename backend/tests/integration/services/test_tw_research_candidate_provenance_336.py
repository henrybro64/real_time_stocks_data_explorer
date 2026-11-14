"""PostgreSQL round-trip for candidate-set → technical scan provenance (#336)."""

from __future__ import annotations

import pytest

from app.db.init_ids import DEFAULT_USER_ID
from app.repositories.tw_research_repository import TwResearchRepository, tw_research_repository
from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
from app.services.tw_research.first_pass_durable import (
    CandidateSetValidationError,
    create_technical_scan_from_candidate_set,
    persist_first_pass_batch,
    submit_candidate_set,
)
from app.services.tw_research.persistence import load_scan_state
from app.services.tw_research.scan import TwResearchDurableStoreError, TwResearchScanService
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record


def _tw(stock_id: str):
    symbol = stock_id.split(":", 1)[1]
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=symbol,
        name_zh_tw=symbol,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _seed_snapshot_and_candidates(
    *,
    user_id: str,
    stock_ids: tuple[str, ...],
    candidate_ids: tuple[str, ...],
):
    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version="integration-u1",
        requested_count=len(stock_ids),
        collected_count=len(stock_ids),
        deduped_count=len(stock_ids),
        records=[
            FirstPassSymbolRecord(
                stock_id=sid,
                stock_name=sid,
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
    snap = persist_first_pass_batch(
        batch, user_id=user_id, repository=tw_research_repository
    )
    candidates = submit_candidate_set(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=list(candidate_ids),
        origin="analyst",
        expected_version=0,
        repository=tw_research_repository,
    )
    return snap, candidates


def test_postgres_candidate_technical_scan_provenance_round_trip() -> None:
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = DEFAULT_USER_ID
    snap, candidates = _seed_snapshot_and_candidates(
        user_id=user_id,
        stock_ids=("TW:2330", "TW:2454", "TW:2303"),
        candidate_ids=("TW:2330", "TW:2454"),
    )
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    result = create_technical_scan_from_candidate_set(
        user_id=user_id,
        expected_version=int(candidates["version"]),
        repository=tw_research_repository,
        scan_service=service,
    )
    scan = result["scan"]
    assert list(scan.stock_ids) == ["TW:2330", "TW:2454"]
    assert "TW:2303" not in scan.stock_ids
    assert scan.candidate_provenance is not None
    assert scan.candidate_provenance["candidateSetId"] == candidates["candidateSetId"]
    assert scan.candidate_provenance["candidateSetVersion"] == candidates["version"]
    assert scan.candidate_provenance["firstPassSnapshotId"] == snap.snapshot_id
    assert scan.candidate_provenance["firstPassDigest"] == snap.content_digest
    assert scan.candidate_provenance["approvedStockIds"] == ["TW:2330", "TW:2454"]
    assert "calculationVersion" in scan.candidate_provenance

    # Reload through the durable path (not process cache).
    reloaded = load_scan_state(scan.scan_id, repository=TwResearchRepository())
    assert reloaded is not None
    assert list(reloaded.stock_ids) == ["TW:2330", "TW:2454"]
    assert reloaded.candidate_provenance == scan.candidate_provenance
    assert any("candidate set" in note.lower() for note in reloaded.notes)
    assert reloaded.user_id == user_id


def test_postgres_candidate_scan_catalog_mismatch_creates_no_row() -> None:
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = DEFAULT_USER_ID
    _snap, candidates = _seed_snapshot_and_candidates(
        user_id=user_id,
        stock_ids=("TW:2330", "TW:2454"),
        candidate_ids=("TW:2330", "TW:2454"),
    )
    before = tw_research_repository.list_scans_for_user(user_id, limit=50)
    before_ids = {row["scan_id"] for row in before}

    def loader(stock_id: str):
        if stock_id == "TW:2454":
            return None
        return _tw(stock_id)

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=loader,
        persist=True,
    )
    with pytest.raises(CandidateSetValidationError) as raised:
        create_technical_scan_from_candidate_set(
            user_id=user_id,
            expected_version=int(candidates["version"]),
            repository=tw_research_repository,
            scan_service=service,
        )
    assert raised.value.detail["error"] == "candidate_universe_mismatch"
    after = tw_research_repository.list_scans_for_user(user_id, limit=50)
    after_ids = {row["scan_id"] for row in after}
    assert after_ids == before_ids


def test_postgres_candidate_scan_persist_failure_fails_closed() -> None:
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

    user_id = DEFAULT_USER_ID
    _snap, candidates = _seed_snapshot_and_candidates(
        user_id=user_id,
        stock_ids=("TW:2330", "TW:2454"),
        candidate_ids=("TW:2330",),
    )
    before_ids = {
        row["scan_id"]
        for row in tw_research_repository.list_scans_for_user(user_id, limit=50)
    }

    class BoomService(TwResearchScanService):
        def _persist_scan(self, state, *, create: bool = False):  # type: ignore[no-untyped-def]
            del create
            raise TwResearchDurableStoreError(
                "Durable scan write failed.",
                scan_id=state.scan_id,
                operation="persist_scan",
            )

    service = BoomService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    with pytest.raises(TwResearchDurableStoreError):
        create_technical_scan_from_candidate_set(
            user_id=user_id,
            expected_version=int(candidates["version"]),
            repository=tw_research_repository,
            scan_service=service,
        )
    after_ids = {
        row["scan_id"]
        for row in tw_research_repository.list_scans_for_user(user_id, limit=50)
    }
    assert after_ids == before_ids


def test_postgres_technical_first_scan_without_candidate_provenance() -> None:
    if not tw_research_repository.is_available():
        pytest.skip("tw_research durable store unavailable")

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
    assert state.candidate_provenance is None
    reloaded = load_scan_state(state.scan_id)
    assert reloaded is not None
    assert reloaded.candidate_provenance is None
    assert list(reloaded.stock_ids) == ["TW:2330"]
