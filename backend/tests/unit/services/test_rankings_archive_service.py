"""Unit tests for rankings EOD archive capture service (#167)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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
        self.existing: set[tuple[str, str, str, date]] = set()
        self.upserts: list[tuple[str, str, str, date]] = []

    def is_available(self) -> bool:
        return self.available

    def load_eod_snapshot(self, *, market, universe, ranking_type, trading_date):
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
        key = (market, universe, ranking_type.value, trading_date)
        if key in self.existing:
            return _FakeEnvelope()
        self.existing.add(key)
        self.upserts.append(key)
        return _FakeEnvelope(item_count=len(response.items))


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


def _response(**overrides) -> RankingResponse:
    payload = dict(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        universe="DJIA",
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id="US:AAPL",
                    market="US",
                    symbol="AAPL",
                    name="Apple",
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
                timestamp="2026-08-18T16:00:00-04:00",
            )
        ],
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
    assert result == {"captured": 0, "skipped": 0, "failed": 0}
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
