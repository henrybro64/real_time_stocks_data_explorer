"""Hardening coverage for #318 / #319 / #320."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.repositories.tw_research_repository import TwResearchRepository
from app.services.tw_research.coverage import (
    derive_readiness,
    validate_closed_month_coverage,
    validate_ohlcv_series,
)
from app.services.tw_research.export import (
    build_export_chunk_from_rows,
    build_immutable_manifest,
)
from app.services.tw_research.persistence import (
    finalize_verified_snapshot,
    load_scan_state,
    persist_scan,
    persist_symbol,
)
from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
from app.services.tw_research.universe import (
    UniverseSelector,
    deterministic_universe_digest,
    resolve_universe,
)
from tests.support import install_in_memory_sessionlocal, stock_record


TZ = ZoneInfo("Asia/Taipei")


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


def _candle(day: date, close: float = 100.0) -> CandleRecord:
    stamp = datetime(day.year, day.month, day.day, 13, 30, tzinfo=TZ)
    return CandleRecord(
        time=stamp.isoformat(),
        open=close - 0.5,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=1000,
    )


def test_deterministic_universe_digest_stable_across_reorder_and_duplicates() -> None:
    a = deterministic_universe_digest(
        mode="stock_ids",
        stock_ids=("TW:2618", "2727", "TW:2330", "TW:2618"),
    )
    b = deterministic_universe_digest(
        mode="stock_ids",
        stock_ids=("TW:2330", "TW:2727", "TW:2618"),
    )
    assert a == b
    assert len(a) == 64

    catalog = {
        "TW:2727": _tw("TW:2727"),
        "TW:2618": _tw("TW:2618"),
        "TW:2330": _tw("TW:2330"),
    }
    first = resolve_universe(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2618", "TW:2727", "TW:2330", "TW:2618"),
        ),
        stock_resolver=catalog.get,
    )
    second = resolve_universe(
        UniverseSelector(
            mode="stock_ids",
            stock_ids=("TW:2330", "TW:2727", "TW:2618"),
        ),
        stock_resolver=catalog.get,
    )
    assert first.universe_version == second.universe_version
    assert first.universe_version.startswith("explicit:3:")


def test_missing_trading_day_blocks_verified_coverage(monkeypatch) -> None:
    # Build Mon–Fri series for a short window, then drop one real weekday.
    days = [
        date(2024, 6, 3),
        date(2024, 6, 4),
        date(2024, 6, 5),
        date(2024, 6, 6),
        date(2024, 6, 7),
    ]
    candles = [_candle(day, close=100 + index) for index, day in enumerate(days)]
    # Remove Wednesday.
    candles = [candle for candle in candles if not candle.time.startswith("2024-06-05")]

    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.is_trading_day",
        lambda market, day, allow_holiday_fetch=False: day.weekday() < 5,
    )
    report = validate_ohlcv_series(
        candles,
        market="TW",
        require_min_bars=3,
        check_calendar_gaps=True,
    )
    assert report.ok is False
    assert report.reason == "missing_trading_sessions"
    assert "2024-06-05" in report.missing_trading_days


def test_calendar_preflight_reports_missing_years(monkeypatch) -> None:
    from app.services.tw_research.coverage import (
        preflight_tw_trading_calendars,
        required_tw_calendar_years,
    )

    years = required_tw_calendar_years("2024-06-28")
    assert years[0] <= 2020
    assert years[-1] == 2024

    captured: dict[str, object] = {}

    def _fake_preload(requested, allow_network=True, max_fetches=4):
        captured["years"] = list(requested)
        captured["max_fetches"] = max_fetches
        return {
            "ok": False,
            "loaded": [2023],
            "missing": [2024],
            "fetched": 1,
        }

    monkeypatch.setattr(
        "app.services.tw_research.coverage.preload_tw_holiday_calendars",
        _fake_preload,
    )
    result = preflight_tw_trading_calendars(
        trading_day="2024-06-28",
        allow_network=True,
    )
    assert result["ok"] is False
    assert 2024 in result["missing"]
    assert captured["max_fetches"] == len(years)


def test_technical_snapshot_then_enriched_snapshot_preserves_prior(monkeypatch) -> None:
    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2727",)),
        trading_day="2024-06-28",
        user_id="user-1",
    )
    record = SymbolScanRecord(
        stock_id="TW:2727",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    state.records["TW:2727"] = record
    state.qualified_ids = ["TW:2727"]
    state.cursor = 1
    state.status = "completed_processing"
    state.quality_status = "technically_qualified"
    persist_scan(state, repository=repo, user_id="user-1")
    persist_symbol(state, record, repository=repo)
    repo.complete_scan_batch(state.scan_id, status="completed_processing")

    first = finalize_verified_snapshot(
        state,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
    )
    assert first is not None
    assert first["manifest"]["technicalStatus"] == "complete"
    assert first["manifest"]["enrichmentStatus"] == "not_started"
    first_digest = first["contentDigest"]
    first_snapshot = repo.get_export_snapshot(first["snapshotId"])
    assert first_snapshot is not None

    record.enrichment = {"qualified": True, "fields": {"peg": {"value": 1.2}}}
    persist_symbol(state, record, repository=repo)
    second = finalize_verified_snapshot(
        state,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 18, 0, tzinfo=TZ),
    )
    assert second is not None
    assert second["snapshotId"] != first["snapshotId"]
    assert second["contentDigest"] != first_digest
    assert second["manifest"]["enrichmentStatus"] == "complete"
    # Prior immutable snapshot bytes stay unchanged.
    still_first = repo.get_export_snapshot(first["snapshotId"])
    assert still_first is not None
    assert still_first["content_digest"] == first_digest
    assert still_first["ledger_json"] == first_snapshot["ledger_json"]
    latest = repo.latest_verified_snapshot(
        universe_version=state.universe_version,
        trading_day="2024-06-28",
        user_id="user-1",
    )
    assert latest is not None
    assert latest["snapshot_id"] == second["snapshotId"]


def test_enrichment_lease_recovers_after_abandonment(monkeypatch) -> None:
    from datetime import timezone as tz

    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        user_id="user-1",
    )
    for stock_id in ("TW:2330", "TW:2454"):
        record = SymbolScanRecord(
            stock_id=stock_id,
            stage="B",
            readiness="verified_ready",
            daily={"passStatus": "pass"},
            monthly={"passStatus": "pass"},
        )
        state.records[stock_id] = record
        persist_symbol(state, record, repository=repo)
    state.qualified_ids = ["TW:2330", "TW:2454"]
    persist_scan(state, repository=repo, user_id="user-1")

    claimed = repo.claim_enrichment_batch(state.scan_id, batch_size=2, lease_seconds=60)
    assert claimed is not None
    assert claimed["batch_ids"] == ["TW:2330", "TW:2454"]
    blocked = repo.claim_enrichment_batch(state.scan_id, batch_size=2)
    assert blocked is not None
    assert blocked["lease_held"] is True

    from app.db.models import TwResearchScan

    with factory() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.enrich_reserve_expires_at = datetime(2020, 1, 1, tzinfo=tz.utc)
        session.commit()

    recovered = repo.claim_enrichment_batch(state.scan_id, batch_size=2)
    assert recovered is not None
    assert recovered["batch_ids"] == ["TW:2330", "TW:2454"]


def test_insufficient_history_counted_separately_from_technical_fail() -> None:
    from app.services.tw_research.export import build_export_manifest
    from app.services.tw_research.scan import ScanState

    state = ScanState(
        scan_id="scan-counts",
        trading_day="2024-06-28",
        universe_version="explicit:2:abc",
        calculation_version="macd12-26-9_kd9-3-3_v1",
        selector=UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        stock_ids=("TW:2330", "TW:2454"),
        cursor=2,
        status="completed_processing",
        quality_status="source_incomplete",
    )
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="A",
        readiness="processed",
        daily={"passStatus": "fail"},
    )
    state.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="A",
        readiness="source_incomplete",
        daily={"passStatus": "unknown"},
        coverage={"daily": {"ok": False, "reason": "insufficient_history"}},
    )
    manifest = build_export_manifest(state, generated_at="2024-06-28T17:00:00+00:00")
    assert manifest["counts"]["insufficientHistory"] == 1
    assert manifest["counts"]["sourceIncomplete"] == 1
    assert manifest["counts"]["qualifiedCount"] == 0
    assert manifest["technicalStatus"] == "complete"
    assert manifest["sourceCompleteness"] == "partial"
    assert manifest["enrichmentStatus"] == "unavailable"


def test_unknown_trading_calendar_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: False,
    )
    candles = [_candle(date(2024, 6, day), close=100 + day) for day in range(3, 8)]
    report = validate_ohlcv_series(
        candles,
        market="TW",
        require_min_bars=3,
        check_calendar_gaps=True,
    )
    assert report.ok is False
    assert report.reason == "trading_calendar_unavailable"


def test_incomplete_closed_month_cannot_verify(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.is_trading_day",
        lambda market, day, allow_holiday_fetch=False: day.weekday() < 5,
    )
    # May 2024 weekdays minus one day → incomplete closed month.
    days = []
    probe = date(2024, 5, 1)
    while probe.month == 5:
        if probe.weekday() < 5:
            days.append(probe)
        probe += timedelta(days=1)
    days = days[:-1]
    candles = [_candle(day, close=90 + index) for index, day in enumerate(days)]
    report = validate_closed_month_coverage(
        candles,
        year=2024,
        month=5,
        market="TW",
    )
    assert report.ok is False
    assert report.reason == "incomplete_closed_month"
    assert report.missing_trading_days


def test_derive_readiness_distinguishes_coverage_from_signals() -> None:
    assert (
        derive_readiness(
            stage="B",
            daily_pass="pass",
            monthly_pass="pass",
            daily_coverage_ok=True,
            monthly_coverage_ok=True,
        )
        == "verified_ready"
    )
    assert (
        derive_readiness(
            stage="B",
            daily_pass="pass",
            monthly_pass="pass",
            daily_coverage_ok=True,
            monthly_coverage_ok=False,
        )
        == "source_incomplete"
    )
    assert (
        derive_readiness(
            stage="B",
            daily_pass="pass",
            monthly_pass="pass",
            daily_coverage_ok=True,
            monthly_coverage_ok=True,
            coverage_checked=False,
        )
        == "technically_qualified"
    )
    assert (
        derive_readiness(
            stage="A",
            daily_pass="fail",
            monthly_pass=None,
            daily_coverage_ok=True,
            monthly_coverage_ok=True,
        )
        == "processed"
    )


def test_immutable_chunk_digests_are_stable_for_frozen_timestamp() -> None:
    from app.services.tw_research.export import stable_chunk_checksum

    rows = [
        {"stockId": "TW:2330", "stage": "B", "readiness": "verified_ready"},
        {"stockId": "TW:2454", "stage": "A", "readiness": "processed"},
    ]
    frozen = "2024-06-28T16:00:00+08:00"
    first = build_export_chunk_from_rows(
        rows,
        chunk_index=0,
        chunk_size=25,
        kind="ledger",
        scan_id="scan-1",
        trading_day="2024-06-28",
        frozen_at=frozen,
    )
    second = build_export_chunk_from_rows(
        rows,
        chunk_index=0,
        chunk_size=25,
        kind="ledger",
        scan_id="scan-DIFFERENT",
        trading_day="2024-06-28",
        frozen_at="2099-01-01T00:00:00+00:00",
    )
    assert first["checksumSha256"] == second["checksumSha256"]
    assert first["checksumSha256"] == stable_chunk_checksum(
        rows=rows,
        chunk_index=0,
        chunk_size=25,
        kind="ledger",
        trading_day="2024-06-28",
        total_rows=len(rows),
    )


def test_content_digest_ignores_timestamps_and_scan_uuids(monkeypatch) -> None:
    from app.services.tw_research.export import stable_export_content_digest

    rows = [{"stockId": "TW:2330", "stage": "A", "readiness": "processed", "daily": None}]
    digests = {"0": "abc"}
    first = stable_export_content_digest(
        rows=rows,
        chunk_digests=digests,
        universe_version="explicit:1:deadbeef",
        trading_day="2024-06-28",
        quality_status="verified_ready",
    )
    second = stable_export_content_digest(
        rows=rows,
        chunk_digests=digests,
        universe_version="explicit:1:deadbeef",
        trading_day="2024-06-28",
        quality_status="verified_ready",
    )
    assert first == second

    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state_a = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id="user-a",
    )
    state_b = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id="user-b",
    )
    for state in (state_a, state_b):
        state.records["TW:2330"] = SymbolScanRecord(
            stock_id="TW:2330",
            stage="A",
            readiness="processed",
            daily={"passStatus": "fail"},
        )
        state.cursor = 1
        state.status = "completed_processing"
        state.quality_status = "technically_qualified"
        persist_scan(state, repository=repo, user_id=state.user_id)
        persist_symbol(state, state.records["TW:2330"], repository=repo)

    finalized_a = finalize_verified_snapshot(
        state_a,
        repository=repo,
        user_id="user-a",
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
    )
    finalized_b = finalize_verified_snapshot(
        state_b,
        repository=repo,
        user_id="user-b",
        finalized_at=datetime(2024, 6, 29, 9, 0, tzinfo=TZ),
    )
    assert finalized_a is not None and finalized_b is not None
    assert finalized_a["contentDigest"] == finalized_b["contentDigest"]
    # Same digest, but per-owner snapshot rows stay authorization-isolated.
    assert finalized_a["snapshotId"] != finalized_b["snapshotId"]


def test_zero_qualifier_scan_can_finalize(monkeypatch) -> None:
    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id="user-1",
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
    persist_scan(state, repository=repo, user_id="user-1")
    persist_symbol(state, state.records["TW:2330"], repository=repo)
    finalized = finalize_verified_snapshot(
        state,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
    )
    assert finalized is not None
    assert state.status == "verified_ready"
    assert finalized["manifest"]["counts"]["verifiedReady"] == 0


def test_stale_upsert_does_not_regress_cursor(monkeypatch) -> None:
    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        user_id="user-1",
    )
    persist_scan(state, repository=repo, user_id="user-1")
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
    )
    assert claimed is not None
    # Persist symbol results for the reserved slice, then complete.
    for stock_id in claimed["batch_ids"]:
        record = SymbolScanRecord(stock_id=stock_id, stage="A", readiness="processed")
        persist_symbol(state, record, repository=repo)
    completed = repo.complete_scan_batch(state.scan_id, status="running")
    assert completed is not None
    assert completed["cursor"] == 2

    # Stale worker still thinks cursor is 0 — upsert must not rewind progress.
    from app.services.tw_research.persistence import scan_row_payload

    state.cursor = 0
    stale = scan_row_payload(state, user_id="user-1")
    stale["status"] = "running"
    stale["upstream_month_fetches"] = 0
    repo.upsert_scan(stale)
    row = repo.get_scan(state.scan_id)
    assert row is not None
    assert row["cursor"] == 2



def test_postgres_persistence_round_trip_and_atomic_finalize(monkeypatch) -> None:
    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)

    from app.repositories import tw_research_repository as repo_module
    from app.services.tw_research import persistence as persistence_module

    repo_module.tw_research_repository.set_session_factory_override(factory)
    persistence_module.tw_research_repository.set_session_factory_override(factory)

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2727", "TW:2618")),
        trading_day="2024-06-28",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        user_id="user-1",
    )

    persist_scan(state, repository=repo, user_id="user-1")
    record = SymbolScanRecord(
        stock_id="TW:2727",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
        checkpoint=(
            state.trading_day,
            state.universe_version,
            state.calculation_version,
            "TW:2727",
        ),
    )
    state.records["TW:2727"] = record
    state.records["TW:2618"] = SymbolScanRecord(
        stock_id="TW:2618",
        stage="A",
        readiness="processed",
        daily={"passStatus": "fail"},
    )
    state.status = "completed_processing"
    state.quality_status = "technically_qualified"
    persist_scan(state, repository=repo, user_id="user-1")
    persist_symbol(state, record, repository=repo)
    persist_symbol(state, state.records["TW:2618"], repository=repo)
    # Cursor is owned by claim/complete — not by blind upsert.
    completed = repo.complete_scan_batch(state.scan_id, status="completed_processing")
    assert completed is not None
    assert completed["cursor"] == 2

    reloaded = load_scan_state(state.scan_id, repository=repo)
    assert reloaded is not None
    assert reloaded.cursor == 2
    assert reloaded.records["TW:2727"].readiness == "verified_ready"
    assert reloaded.quality_status == "technically_qualified"

    # Not verified at scan level until finalize succeeds.
    assert reloaded.status != "verified_ready" or reloaded.active_snapshot_id is None

    finalized = finalize_verified_snapshot(
        reloaded,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
    )
    assert finalized is not None
    assert finalized["contentDigest"]
    assert reloaded.status == "verified_ready"
    assert reloaded.quality_status == "verified_ready"
    assert reloaded.active_snapshot_id == finalized["snapshotId"]

    # Incomplete coverage may still publish when labeled partial.
    incomplete = load_scan_state(state.scan_id, repository=repo)
    assert incomplete is not None
    incomplete.records["TW:2727"].readiness = "source_incomplete"
    incomplete.status = "completed_processing"
    incomplete.quality_status = "source_incomplete"
    partial = finalize_verified_snapshot(
        incomplete,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 18, 0, tzinfo=TZ),
    )
    assert partial is not None
    assert partial["manifest"]["sourceCompleteness"] == "partial"
    assert partial["snapshotId"] != finalized["snapshotId"]

    latest = repo.latest_verified_snapshot(
        universe_version=state.universe_version,
        trading_day="2024-06-28",
        user_id="user-1",
    )
    assert latest is not None
    assert latest["snapshot_id"] == partial["snapshotId"]
    assert latest["quality_status"] == "verified_ready"

    # Durable batch claim reports complete once cursor is exhausted.
    claimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=1,
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
    )
    assert claimed is not None
    assert claimed["already_complete"] is True

    # Deleting the scan must not cascade-delete the immutable snapshot.
    assert repo.delete_scans_older_than(datetime(2099, 1, 1, tzinfo=TZ)) >= 1
    assert repo.get_export_snapshot(finalized["snapshotId"]) is not None

    # Restart simulation: clear in-memory cache; scan row is gone after delete.
    service._scans.clear()
    assert service.get_scan(state.scan_id) is None


def test_theme_universe_version_stays_within_column_limit() -> None:
    from dataclasses import dataclass

    keys = tuple(f"tw-theme-{index:02d}-long-name-for-column-pressure" for index in range(14))

    @dataclass
    class _Theme:
        key: str

    @dataclass
    class _Group:
        theme: _Theme
        stocks: tuple = ()

    class FakeThemes:
        def get_stocks_for_theme(self, theme_key: str):
            return _Group(theme=_Theme(key=theme_key), stocks=())

    resolved = resolve_universe(
        UniverseSelector(mode="theme_keys", theme_keys=keys),
        stock_resolver=lambda stock_id: None,
        theme_service=FakeThemes(),  # type: ignore[arg-type]
    )
    assert resolved.universe_version.startswith("themes:14:")
    assert len(resolved.universe_version) <= 128
    # Old concatenated form would exceed the persisted String(128) column.
    assert len("themes:" + ",".join(keys) + ":deadbeefdeadbeef") > 128


def test_expect_through_flags_trailing_history_gap(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.is_trading_day",
        lambda market, day, allow_holiday_fetch=False: day.weekday() < 5,
    )
    candles = [_candle(date(2024, 5, day), close=100 + day) for day in range(2, 32) if date(2024, 5, day).weekday() < 5]
    report = validate_ohlcv_series(
        candles,
        market="TW",
        require_min_bars=3,
        check_calendar_gaps=True,
        expect_through=date(2024, 6, 28),
    )
    assert report.ok is False
    assert report.reason == "missing_trading_sessions"
    assert any(day.startswith("2024-06-") for day in report.missing_trading_days)


def test_truncated_leading_month_is_trimmed_for_coverage(monkeypatch) -> None:
    from app.services.tw_research.coverage import trim_truncated_leading_months
    from app.services.tw_research.indicators import assemble_monthly_candles

    monkeypatch.setattr(
        "app.services.tw_research.coverage.ensure_tw_holiday_calendars_loaded",
        lambda years: True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.is_trading_day",
        lambda market, day, allow_holiday_fetch=False: day.weekday() < 5,
    )
    # Start mid-April, then complete May weekdays.
    days = [date(2024, 4, day) for day in range(15, 31) if date(2024, 4, day).weekday() < 5]
    probe = date(2024, 5, 1)
    while probe.month == 5:
        if probe.weekday() < 5:
            days.append(probe)
        probe += timedelta(days=1)
    candles = [_candle(day, close=90 + index) for index, day in enumerate(days)]
    monthly = assemble_monthly_candles(
        candles, now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ)
    )
    trimmed = trim_truncated_leading_months(candles, monthly, market="TW")
    assert trimmed
    assert not any(bar.time.startswith("2024-04") for bar in trimmed)


def test_abandoned_batch_lease_is_reclaimed(monkeypatch) -> None:
    from datetime import timezone as tz

    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        user_id="user-1",
    )
    persist_scan(state, repository=repo, user_id="user-1")

    first = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
        lease_seconds=60,
    )
    assert first is not None
    assert first["batch_ids"] == ["TW:2330", "TW:2454"]
    assert first["scan"]["cursor"] == 0  # completed watermark unchanged

    # Lease still held → peer cannot claim overlapping work.
    blocked = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
    )
    assert blocked is not None
    assert blocked["lease_held"] is True
    assert blocked["batch_ids"] == []

    # Expire the lease without persisting symbol results → reclaim from the gap.
    from app.db.models import TwResearchScan

    with factory() as session:
        row = session.get(TwResearchScan, state.scan_id)
        assert row is not None
        row.reserve_expires_at = datetime(2020, 1, 1, tzinfo=tz.utc)
        session.commit()

    reclaimed = repo.claim_scan_batch(
        state.scan_id,
        batch_size=2,
        terminal_statuses={"completed_processing", "verified_ready", "budget_exhausted"},
    )
    assert reclaimed is not None
    assert reclaimed["batch_ids"] == ["TW:2330", "TW:2454"]


def test_persist_failure_marks_scan_failed(monkeypatch) -> None:
    from app.services.tw_research.persistence import persist_scan as persist_scan_fn
    from app.services.tw_research.scan import ScanState

    state = ScanState(
        scan_id="scan-persist-fail",
        trading_day="2024-06-28",
        universe_version="explicit:1:abc",
        calculation_version="macd12-26-9_kd9-3-3_v1",
        selector=UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        stock_ids=("TW:2330",),
        status="running",
        quality_status="processed",
    )

    class BrokenRepo:
        def is_available(self) -> bool:
            return True

        def upsert_scan(self, payload):  # noqa: ANN001
            raise RuntimeError("disk full")

    assert persist_scan_fn(state, repository=BrokenRepo()) is False  # type: ignore[arg-type]
    assert state.status == "failed"
    assert any(note.startswith("persist_failed:") for note in state.notes)


def test_immutable_manifest_content_digest_stable() -> None:
    from app.services.tw_research.scan import ScanState
    from app.services.tw_research.universe import UniverseSelector

    state = ScanState(
        scan_id="scan-stable",
        trading_day="2024-06-28",
        universe_version="explicit:1:abc",
        calculation_version="macd12-26-9_kd9-3-3_v1",
        selector=UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        stock_ids=("TW:2330",),
        status="verified_ready",
        quality_status="verified_ready",
    )
    rows = [{"stockId": "TW:2330", "stage": "B", "readiness": "verified_ready"}]
    digests = {"0": "deadbeef"}
    first = build_immutable_manifest(
        state,
        rows=rows,
        chunk_digests=digests,
        frozen_at="2024-06-28T17:00:00+00:00",
        quality_status="verified_ready",
    )
    second = build_immutable_manifest(
        state,
        rows=rows,
        chunk_digests=digests,
        frozen_at="2024-06-28T17:00:00+00:00",
        quality_status="verified_ready",
    )
    assert first["contentDigest"] == second["contentDigest"]
