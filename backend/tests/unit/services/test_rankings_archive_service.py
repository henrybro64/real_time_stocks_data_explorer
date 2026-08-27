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
        self.schema_ok = True
        self.existing: set[tuple[str, str, str, date]] = set()
        self.upserts: list[tuple[str, str, str, date]] = []
        self.upsert_error: Exception | None = None
        self.load_calls = 0
        self.exists_calls = 0

    def is_available(self) -> bool:
        return self.available

    def ensure_schema(self) -> bool:
        return self.schema_ok

    def has_eod_snapshot(self, *, market, universe, ranking_type, trading_date):
        self.exists_calls += 1
        key = (market, universe, ranking_type.value, trading_date)
        return key in self.existing

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
