"""Disabled-by-default post-market research schedule (#330)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.tw_research.scheduler import (
    resolve_schedule_trading_day,
    run_scheduled_research,
)
from app.services.tw_research.scan import TwResearchScanService
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record

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


def test_schedule_disabled_skips_without_creating_scan() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=False,
        trading_day="2024-06-28",
        service=service,
    )
    assert result.outcome == "skipped_disabled"
    assert result.scan_id is None


def test_schedule_dry_run_does_not_create_scan() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        dry_run=True,
        trading_day="2024-06-28",
        service=service,
    )
    assert result.outcome == "dry_run"
    assert result.dry_run is True
    assert result.scan_id is None


def test_schedule_reuses_same_day_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    catalog = {f"TW:{n}": _tw(f"TW:{n}") for n in ("2330", "2454", "2303")}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    created = service.create_scan(
        UniverseSelector(mode="starter_preset"),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )

    def _list(user_id: str, limit: int = 20):  # noqa: ANN001
        del limit
        assert user_id == "schedule-owner"
        return [
            {
                "scanId": created.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": created.status,
            }
        ]

    monkeypatch.setattr(service, "list_scans", _list)
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=1,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert result.scan_id == created.scan_id
    assert result.outcome in {
        "reused_scan",
        "partial",
        "budget_exhausted",
        "completed_window",
        "technically_complete",
        "enrichment_partial",
        "verified_ready",
    }
    assert result.enrich_batches_run >= 0


def test_schedule_continues_across_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    ids = tuple(f"TW:{2300 + i}" for i in range(12))
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    created = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=ids),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )

    # Force starter-preset reuse path via list_scans while keeping the large universe.
    created.selector = UniverseSelector(mode="starter_preset")

    def _list(user_id: str, limit: int = 20):  # noqa: ANN001
        del limit
        return [
            {
                "scanId": created.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": created.status,
            }
        ]

    monkeypatch.setattr(service, "list_scans", _list)
    first = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=1,
        max_enrich_batches=0,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert first.scan_id == created.scan_id
    assert first.batches_run == 1
    mid = service.get_scan(created.scan_id)
    assert mid is not None
    assert mid.cursor < len(ids)

    second = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=5,
        max_enrich_batches=0,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert second.scan_id == created.scan_id
    done = service.get_scan(created.scan_id)
    assert done is not None
    assert done.cursor == len(ids) or done.status in {
        "completed_processing",
        "verified_ready",
        "budget_exhausted",
        "partial",
    }


def test_resolve_schedule_trading_day_skips_weekend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.is_trading_day",
        lambda market, value, allow_holiday_fetch=False: False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=False: date(2024, 6, 28),
    )
    day, skip = resolve_schedule_trading_day(
        now=datetime(2024, 6, 30, 10, 0, tzinfo=TZ),
        allow_holiday_fetch=False,
    )
    assert day == "2024-06-28"
    assert skip == "skipped_nontrading_day"


def test_schedule_73_symbol_universe_across_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    ids = tuple(f"TW:{2000 + i}" for i in range(73))
    catalog = {stock_id: _tw(stock_id) for stock_id in ids}
    fetches: list[str] = []

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del limit, max_month_fetches
        fetches.append(stock.id)
        return [], 0

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    created = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=ids),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    created.selector = UniverseSelector(mode="starter_preset")

    def _list(user_id: str, limit: int = 20):  # noqa: ANN001
        del user_id, limit
        state = service.get_scan(created.scan_id)
        assert state is not None
        return [
            {
                "scanId": created.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": state.status,
            }
        ]

    monkeypatch.setattr(service, "list_scans", _list)

    # Window 1: at most 50 symbols (5 batches × 10).
    first = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=5,
        max_enrich_batches=0,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert first.scan_id == created.scan_id
    assert first.batches_run == 5
    mid = service.get_scan(created.scan_id)
    assert mid is not None
    assert mid.cursor == 50
    assert len(mid.records) == 50
    first_fetch_count = len(fetches)

    # Simulated restart: new service process would hydrate from durable store;
    # here the in-memory service retains progress and list_scans reuses the id.
    second = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=5,
        max_enrich_batches=2,
        service=service,
        now=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
    )
    assert second.scan_id == created.scan_id
    done = service.get_scan(created.scan_id)
    assert done is not None
    assert done.cursor == 73
    assert len(done.records) == 73
    # Completed symbols are not re-fetched.
    assert len(fetches) == first_fetch_count + 23
    assert done.status in {"completed_processing", "verified_ready", "partial"}


def test_schedule_prefers_prior_day_incomplete_over_new_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    catalog = {f"TW:{n}": _tw(f"TW:{n}") for n in ("2330", "2454", "2303")}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    prior = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-27",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 27, 16, 0, tzinfo=TZ),
    )
    prior.selector = UniverseSelector(mode="starter_preset")
    prior = service.run_batch(
        prior.scan_id,
        batch_size=1,
        now=datetime(2024, 6, 27, 16, 0, tzinfo=TZ),
    )
    assert prior.cursor == 1

    def _list(user_id: str, limit: int = 20):  # noqa: ANN001
        del user_id, limit
        state = service.get_scan(prior.scan_id)
        assert state is not None
        return [
            {
                "scanId": prior.scan_id,
                "tradingDay": "2024-06-27",
                "selectorMode": "starter_preset",
                "status": state.status,
            }
        ]

    monkeypatch.setattr(service, "list_scans", _list)
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=2,
        max_enrich_batches=0,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert result.scan_id == prior.scan_id
    assert result.trading_day == "2024-06-27"
    assert any(note.startswith("backlog_resume:") for note in result.notes)
    finished = service.get_scan(prior.scan_id)
    assert finished is not None
    assert finished.cursor == 3


def test_verified_snapshot_with_pending_enrichment_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scan.preflight_tw_trading_calendars",
        lambda **_kwargs: {"ok": True, "missing": []},
    )
    from app.services.tw_research.scan import SymbolScanRecord

    catalog = {f"TW:{n}": _tw(f"TW:{n}") for n in ("2330", "2454")}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454")),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state.selector = UniverseSelector(mode="starter_preset")
    state.status = "verified_ready"
    state.quality_status = "verified_ready"
    state.active_snapshot_id = "snap-1"
    state.cursor = 2
    state.qualified_ids = ["TW:2330"]
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
    state.max_enrichments_per_run = 5
    state.enrichments_run = 0

    enrich_calls: list[str] = []

    def _enrich(scan_id: str, **kwargs):  # noqa: ANN001
        del kwargs
        enrich_calls.append(scan_id)
        record = state.records["TW:2330"]
        record.enrichment = {"ok": True}
        state.enrichments_run = 1
        return state

    monkeypatch.setattr(service, "run_enrichment_batch", _enrich)
    monkeypatch.setattr(
        service,
        "list_scans",
        lambda user_id, limit=20: [
            {
                "scanId": state.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": "verified_ready",
            }
        ],
    )

    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=0,
        max_enrich_batches=2,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert enrich_calls == [state.scan_id]
    assert "verified_snapshot_enrichment_pending" in result.notes
    # Enrichment completed for the only pending qualifier → schedule completion.
    assert result.outcome == "verified_ready"
    assert result.enrich_batches_run == 1


def test_verified_snapshot_enrichment_budget_exhausted_defers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.tw_research.constants import MAX_ENRICHMENTS_PER_SCAN_CAP
    from app.services.tw_research.scan import SymbolScanRecord

    catalog = {"TW:2330": _tw("TW:2330")}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state.selector = UniverseSelector(mode="starter_preset")
    state.status = "verified_ready"
    state.active_snapshot_id = "snap-1"
    state.qualified_ids = ["TW:2330"]
    # Hard cap already reached — no further enrichment windows may open.
    state.max_enrichments_per_run = MAX_ENRICHMENTS_PER_SCAN_CAP
    state.enrichments_run = MAX_ENRICHMENTS_PER_SCAN_CAP
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="technically_qualified",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    monkeypatch.setattr(
        service,
        "list_scans",
        lambda user_id, limit=20: [
            {
                "scanId": state.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": "verified_ready",
            }
        ],
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=1,
        max_enrich_batches=2,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert result.outcome == "enrichment_budget_exhausted"
    assert "enrichment_budget_exhausted" in result.notes
    assert "enrichment_cap_reached" in result.notes


def test_market_ohlcv_ready_requires_sample_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.tw_research.scheduler import market_ohlcv_ready_for_trading_day

    monkeypatch.setattr(
        "app.services.tw_research.scheduler.stored_history_covers_requirement",
        lambda stock, *, limit, expect_through: stock.id == "TW:2330",
    )
    assert market_ohlcv_ready_for_trading_day(
        "2024-06-28",
        sample_stock_ids=("TW:2454", "TW:2330"),
        stock_loader=lambda stock_id: _tw(stock_id),
    )
    assert not market_ohlcv_ready_for_trading_day(
        "2024-06-28",
        sample_stock_ids=("TW:2454", "TW:2303"),
        stock_loader=lambda stock_id: _tw(stock_id),
    )


def test_schedule_has_remaining_work_for_verified_pending_enrichment() -> None:
    from app.services.tw_research.scan import SymbolScanRecord
    from app.services.tw_research.scheduler import schedule_has_remaining_work

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
    )
    state.status = "verified_ready"
    state.active_snapshot_id = "snap-1"
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
    assert schedule_has_remaining_work(state) is True
    state.records["TW:2330"].enrichment = {"ok": True}
    assert schedule_has_remaining_work(state) is False
    state.records["TW:2330"].enrichment = None
    state.enrichments_run = 5
    # Spent window with pending work remains actionable via resume until hard cap.
    assert schedule_has_remaining_work(state) is True
    from app.services.tw_research.constants import MAX_ENRICHMENTS_PER_SCAN_CAP

    state.max_enrichments_per_run = MAX_ENRICHMENTS_PER_SCAN_CAP
    state.enrichments_run = MAX_ENRICHMENTS_PER_SCAN_CAP
    assert schedule_has_remaining_work(state) is False


def test_tick_heartbeat_renews_during_long_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix 2: heartbeat renews while work exceeds the original lease window."""
    import time

    from app.services.tw_research.scheduler import _TickLeaseHeartbeat

    renew_calls: list[float] = []

    class _Repo:
        def renew_schedule_tick_lock(self, **kwargs):  # noqa: ANN003
            del kwargs
            renew_calls.append(time.monotonic())
            return {"ok": True, "stale": False}

    with _TickLeaseHeartbeat(
        repo=_Repo(),
        lock_name="schedule:test",
        owner_token="owner-a",
        lease_seconds=2,
        interval_seconds=0.15,
    ) as heartbeat:
        time.sleep(0.55)
    assert heartbeat.lost is False
    assert len(renew_calls) >= 2


def test_tick_heartbeat_marks_lost_on_renew_failure() -> None:
    import time

    from app.services.tw_research.scheduler import _TickLeaseHeartbeat

    class _Repo:
        def renew_schedule_tick_lock(self, **kwargs):  # noqa: ANN003
            del kwargs
            raise RuntimeError("postgres unavailable")

    with _TickLeaseHeartbeat(
        repo=_Repo(),
        lock_name="schedule:test",
        owner_token="owner-a",
        lease_seconds=2,
        interval_seconds=0.1,
    ) as heartbeat:
        time.sleep(0.35)
    assert heartbeat.lost is True


def test_market_data_not_ready_defers_create_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.market_ohlcv_ready_for_trading_day",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.is_trading_day",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.tw_research.scheduler.last_completed_session_date",
        lambda *a, **k: date(2024, 6, 28),
    )
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=False,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day=None,
        max_batches=1,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=True,
    )
    assert result.outcome == "skipped_market_data_not_ready"
    assert result.scan_id is None


def test_durable_schedule_fails_closed_when_repo_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Repo:
        def is_available(self) -> bool:
            return False

    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        _Repo(),
    )
    catalog = {"TW:2330": _tw("TW:2330")}
    fetches: list[str] = []

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del limit, max_month_fetches
        fetches.append(stock.id)
        return [], 0

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=True,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=1,
        service=service,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert result.outcome == "deferred_lock"
    assert "schedule_coordination_unavailable" in result.notes
    assert result.scan_id is None
    assert fetches == []


def test_enrichment_window_resume_across_schedule_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.tw_research.enrichment import EnrichmentResult, ProvenanceField
    from app.services.tw_research.scan import SymbolScanRecord

    catalog = {f"TW:{n}": _tw(f"TW:{n}") for n in ("2330", "2454", "2303")}
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330", "TW:2454", "TW:2303")),
        trading_day="2024-06-28",
        target_count=10,
        user_id="schedule-owner",
        max_enrichments_per_run=1,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    state.selector = UniverseSelector(mode="starter_preset")
    state.status = "verified_ready"
    state.active_snapshot_id = "snap-1"
    state.cursor = 3
    state.qualified_ids = ["TW:2330", "TW:2454", "TW:2303"]
    for stock_id in state.qualified_ids:
        state.records[stock_id] = SymbolScanRecord(
            stock_id=stock_id,
            stage="B",
            readiness="technically_qualified",
            daily={"passStatus": "pass"},
            monthly={"passStatus": "pass"},
        )

    def _enrich(stock_id: str, **kwargs):  # noqa: ANN001
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

    # Spend the first enrichment window.
    state = service.run_enrichment_batch(state.scan_id, batch_size=1, enricher=_enrich)
    assert state.enrichments_run == 1
    assert state.records["TW:2454"].enrichment is None

    monkeypatch.setattr(
        service,
        "list_scans",
        lambda user_id, limit=20: [
            {
                "scanId": state.scan_id,
                "tradingDay": "2024-06-28",
                "selectorMode": "starter_preset",
                "status": "verified_ready",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.tw_research.scan.enrich_qualified_symbol",
        _enrich,
    )
    result = run_scheduled_research(
        user_id="schedule-owner",
        enabled=True,
        trading_day="2024-06-28",
        max_batches=0,
        max_enrich_batches=3,
        service=service,
        now=datetime(2024, 6, 28, 17, 0, tzinfo=TZ),
        require_market_data=False,
    )
    assert "enrichment_window_resumed" in result.notes
    done = service.get_scan(state.scan_id)
    assert done is not None
    assert all(done.records[sid].enrichment is not None for sid in done.qualified_ids)
    assert done.max_enrichments_per_run > 1
    assert result.outcome == "verified_ready"
