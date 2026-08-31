"""Unit tests for rankings EOD archive capture service (#167)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import threading
import time
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.archive_models import ArchiveBase
from app.repositories.rankings_archive_repository import RankingsArchiveRepository
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services.rankings_archive_service import RankingsArchiveService


class _FakeEnvelope:
    def __init__(self, snapshot_id: int = 1, item_count: int = 1) -> None:
        self.snapshot = type("S", (), {"id": snapshot_id})()
        self.items = [object()] * item_count


class _FakeRepo:
    def __init__(self) -> None:
        self.available = True
        self.schema_ok = True
        self.existing: set[tuple[str, str, str, date]] = set()
        self.upserts: list[tuple[str, str, str, date]] = []
        self.upsert_error: Exception | None = None
        self.load_calls = 0
        self.exists_calls = 0
        self.purge_calls: list[date] = []
        self.item_counts: dict[tuple[str, str, str, date], int] = {}

    def is_available(self) -> bool:
        return self.available

    def is_configured(self) -> bool:
        return True

    def ensure_schema(self) -> bool:
        return self.schema_ok

    def has_eod_snapshot(self, *, market, universe, ranking_type, trading_date):
        self.exists_calls += 1
        key = (market, universe, ranking_type.value, trading_date)
        return key in self.existing

    def list_existing_eod_trading_dates(
        self,
        *,
        market,
        universe,
        ranking_type,
        trading_dates,
    ):
        self.exists_calls += 1
        ranking_type_value = ranking_type.value
        return {
            trading_date
            for trading_date in trading_dates
            if (market, universe, ranking_type_value, trading_date) in self.existing
        }

    def list_eod_snapshot_keys_in_range(self, *, min_trading_date, max_trading_date):
        self.exists_calls += 1
        return {
            key
            for key in self.existing
            if min_trading_date <= key[3] <= max_trading_date
        }

    def load_eod_snapshot(self, *, market, universe, ranking_type, trading_date):
        self.load_calls += 1
        key = (market, universe, ranking_type.value, trading_date)
        if key in self.existing:
            return _FakeEnvelope()
        return None

    def upsert_eod_snapshot(
        self,
        *,
        market,
        universe,
        ranking_type,
        trading_date,
        response,
        captured_at=None,
        capture_scope="end_of_day",
    ):
        if self.upsert_error is not None:
            raise self.upsert_error
        key = (market, universe, ranking_type.value, trading_date)
        if key in self.existing:
            return _FakeEnvelope()
        self.existing.add(key)
        self.upserts.append(key)
        self.item_counts[key] = len(response.items)
        return _FakeEnvelope(item_count=len(response.items))

    def purge_snapshots_older_than(self, *, cutoff_trading_date, batch_size=200):
        self.purge_calls.append(cutoff_trading_date)
        removed = [key for key in list(self.existing) if key[3] < cutoff_trading_date]
        items_deleted = 0
        for key in removed:
            self.existing.discard(key)
            items_deleted += self.item_counts.pop(key, 1)
        return {
            "cutoff_date": cutoff_trading_date.isoformat(),
            "snapshots_deleted": len(removed),
            "items_deleted": items_deleted,
            "dry_run": 0,
        }

    def count_snapshots_older_than(self, *, cutoff_trading_date):
        matched = [key for key in self.existing if key[3] < cutoff_trading_date]
        return {
            "cutoff_date": cutoff_trading_date.isoformat(),
            "snapshots_deleted": len(matched),
            "items_deleted": sum(self.item_counts.get(key, 1) for key in matched),
            "dry_run": 1,
        }


class _FakeProvider:
    def __init__(self, response: RankingResponse | Exception) -> None:
        self._response = response
        self.fetch_calls: list[dict] = []

    def fetch(self, query, *, limit, now, allow_hardcoded_fallback=True):
        self.fetch_calls.append(
            {
                "limit": limit,
                "allow_hardcoded_fallback": allow_hardcoded_fallback,
                "query": query,
            }
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeRegistry:
    def __init__(self, response: RankingResponse | Exception) -> None:
        self._provider = _FakeProvider(response)

    def get_provider(self, query):
        return self._provider


def _us_item(*, item_timestamp: str, symbol: str = "AAPL", name: str = "Apple") -> RankingItem:
    return RankingItem(
        rank=1,
        stock=StockResponse(
            id=f"US:{symbol}",
            market="US",
            symbol=symbol,
            name=name,
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        ),
        lastPrice=190.0,
        changePercent=1.0,
        volume=100,
        volumeDeltaPercent=None,
        netBuySellShares=None,
        peRatio=None,
        peRatioType=None,
        peRatioSource=None,
        valuationAsOf=None,
        valuationWarning=None,
        timestamp=item_timestamp,
    )


def _response(**overrides) -> RankingResponse:
    payload = dict(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        universe="DJIA",
        items=[_us_item(item_timestamp="2026-08-18T16:00:00-04:00")],
        timestamp="2026-08-18T20:00:00+00:00",
        source="unit-test",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="closed",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope="DJIA",
        twRankingsScope=None,
    )
    payload.update(overrides)
    return RankingResponse(**payload)


def _us_session_response(session_day: date) -> RankingResponse:
    """US quote payload whose item stamps prove ``session_day`` in America/New_York."""
    item_ts = f"{session_day.isoformat()}T16:00:00-04:00"
    envelope_ts = f"{session_day.isoformat()}T20:00:00+00:00"
    return _response(timestamp=envelope_ts, items=[_us_item(item_timestamp=item_ts)])


def _tw_institutional_response(*, stamp: str) -> RankingResponse:
    return _response(
        market="TW",
        rankingType=RankingType.net_buy,
        twBoard="TWSE",
        universe="TWSE",
        usRankingsScope=None,
        twRankingsScope="institutional",
        sourceType=RankingSourceType.free_public,
        timestamp=stamp,
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id="TW:2330",
                    market="TW",
                    symbol="2330",
                    name="TSMC",
                    exchange="TWSE",
                    currency="TWD",
                    timezone="Asia/Taipei",
                ),
                lastPrice=100.0,
                changePercent=1.0,
                volume=None,
                volumeDeltaPercent=None,
                netBuySellShares=1000,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp=stamp,
            )
        ],
    )


def test_eod_archive_queries_cover_ui_universes() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    queries = service.eod_archive_queries()
    keys = {(q.market, q.universe, q.ranking_type) for q in queries}
    assert ("US", "DJIA", RankingType.volume) in keys
    assert ("US", "NASDAQ100", RankingType.valuation_low_pe) in keys
    assert ("TW", "TWSE", RankingType.net_buy) in keys
    assert ("TW", "TPEx", RankingType.net_sell) in keys
    assert ("US", "DJIA", RankingType.net_buy) not in keys
    assert len(queries) == 20


def test_ready_trading_date_respects_us_settle_delay() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    ny = ZoneInfo("America/New_York")
    before = datetime(2026, 8, 18, 16, 30, tzinfo=ny)
    after = datetime(2026, 8, 18, 16, 50, tzinfo=ny)
    assert service.ready_trading_date("US", before) == date(2026, 8, 17)
    assert service.ready_trading_date("US", after) == date(2026, 8, 18)


def test_ready_trading_date_respects_tw_institutional_publish_time() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    tp = ZoneInfo("Asia/Taipei")
    early = datetime(2026, 8, 18, 17, 0, tzinfo=tp)
    late = datetime(2026, 8, 18, 18, 5, tzinfo=tp)
    assert (
        service.ready_trading_date("TW", early, ranking_type=RankingType.net_buy)
        == date(2026, 8, 17)
    )
    assert (
        service.ready_trading_date("TW", late, ranking_type=RankingType.net_buy)
        == date(2026, 8, 18)
    )


def test_capture_skips_when_archive_unavailable() -> None:
    repo = _FakeRepo()
    repo.available = False
    service = RankingsArchiveService(repository=repo, provider_registry=_FakeRegistry(_response()))
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert result["captured"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 1
    assert result["had_missing"] == 0
    assert result["still_missing"] == 0
    assert repo.upserts == []


def test_capture_is_idempotent_and_isolates_provider_failures() -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    now = datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc)
    first = service.capture_eod_archives(
        now=now,
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    second = service.capture_eod_archives(
        now=now,
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    # US only: 2 universes × 4 ranking types = 8
    assert first["captured"] == 8
    assert second["captured"] == 0
    assert second["skipped"] >= 8

    failing = RankingsArchiveService(
        repository=_FakeRepo(),
        provider_registry=_FakeRegistry(RuntimeError("upstream down")),
    )
    failed = failing.capture_eod_archives(
        now=now,
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert failed["failed"] == 8
    assert failed["captured"] == 0


def test_capture_disables_hardcoded_fallback_and_skips_fallback_scope() -> None:
    repo = _FakeRepo()
    registry = _FakeRegistry(
        _response(
            market="TW",
            rankingType=RankingType.volume,
            twBoard="TWSE",
            universe="TWSE",
            usRankingsScope=None,
            twRankingsScope="fallback",
            source="Limited hard-coded TWSE/TPEx quote fallback",
        )
    )
    service = RankingsArchiveService(repository=repo, provider_registry=registry)
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
        market="TW",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert registry._provider.fetch_calls
    assert all(
        call["allow_hardcoded_fallback"] is False for call in registry._provider.fetch_calls
    )
    assert result["captured"] == 0
    assert result["skipped"] >= 1
    assert repo.upserts == []


def test_capture_skips_us_holiday_trading_date() -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    # New Year's Day 2026 is a US market holiday (Thursday).
    result = service.capture_eod_archives(
        now=datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc),
        market="US",
        trading_date=date(2026, 1, 1),
        force=True,
    )
    assert result["captured"] == 0
    assert result["skipped"] >= 8
    assert repo.upserts == []


def test_ready_trading_date_skips_us_new_years_holiday() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    ny = ZoneInfo("America/New_York")
    # After settle on New Year's Day — must not treat the holiday as EOD-ready.
    after_holiday_close = datetime(2026, 1, 1, 17, 0, tzinfo=ny)
    ready = service.ready_trading_date("US", after_holiday_close)
    assert ready != date(2026, 1, 1)
    assert ready == date(2025, 12, 31)


def test_capture_rejects_prior_date_when_provider_timestamps_mismatch() -> None:
    """Never stamp today's live quotes under yesterday's archive key (#178)."""
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    # Tuesday morning ET: target Monday, but live item stamps are Tuesday.
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc),  # 10:00 ET Tue
        market="US",
        trading_date=date(2026, 8, 17),
        force=True,
    )
    assert result["captured"] == 0
    assert result["skipped"] >= 8
    assert repo.upserts == []


def test_capture_backfills_prior_day_when_provider_date_matches() -> None:
    repo = _FakeRepo()
    monday = date(2026, 8, 17)
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_us_session_response(monday)),
    )
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        trading_date=monday,
        force=True,
    )
    assert result["captured"] == 8
    assert all(key[3] == monday for key in repo.upserts)


def test_capture_weekend_backfill_uses_friday_provider_session() -> None:
    repo = _FakeRepo()
    friday = date(2026, 8, 21)
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_us_session_response(friday)),
    )
    # Saturday morning ET — Friday is settled; provider still exposes Friday stamps.
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc),
        market="US",
        trading_date=friday,
        force=True,
    )
    assert result["captured"] == 8
    assert all(key[3] == friday for key in repo.upserts)


def test_capture_tw_institutional_validates_report_date() -> None:
    from app.services.market_clock import tw_institutional_daily_bucket_for_date

    repo = _FakeRepo()
    report_date = date(2026, 8, 17)
    stamp = (
        tw_institutional_daily_bucket_for_date(report_date)
        .astimezone(ZoneInfo("Asia/Taipei"))
        .isoformat()
    )
    matching = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_tw_institutional_response(stamp=stamp)),
    )
    ok = matching.capture_eod_archives(
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
        market="TW",
        trading_date=report_date,
        force=True,
    )
    # Only net_buy/net_sell queries for TWSE+TPEx can match this response type;
    # quote ranking types will date-mismatch or still attempt — registry returns
    # the same payload for every query. Count net_* upserts only.
    institutional_upserts = [
        key
        for key in repo.upserts
        if key[2] in {RankingType.net_buy.value, RankingType.net_sell.value}
    ]
    assert ok["captured"] >= 1
    assert institutional_upserts
    assert all(key[3] == report_date for key in institutional_upserts)

    mismatched = RankingsArchiveService(
        repository=_FakeRepo(),
        provider_registry=_FakeRegistry(_tw_institutional_response(stamp=stamp)),
    )
    bad = mismatched.capture_eod_archives(
        now=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
        market="TW",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert bad["captured"] == 0


def test_capture_skips_unsupported_historical_provider() -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(
            _response(
                items=[],
                sourceType=RankingSourceType.unsupported,
                unsupportedReason="not available",
            )
        ),
    )
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert result["captured"] == 0
    assert result["skipped"] >= 8
    assert repo.upserts == []


def test_capture_skips_empty_us_response_when_yahoo_auth_denies_all_paths() -> None:
    """Empty DJIA/NASDAQ100 payload after Yahoo 401 must not write archive rows (#182)."""
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(
            _response(
                items=[],
                sourceType=RankingSourceType.free_unofficial,
                source="DJIA constituent quote rankings",
                usRankingsScope="DJIA",
                universe="DJIA",
            )
        ),
    )
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert result["captured"] == 0
    assert result["skipped"] >= 8
    assert result["failed"] == 0
    assert repo.upserts == []


def test_capture_auto_backfill_picks_newest_missing_settled_day() -> None:
    """Sampler path (no explicit date) recovers the newest missing settled day."""
    repo = _FakeRepo()
    # Tuesday already archived; Monday missing — provider still returns Monday.
    for universe in ("DJIA", "NASDAQ100"):
        for ranking_type in (
            RankingType.volume,
            RankingType.valuation_low_pe,
            RankingType.change_up,
            RankingType.change_down,
        ):
            repo.existing.add(("US", universe, ranking_type.value, date(2026, 8, 18)))

    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_us_session_response(date(2026, 8, 17))),
    )
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        force=False,
    )
    assert result["captured"] == 8
    assert all(key[3] == date(2026, 8, 17) for key in repo.upserts)


def test_capture_auto_backfill_remaps_older_provider_date_onto_missing_candidate() -> None:
    """One fetch whose payload proves Monday still fills Monday when Tuesday is also missing."""
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_us_session_response(date(2026, 8, 17))),
    )
    # Wednesday ET after settle: Tue+Mon missing; provider still exposes Monday.
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 19, 20, 50, tzinfo=timezone.utc),
        market="US",
        force=False,
    )
    assert result["captured"] == 8
    assert all(key[3] == date(2026, 8, 17) for key in repo.upserts)


def test_infer_provider_date_tolerates_stale_constituent_quote() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    response = _response(
        items=[
            _us_item(item_timestamp="2026-08-18T16:00:00-04:00"),
            _us_item(
                item_timestamp="2026-08-17T16:00:00-04:00",
                symbol="HALT",
                name="Halted Co",
            ),
        ],
        timestamp="2026-08-18T20:00:00+00:00",
    )
    # Adjust rank on second item for clarity; inference only cares about stamps.
    response.items[1] = response.items[1].model_copy(update={"rank": 2, "lastPrice": 10.0, "volume": 0})
    inferred = service._infer_provider_trading_date(
        response,
        market="US",
        ranking_type=RankingType.volume,
    )
    assert inferred == date(2026, 8, 18)


def test_capture_uses_lightweight_existence_checks_not_full_loads() -> None:
    repo = _FakeRepo()
    for universe in ("DJIA", "NASDAQ100"):
        for ranking_type in (
            RankingType.volume,
            RankingType.valuation_low_pe,
            RankingType.change_up,
            RankingType.change_down,
        ):
            for trading in (date(2026, 8, 18), date(2026, 8, 17), date(2026, 8, 14)):
                repo.existing.add(("US", universe, ranking_type.value, trading))

    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        force=False,
    )
    assert repo.exists_calls > 0
    assert repo.load_calls == 0


def test_capture_market_filter_does_not_count_other_market_as_skipped() -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert result["captured"] == 8
    # Only US targets can be skipped/failed; TW queries must not inflate skipped.
    assert result["skipped"] == 0
    assert result["failed"] == 0


def test_capture_contains_persist_exceptions_per_target() -> None:
    repo = _FakeRepo()
    repo.upsert_error = RuntimeError("sql blip")
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    result = service.capture_eod_archives(
        now=datetime(2026, 8, 18, 20, 50, tzinfo=timezone.utc),
        market="US",
        trading_date=date(2026, 8, 18),
        force=True,
    )
    assert result["captured"] == 0
    assert result["failed"] == 8
    assert result["skipped"] == 0


def test_archive_connectivity_failure_classification() -> None:
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    from app.services.rankings_archive_service import _is_archive_connectivity_failure

    programming = ProgrammingError(
        "SELECT TOP 5 DISTINCT ...",
        {},
        Exception("Incorrect syntax near the keyword 'DISTINCT'."),
    )
    operational = OperationalError(
        "SELECT 1",
        {},
        Exception("login timeout expired"),
    )
    # Bare "connection" in unrelated text must not classify as outage.
    vague = DBAPIError(
        "SELECT 1",
        {},
        Exception("[ODBC Driver 18 for SQL Server] something else"),
        connection_invalidated=False,
    )
    specific = DBAPIError(
        "SELECT 1",
        {},
        Exception("TCP Provider: connection refused"),
        connection_invalidated=False,
    )
    invalidated = DBAPIError(
        "SELECT 1",
        {},
        Exception("unrecognized transport failure"),
        connection_invalidated=True,
    )
    assert _is_archive_connectivity_failure(programming) is False
    assert _is_archive_connectivity_failure(operational) is True
    assert _is_archive_connectivity_failure(RuntimeError("azure down")) is False
    assert _is_archive_connectivity_failure(ConnectionError("reset")) is True
    assert _is_archive_connectivity_failure(vague) is False
    assert _is_archive_connectivity_failure(specific) is True
    assert _is_archive_connectivity_failure(invalidated) is True


def test_any_active_capture_window_only_within_retry_budget() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    ny = ZoneInfo("America/New_York")
    # Monday morning: prior Friday is settled but outside the 90-minute retry budget.
    before_monday_open = datetime(2026, 8, 17, 9, 0, tzinfo=ny)
    assert service.any_active_capture_window(before_monday_open) is False
    wake = service.next_sampler_wake_at(before_monday_open)
    assert wake > before_monday_open.astimezone(timezone.utc)


def test_any_active_capture_window_true_just_after_settle() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    ny = ZoneInfo("America/New_York")
    after_settle = datetime(2026, 8, 18, 17, 0, tzinfo=ny)
    assert service.any_active_capture_window(after_settle) is True
    wake = service.next_sampler_wake_at(after_settle)
    assert wake > after_settle.astimezone(timezone.utc)


def test_weekend_next_wake_is_next_trading_session_not_fifteen_minutes() -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    ny = ZoneInfo("America/New_York")
    saturday = datetime(2026, 8, 22, 10, 0, tzinfo=ny)
    assert service.any_active_capture_window(saturday) is False
    wake = service.next_sampler_wake_at(saturday)
    assert (wake - saturday.astimezone(timezone.utc)) > timedelta(hours=24)


def test_run_sampler_skips_db_before_next_settle(monkeypatch) -> None:
    """Sampler must not call capture when sleeping until a future settle (#187)."""
    service = RankingsArchiveService(repository=_FakeRepo())
    calls: list[str] = []

    def _capture(**_kwargs):
        calls.append("capture")
        return {"captured": 0, "skipped": 0, "failed": 0, "still_missing": 0}

    monkeypatch.setattr(service, "capture_eod_archives", _capture)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_eod_enabled",
        True,
    )
    monkeypatch.setattr(service, "any_active_capture_window", lambda _now=None: False)
    future = datetime.now(timezone.utc) + timedelta(hours=6)
    monkeypatch.setattr(service, "next_sampler_wake_at", lambda _now=None: future)

    def _wait(seconds: float) -> bool:
        service._stop_event.set()
        return True

    monkeypatch.setattr(service._stop_event, "wait", _wait)
    service._run_sampler(poll_seconds=60)
    assert calls == []


def test_run_sampler_skips_db_on_weekend_restart(monkeypatch) -> None:
    """Weekend restart must not touch Azure; catch-up waits for next settle (#187)."""
    service = RankingsArchiveService(repository=_FakeRepo())
    calls: list[str] = []
    ny = ZoneInfo("America/New_York")
    saturday = datetime(2026, 8, 22, 10, 0, tzinfo=ny)
    assert service.any_active_capture_window(saturday) is False

    def _capture(**_kwargs):
        calls.append("capture")
        return {"captured": 0, "skipped": 0, "failed": 0, "still_missing": 0}

    monkeypatch.setattr(service, "capture_eod_archives", _capture)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_eod_enabled",
        True,
    )
    monkeypatch.setattr(service, "any_active_capture_window", lambda _now=None: False)
    future_wake = datetime.now(timezone.utc) + timedelta(hours=40)
    monkeypatch.setattr(service, "next_sampler_wake_at", lambda _now=None: future_wake)

    def _wait(seconds: float) -> bool:
        # Idle sleep is chunked by poll_seconds; Azure must still not be touched.
        assert seconds == 60
        service._stop_event.set()
        return True

    monkeypatch.setattr(service._stop_event, "wait", _wait)
    service._run_sampler(poll_seconds=60)
    assert calls == []
    assert (service.next_sampler_wake_at(saturday) - saturday.astimezone(timezone.utc)) > timedelta(
        hours=24
    )


def test_run_sampler_captures_once_then_sleeps_until_next_wake(monkeypatch) -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    calls: list[str] = []

    def _capture(**_kwargs):
        calls.append("capture")
        return {
            "captured": 1,
            "skipped": 0,
            "failed": 0,
            "had_missing": 1,
            "still_missing": 0,
            "sql_round_trips": 3,
        }

    monkeypatch.setattr(service, "capture_eod_archives", _capture)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_eod_enabled",
        True,
    )
    monkeypatch.setattr(service, "any_active_capture_window", lambda _now=None: True)
    future = datetime.now(timezone.utc) + timedelta(hours=12)
    monkeypatch.setattr(service, "next_sampler_wake_at", lambda _now=None: future)

    def _wait(seconds: float) -> bool:
        service._stop_event.set()
        return True

    monkeypatch.setattr(service._stop_event, "wait", _wait)
    service._run_sampler(poll_seconds=900)
    assert calls == ["capture"]


def test_run_sampler_retries_within_budget_when_still_missing(monkeypatch) -> None:
    service = RankingsArchiveService(repository=_FakeRepo())
    calls: list[str] = []
    wake_gaps: list[float] = []

    def _capture(**_kwargs):
        calls.append("capture")
        return {
            "captured": 0,
            "skipped": 1,
            "failed": 0,
            "had_missing": 1,
            "still_missing": 1,
            "sql_round_trips": 2,
        }

    monkeypatch.setattr(service, "capture_eod_archives", _capture)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_eod_enabled",
        True,
    )
    monkeypatch.setattr(service, "any_active_capture_window", lambda _now=None: True)

    def _wait(seconds: float) -> bool:
        wake_gaps.append(seconds)
        service._stop_event.set()
        return True

    monkeypatch.setattr(service._stop_event, "wait", _wait)
    service._run_sampler(poll_seconds=120)
    assert calls == ["capture"]
    # After capture with still_missing, next_wake is now+poll; loop waits that gap.
    assert wake_gaps and wake_gaps[0] <= 120


def test_missed_friday_recovered_on_tuesday_settle_candidates() -> None:
    """Backfill window still includes a missed prior session after the next settle."""
    service = RankingsArchiveService(repository=_FakeRepo())
    ny = ZoneInfo("America/New_York")
    tuesday_after_settle = datetime(2026, 8, 18, 17, 0, tzinfo=ny)
    dates = service._settled_trading_dates(
        "US",
        tuesday_after_settle,
        ranking_type=RankingType.volume,
        limit=7,
    )
    assert date(2026, 8, 14) in dates or date(2026, 8, 17) in dates
    # Friday 2026-08-14 should be in the settled lookback from Tuesday.
    assert date(2026, 8, 14) in dates


def test_capture_batches_existence_checks() -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(
            _response(
                tradingDate="2026-08-18",
                timestamp="2026-08-18T20:45:00+00:00",
            )
        ),
    )
    ny = ZoneInfo("America/New_York")
    after_settle = datetime(2026, 8, 18, 17, 0, tzinfo=ny)
    # Cover every candidate date the capture cycle will inspect.
    for query in service.eod_archive_queries():
        universe = query.universe or query.tw_board or "DJIA"
        query_market = query.market
        for day in service._settled_trading_dates(
            query_market,  # type: ignore[arg-type]
            after_settle,
            ranking_type=query.ranking_type,
            limit=7,
        ):
            repo.existing.add((query.market, universe, query.ranking_type.value, day))

    before = repo.exists_calls
    result = service.capture_eod_archives(now=after_settle, trigger="test")
    # One ranged existence query for the whole cycle (not per date × query).
    assert repo.exists_calls == before + 1
    assert result["had_missing"] == 0
    assert result["still_missing"] == 0
    assert result["sql_round_trips"] >= 3


def test_retention_cutoff_preserves_boundary_day() -> None:
    cutoff = RankingsArchiveService.retention_cutoff_date(
        retention_days=730,
        as_of=date(2026, 8, 31),
    )
    assert cutoff == date(2024, 8, 31)
    assert RankingsArchiveService.retention_cutoff_date(retention_days=0) is None


def test_maybe_purge_refuses_standalone_wake(monkeypatch) -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )
    assert service.maybe_purge_expired_archives(already_awake=False) is None
    assert repo.purge_calls == []


def test_maybe_purge_runs_after_awake_capture_then_skips_within_interval(
    monkeypatch,
) -> None:
    repo = _FakeRepo()
    old = date(2020, 1, 2)
    recent = date(2026, 8, 18)
    repo.existing.add(("US", "DJIA", RankingType.volume.value, old))
    repo.existing.add(("US", "DJIA", RankingType.volume.value, recent))
    repo.item_counts[("US", "DJIA", RankingType.volume.value, old)] = 3
    repo.item_counts[("US", "DJIA", RankingType.volume.value, recent)] = 1

    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    first = service.maybe_purge_expired_archives(now=now, already_awake=True)
    assert first is not None
    assert first["snapshots_deleted"] == 1
    assert first["items_deleted"] == 3
    assert len(repo.purge_calls) == 1
    assert ("US", "DJIA", RankingType.volume.value, old) not in repo.existing
    assert ("US", "DJIA", RankingType.volume.value, recent) in repo.existing

    # Second call within 7 days must not purge again.
    second = service.maybe_purge_expired_archives(
        now=now + timedelta(days=1),
        already_awake=True,
    )
    assert second is None
    assert len(repo.purge_calls) == 1

    # force skips interval but still requires already_awake.
    forced = service.maybe_purge_expired_archives(
        now=now + timedelta(days=1),
        already_awake=True,
        force=True,
    )
    assert forced is not None
    assert len(repo.purge_calls) == 2


def test_maybe_purge_disabled_when_retention_days_zero(monkeypatch) -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        0,
    )
    assert (
        service.maybe_purge_expired_archives(
            now=datetime(2026, 8, 31, tzinfo=timezone.utc),
            already_awake=True,
            force=True,
        )
        is None
    )
    assert repo.purge_calls == []


def test_capture_triggers_opportunistic_purge(monkeypatch) -> None:
    repo = _FakeRepo()
    service = RankingsArchiveService(
        repository=repo,
        provider_registry=_FakeRegistry(_response()),
    )
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )
    # Mark all candidates present so capture skips provider fetches but still
    # completes an awake cycle and may purge.
    ny = ZoneInfo("America/New_York")
    after_settle = datetime(2026, 8, 18, 17, 0, tzinfo=ny)
    for query in service.eod_archive_queries():
        universe = query.universe or query.tw_board or "DJIA"
        for day in service._settled_trading_dates(
            query.market,  # type: ignore[arg-type]
            after_settle,
            ranking_type=query.ranking_type,
            limit=7,
        ):
            repo.existing.add((query.market, universe, query.ranking_type.value, day))

    repo.existing.add(("US", "DJIA", RankingType.volume.value, date(2020, 1, 1)))
    result = service.capture_eod_archives(now=after_settle)
    assert result["failed"] == 0
    assert repo.purge_calls
    assert ("US", "DJIA", RankingType.volume.value, date(2020, 1, 1)) not in repo.existing


def _archive_session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    ArchiveBase.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _archive_response(symbol: str = "AAPL") -> RankingResponse:
    return RankingResponse(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        universe="DJIA",
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id=f"US:{symbol}",
                    market="US",
                    symbol=symbol,
                    name=symbol,
                    exchange="NASDAQ",
                    currency="USD",
                    timezone="America/New_York",
                ),
                lastPrice=100.0,
                changePercent=1.0,
                volume=1000,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2026-08-20T16:00:00-04:00",
            )
        ],
        timestamp="2026-08-20T20:00:00+00:00",
        source="unit-test",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="EOD archive",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope="djia",
        twRankingsScope=None,
    )


def test_retention_days_config_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(rankings_archive_retention_days=-1)


def test_history_reads_never_trigger_retention_purge(monkeypatch) -> None:
    """#188 / #187: history APIs must not purge; only capture cycles may."""
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_archive_session_factory())
    purge_spy = MagicMock(wraps=repo.purge_snapshots_older_than)
    repo.purge_snapshots_older_than = purge_spy  # type: ignore[method-assign]

    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )

    trading_date = date(2026, 8, 20)
    assert (
        repo.upsert_eod_snapshot(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
            trading_date=trading_date,
            response=_archive_response(),
        )
        is not None
    )

    dates = service.list_history_dates(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        limit=5,
    )
    assert dates == [trading_date]

    loaded = service.get_history_rankings(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        trading_date=trading_date,
    )
    assert loaded is not None

    assert service.maybe_purge_expired_archives(already_awake=False) is None
    purge_spy.assert_not_called()


def test_preview_retention_purge_returns_none_when_unavailable(monkeypatch) -> None:
    repo = _FakeRepo()
    repo.available = False
    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )
    assert service.preview_retention_purge() is None


def test_maybe_purge_concurrent_threads_only_one_purges(monkeypatch) -> None:
    """Claim weekly slot inside the lock so overlapping capture threads do not purge twice."""
    repo = _FakeRepo()
    repo.existing.add(("US", "DJIA", RankingType.volume.value, date(2020, 1, 1)))
    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )
    purge_calls: list[date] = []

    def slow_purge(*, cutoff_trading_date, batch_size=200):
        purge_calls.append(cutoff_trading_date)
        time.sleep(0.2)
        return _FakeRepo.purge_snapshots_older_than(
            repo,
            cutoff_trading_date=cutoff_trading_date,
            batch_size=batch_size,
        )

    repo.purge_snapshots_older_than = slow_purge  # type: ignore[method-assign]

    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    results: list[dict[str, int | str] | None] = []

    def run() -> None:
        results.append(service.maybe_purge_expired_archives(now=now, already_awake=True))

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(purge_calls) == 1
    assert sum(1 for result in results if result is not None) == 1


def test_maybe_purge_reverts_claim_when_purge_fails(monkeypatch) -> None:
    repo = _FakeRepo()
    repo.existing.add(("US", "DJIA", RankingType.volume.value, date(2020, 1, 1)))
    service = RankingsArchiveService(repository=repo)
    monkeypatch.setattr(
        "app.services.rankings_archive_service.settings.rankings_archive_retention_days",
        730,
    )
    calls = {"n": 0}
    original_purge = repo.purge_snapshots_older_than

    def flaky_purge(*, cutoff_trading_date, batch_size=200):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated purge failure")
        return original_purge(cutoff_trading_date=cutoff_trading_date, batch_size=batch_size)

    repo.purge_snapshots_older_than = flaky_purge  # type: ignore[method-assign]
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    assert service.maybe_purge_expired_archives(now=now, already_awake=True) is None

    second = service.maybe_purge_expired_archives(now=now, already_awake=True)
    assert second is not None
    assert second["snapshots_deleted"] == 1
    assert calls["n"] == 2
