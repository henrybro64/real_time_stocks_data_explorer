"""PostgreSQL-backed multi-window schedule E2E for #329 / #330 (Fix 5).

Requires ``DATABASE_URL``. Uses deterministic OHLCV fixtures — never live
market-data providers. Asserts exact outcomes rather than accepting
``partial`` / ``verified_ready`` interchangeably when one result is required.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.db.init_ids import DEFAULT_USER_ID
from app.models.quote import CandleRecord
from app.repositories.tw_research_repository import TwResearchRepository
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    SCAN_BATCH_SIZE,
    UNIVERSE_PRESET_VERSION,
)
from app.services.tw_research.enrichment import EnrichmentResult, ProvenanceField
from app.services.tw_research.preset import starter_preset_stock_ids
from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
from app.services.tw_research.scheduler import run_scheduled_research
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")
OWNER = DEFAULT_USER_ID


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


def _candles_from_closes(closes: list[float]) -> list[CandleRecord]:
    end = datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
    dates: list[datetime] = []
    offset = 0
    while len(dates) < len(closes):
        day = end - timedelta(days=offset)
        offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day.replace(hour=13, minute=30, second=0, microsecond=0))
    dates.reverse()
    out: list[CandleRecord] = []
    for close, stamp in zip(closes, dates, strict=True):
        out.append(
            CandleRecord(
                time=stamp.isoformat(),
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000,
            )
        )
    return out


def _qualifying_history() -> list[CandleRecord]:
    # Flat base + late rally → dual-pass daily+monthly MACD/KD.
    closes = [100.0] * 700 + [100.0 + (i**1.2) * 0.05 for i in range(1, 101)]
    return _candles_from_closes(closes)


def _nonqualifying_history() -> list[CandleRecord]:
    # Steady decline → definitive daily fail (not unknown).
    closes = [200.0 - i * 0.05 for i in range(800)]
    return _candles_from_closes(closes)


def _short_incomplete_history() -> list[CandleRecord]:
    return _candles_from_closes([100.0 + i for i in range(5)])


def _service(*, history_loader, catalog: dict[str, object]) -> TwResearchScanService:
    return TwResearchScanService(
        history_loader=history_loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=True,
    )


def _fake_enrich(stock_id: str, **kwargs):  # noqa: ANN001
    del kwargs
    return EnrichmentResult(
        stock_id=stock_id,
        qualified=True,
        fields={
            "peg": ProvenanceField(
                field="peg",
                status="unavailable",
                source="none",
                reason="verified_eps_growth_denominator_unavailable",
            )
        },
    )


def _patch_calendar_coverage_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep real MACD/KD evaluation; stub calendar-gap checks for fixtures."""
    from app.services.tw_research.coverage import CoverageReport

    def _ok_report(candles, **kwargs):  # noqa: ANN001
        del kwargs
        return CoverageReport(
            ok=True,
            reason=None,
            bar_count=len(candles or []),
            missing_trading_days=(),
            duplicate_dates=(),
            invalid_bars=0,
            price_basis="unadjusted_close",
        )

    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.validate_ohlcv_series",
        _ok_report,
    )
    monkeypatch.setattr(
        "app.services.tw_research.coverage.monthly_series_verified",
        lambda daily, monthly, **kwargs: _ok_report(monthly, **kwargs),
    )


def test_postgres_73_symbol_full_research_across_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario A: 73-symbol dual-pass fixtures, enrichment, immutable export."""
    _patch_calendar_coverage_ok(monkeypatch)
    monkeypatch.setattr(
        "app.services.tw_research.scan.enrich_qualified_symbol",
        _fake_enrich,
    )
    ids = starter_preset_stock_ids()
    assert len(ids) == 73
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    qualify = set(ids[:5])
    pass_hist = _qualifying_history()
    fail_hist = _nonqualifying_history()
    fetches: list[str] = []

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del limit, max_month_fetches
        fetches.append(stock.id)
        return list(pass_hist if stock.id in qualify else fail_hist), 0

    service = _service(history_loader=loader, catalog=catalog)
    first = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=5,
        max_enrich_batches=0,
        target_count=10,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert first.scan_id is not None
    assert first.batches_run == 5
    scan_id = first.scan_id
    mid = service.get_scan(scan_id)
    assert mid is not None
    assert mid.cursor == 5 * SCAN_BATCH_SIZE
    assert len(mid.records) == 50
    first_fetch_count = len(fetches)

    restarted = _service(history_loader=loader, catalog=catalog)
    second = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=5,
        max_enrich_batches=0,
        target_count=10,
        service=restarted,
        now=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert second.scan_id == scan_id
    technical = restarted.get_scan(scan_id)
    assert technical is not None
    assert technical.cursor == 73
    assert len(technical.records) == 73
    assert len(fetches) == first_fetch_count + 23
    assert technical.status in {"completed_processing", "verified_ready"}
    for stock_id in qualify:
        assert technical.records[stock_id].readiness in {
            "technically_qualified",
            "verified_ready",
        }
        assert (technical.records[stock_id].daily or {}).get("passStatus") == "pass"
        assert (technical.records[stock_id].monthly or {}).get("passStatus") == "pass"
    assert set(technical.qualified_ids) == qualify
    # Non-qualifiers are definitive fails, not padded recommendations.
    for stock_id in ids[5:15]:
        assert (technical.records[stock_id].daily or {}).get("passStatus") == "fail"
        assert stock_id not in technical.qualified_ids

    enrich_service = _service(history_loader=loader, catalog=catalog)
    enrich = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=0,
        max_enrich_batches=2,
        target_count=10,
        service=enrich_service,
        now=datetime(2024, 6, 28, 18, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert enrich.scan_id == scan_id
    assert enrich.enrich_batches_run >= 1
    final = enrich_service.get_scan(scan_id)
    assert final is not None
    assert final.status == "verified_ready"
    assert final.active_snapshot_id is not None
    assert all(
        final.records[sid].enrichment is not None for sid in final.qualified_ids
    )
    snapshot_id = final.active_snapshot_id
    repo = TwResearchRepository()
    snap = repo.get_export_snapshot(snapshot_id)
    assert snap is not None
    assert snap["quality_status"] == "verified_ready"
    manifest = snap.get("manifest_json") or snap.get("manifest") or {}
    counts = manifest.get("counts") or {}
    assert int(counts.get("technicallyQualified") or 0) == 5
    # Immutable: further tick must not create a duplicate or mutate the snapshot.
    idle = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=5,
        max_enrich_batches=2,
        target_count=10,
        service=_service(history_loader=loader, catalog=catalog),
        now=datetime(2024, 6, 28, 19, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert idle.scan_id == scan_id
    assert idle.batches_run == 0
    assert idle.outcome == "verified_ready"
    assert len(fetches) == first_fetch_count + 23
    again = repo.get_export_snapshot(snapshot_id)
    assert again is not None
    assert again["content_digest"] == snap["content_digest"]


def test_postgres_schedule_incomplete_source_not_fully_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario B: unavailable history stays incomplete; no fabricated recommendations."""
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    ids = ("TW:2330", "TW:2454", "TW:2303", "TW:2317", "TW:2382")
    monkeypatch.setattr(
        "app.services.tw_research.preset.starter_preset_stock_ids",
        lambda: ids,
    )
    monkeypatch.setattr(
        "app.services.tw_research.universe.starter_preset_stock_ids",
        lambda: ids,
    )
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    unavailable = {"TW:2454", "TW:2317"}
    history = _short_incomplete_history()

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del limit, max_month_fetches
        if stock.id in unavailable:
            return [], 0
        return list(history), 0

    service = _service(history_loader=loader, catalog=catalog)
    result = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=2,
        max_enrich_batches=1,
        target_count=10,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert result.scan_id is not None
    state = service.get_scan(result.scan_id)
    assert state is not None
    assert state.cursor == 5
    assert len(state.records) == 5
    for stock_id in unavailable:
        record = state.records[stock_id]
        assert record.readiness in {"source_incomplete", "processed", "rejected"}
        assert (record.daily or {}).get("passStatus") != "pass"
    progress = service.get_scan_progress(state.scan_id, user_id=OWNER)
    assert progress is not None
    processed = int(progress["processedCount"])
    definitive = int(progress["definitiveEvaluatedCount"])
    assert processed == 5
    assert definitive <= processed
    assert len(state.qualified_ids) == 0


def test_postgres_schedule_restart_and_concurrency_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario C: tick lock exclusivity, same-day reuse, backlog prioritization."""
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    ids = ("TW:2330", "TW:2454", "TW:2303")
    monkeypatch.setattr(
        "app.services.tw_research.preset.starter_preset_stock_ids",
        lambda: ids,
    )
    monkeypatch.setattr(
        "app.services.tw_research.universe.starter_preset_stock_ids",
        lambda: ids,
    )
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    service = _service(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        catalog=catalog,
    )

    prior = service.create_scan(
        UniverseSelector(mode="starter_preset"),
        trading_day="2024-06-27",
        target_count=10,
        user_id=OWNER,
        now=datetime(2024, 6, 27, 16, 0, tzinfo=TZ),
    )
    prior = service.run_batch(
        prior.scan_id,
        batch_size=1,
        now=datetime(2024, 6, 27, 16, 0, tzinfo=TZ),
    )
    assert prior.cursor == 1

    resumed = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=2,
        max_enrich_batches=0,
        target_count=10,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert resumed.scan_id == prior.scan_id
    assert resumed.trading_day == "2024-06-27"
    assert any(note.startswith("backlog_resume:") for note in resumed.notes)
    finished_prior = service.get_scan(prior.scan_id)
    assert finished_prior is not None
    assert finished_prior.cursor == 3

    today = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=1,
        max_enrich_batches=0,
        target_count=10,
        service=service,
        now=datetime(2024, 6, 28, 16, 30, tzinfo=TZ),
        require_market_data=False,
    )
    assert today.scan_id is not None
    today_id = today.scan_id
    reused = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=2,
        max_enrich_batches=0,
        target_count=10,
        service=service,
        now=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert reused.scan_id == today_id

    repo = TwResearchRepository()
    lock_name = f"schedule:{OWNER}:starter_preset:{CALCULATION_VERSION}:t10"
    held = repo.claim_schedule_tick_lock(
        lock_name=lock_name,
        owner_token=f"holder-{uuid4().hex[:8]}",
        lease_seconds=30,
    )
    assert held["acquired"] is True

    def _tick() -> str:
        other = _service(
            history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
            catalog=catalog,
        )
        outcome = run_scheduled_research(
            user_id=OWNER,
            enabled=True,
            trading_day="2024-06-28",
            max_batches=1,
            max_enrich_batches=0,
            target_count=10,
            service=other,
            now=datetime(2024, 6, 28, 18, 0, tzinfo=TZ),
            require_market_data=False,
        )
        return outcome.outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: _tick(), range(2)))
    assert outcomes.count("deferred_lock") == 2
    repo.release_schedule_tick_lock(
        lock_name=lock_name,
        owner_token=held["holder_token"],
    )


def test_postgres_prior_day_verified_pending_enrichment_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2: verified_ready with pending enrichment is backlog-selectable."""
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.enrich_qualified_symbol",
        _fake_enrich,
    )
    ids = ("TW:2330", "TW:2454")
    monkeypatch.setattr(
        "app.services.tw_research.preset.starter_preset_stock_ids",
        lambda: ids,
    )
    monkeypatch.setattr(
        "app.services.tw_research.universe.starter_preset_stock_ids",
        lambda: ids,
    )
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    service = _service(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        catalog=catalog,
    )
    state = service.create_scan(
        UniverseSelector(mode="starter_preset"),
        trading_day="2024-06-27",
        target_count=10,
        user_id=OWNER,
        now=datetime(2024, 6, 27, 16, 0, tzinfo=TZ),
    )
    state.status = "verified_ready"
    state.quality_status = "verified_ready"
    state.active_snapshot_id = "snap-prior"
    state.cursor = 2
    state.qualified_ids = ["TW:2330"]
    state.max_enrichments_per_run = 5
    state.enrichments_run = 0
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    state.records["TW:2454"] = SymbolScanRecord(
        stock_id="TW:2454",
        stage="A",
        readiness="processed",
        daily={"passStatus": "fail"},
    )
    from app.services.tw_research.persistence import persist_scan, persist_symbol

    assert persist_scan(state, user_id=OWNER)
    for record in state.records.values():
        assert persist_symbol(state, record)

    repo = TwResearchRepository()
    backlog = repo.find_incomplete_backlog_scans(
        user_id=OWNER,
        universe_version=UNIVERSE_PRESET_VERSION,
        calculation_version=CALCULATION_VERSION,
        selector_mode="starter_preset",
        target_count=10,
        min_trading_day="2024-06-01",
        max_trading_day="2024-06-28",
    )
    assert any(item["scan_id"] == state.scan_id for item in backlog)

    result = run_scheduled_research(
        user_id=OWNER,
        enabled=True,
        trading_day="2024-06-28",
        max_batches=0,
        max_enrich_batches=2,
        target_count=10,
        service=_service(
            history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
            catalog=catalog,
        ),
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert result.scan_id == state.scan_id
    assert result.trading_day == "2024-06-27"
    assert result.enrich_batches_run >= 1
    done = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=True,
    ).get_scan(state.scan_id)
    assert done is not None
    assert done.records["TW:2330"].enrichment is not None


def test_postgres_enrichment_window_resume_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    ids = ("TW:2330", "TW:2454", "TW:2303")
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=ids),
        trading_day="2024-06-28",
        target_count=10,
        user_id=OWNER,
        max_enrichments_per_run=1,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state.status = "verified_ready"
    state.qualified_ids = list(ids)
    state.enrichments_run = 1
    for stock_id in ids:
        state.records[stock_id] = SymbolScanRecord(
            stock_id=stock_id,
            stage="B",
            readiness="technically_qualified",
            daily={"passStatus": "pass"},
            monthly={"passStatus": "pass"},
            enrichment={"ok": True} if stock_id == "TW:2330" else None,
        )
    from app.services.tw_research.persistence import persist_scan, persist_symbol

    assert persist_scan(state, user_id=OWNER)
    for record in state.records.values():
        assert persist_symbol(state, record)

    repo = TwResearchRepository()
    first = repo.resume_enrichment_window(
        state.scan_id, additional_enrichments=20, max_cap=100
    )
    assert first is not None and first["ok"] is True
    assert first["new_max"] == 21
    second = repo.resume_enrichment_window(
        state.scan_id, additional_enrichments=20, max_cap=100
    )
    assert second is not None and second["ok"] is False
    assert second["code"] == "enrichment_budget_remaining"
