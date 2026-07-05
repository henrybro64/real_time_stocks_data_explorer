from __future__ import annotations

from datetime import datetime, timezone

import app.services.rankings_snapshot_service as snapshot_service_module
from app.db.models import Base, RankingSnapshot, RankingSnapshotItem
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services.ranking_data_providers import RankingProviderRegistry, RankingQuery
from app.services.rankings_snapshot_service import RankingsSnapshotService, _scheduled_queries
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker


class FakeProvider:
    def __init__(
        self,
        *,
        bucket_start: datetime | None,
        response: RankingResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._bucket_start = bucket_start
        self._response = response
        self._error = error
        self.fetch_calls = 0

    def current_bucket_start(self, query: RankingQuery, now: datetime) -> datetime | None:  # noqa: ARG002
        return self._bucket_start

    def fetch(self, query: RankingQuery, *, limit: int, now: datetime) -> RankingResponse:  # noqa: ARG002
        self.fetch_calls += 1
        if self._error is not None:
            raise self._error
        if self._response is None:
            raise AssertionError("FakeProvider.fetch was called without a response")
        return self._response.model_copy(update={"items": self._response.items[:limit]})


class FakeRegistry(RankingProviderRegistry):
    def __init__(self, providers: dict[tuple[str, str, str | None], FakeProvider]) -> None:
        self._providers = providers

    def get_provider(self, query: RankingQuery) -> FakeProvider:
        key = (query.market, query.ranking_type.value, query.tw_board)
        provider = self._providers.get(key)
        if provider is None:
            raise AssertionError(f"Missing fake provider for {key}")
        return provider


def _install_in_memory_sessionlocal(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(snapshot_service_module, "SessionLocal", session_local)
    return session_local


def _stock(stock_id: str, symbol: str, *, exchange: str, currency: str, timezone_name: str) -> StockResponse:
    return StockResponse(
        id=stock_id,
        market=stock_id.split(":", 1)[0],
        symbol=symbol,
        name=f"{symbol} Corp.",
        exchange=exchange,
        currency=currency,
        timezone=timezone_name,
        nameEn=f"{symbol} Corp.",
        nameZhTw=None,
    )


def _response(
    *,
    market: str,
    ranking_type: RankingType,
    source_type: RankingSourceType,
    timestamp: str,
    tw_board: str | None = None,
    items: list[RankingItem] | None = None,
    source: str = "Quote rankings from the app's free best-effort quote providers.",
    warning: str | None = "Free unofficial quote source; data may be delayed or unavailable.",
) -> RankingResponse:
    return RankingResponse(
        market=market,
        rankingType=ranking_type,
        twBoard=tw_board,
        items=items or [],
        timestamp=timestamp,
        source=source,
        sourceType=source_type,
        isMarketOpen=True,
        dataFreshnessLabel="Market open; rankings use the latest quote snapshot.",
        warningMessage=warning,
        unsupportedReason=None,
        usRankingsScope="quote" if market == "US" else None,
    )


def _item(
    *,
    rank: int,
    stock: StockResponse,
    timestamp: str,
    volume: int | None = None,
    change_percent: float | None = None,
    volume_delta_percent: float | None = None,
    net_buy_sell_shares: int | None = None,
) -> RankingItem:
    return RankingItem(
        rank=rank,
        stock=stock,
        lastPrice=100.0 + rank,
        changePercent=change_percent,
        volume=volume,
        volumeDeltaPercent=volume_delta_percent,
        netBuySellShares=net_buy_sell_shares,
        peRatio=None,
        peRatioType=None,
        peRatioSource=None,
        valuationAsOf=None,
        valuationWarning=None,
        timestamp=timestamp,
    )


def test_open_bucket_capture_persists_snapshot_and_item_rows(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    response = _response(
        market="US",
        ranking_type=RankingType.volume,
        source_type=RankingSourceType.free_unofficial,
        timestamp="2026-07-03T13:35:00+00:00",
        items=[
            _item(
                rank=1,
                stock=_stock("US:AAPL", "AAPL", exchange="NASDAQ", currency="USD", timezone_name="America/New_York"),
                timestamp="2026-07-03T09:35:00-04:00",
                volume=2_000_000,
                change_percent=1.2,
            ),
            _item(
                rank=2,
                stock=_stock("US:MSFT", "MSFT", exchange="NASDAQ", currency="USD", timezone_name="America/New_York"),
                timestamp="2026-07-03T09:35:00-04:00",
                volume=1_500_000,
                change_percent=0.8,
            ),
        ],
    )
    provider = FakeProvider(bucket_start=bucket_start, response=response)
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("US", "volume", None): provider})
    )

    result = service.get_rankings(
        "US",
        RankingType.volume,
        limit=1,
        now=datetime(2026, 7, 3, 13, 35, tzinfo=timezone.utc),
    )

    assert result.sourceType == RankingSourceType.free_unofficial
    assert len(result.items) == 1
    assert result.items[0].stock.symbol == "AAPL"
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
        snapshot_items = list(session.execute(select(RankingSnapshotItem)).scalars())
    assert len(snapshots) == 1
    assert snapshots[0].bucket_start.replace(tzinfo=timezone.utc) == bucket_start
    assert len(snapshot_items) == 2


def test_same_bucket_reuses_cached_snapshot_without_duplicate_rows(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    response = _response(
        market="US",
        ranking_type=RankingType.volume,
        source_type=RankingSourceType.free_unofficial,
        timestamp="2026-07-03T13:35:00+00:00",
        items=[
            _item(
                rank=1,
                stock=_stock("US:AAPL", "AAPL", exchange="NASDAQ", currency="USD", timezone_name="America/New_York"),
                timestamp="2026-07-03T09:35:00-04:00",
                volume=2_000_000,
            )
        ],
    )
    provider = FakeProvider(bucket_start=bucket_start, response=response)
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("US", "volume", None): provider})
    )

    first = service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 13, 35, tzinfo=timezone.utc),
    )
    second = service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 13, 40, tzinfo=timezone.utc),
    )

    assert first.sourceType == RankingSourceType.free_unofficial
    assert second.sourceType == RankingSourceType.cache
    assert provider.fetch_calls == 1
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == 1


def test_market_closed_reads_latest_cached_snapshot(monkeypatch) -> None:
    _install_in_memory_sessionlocal(monkeypatch)
    open_bucket = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    open_response = _response(
        market="US",
        ranking_type=RankingType.volume,
        source_type=RankingSourceType.free_unofficial,
        timestamp="2026-07-03T13:35:00+00:00",
        items=[
            _item(
                rank=1,
                stock=_stock("US:AAPL", "AAPL", exchange="NASDAQ", currency="USD", timezone_name="America/New_York"),
                timestamp="2026-07-03T09:35:00-04:00",
                volume=2_000_000,
            )
        ],
    )
    open_provider = FakeProvider(bucket_start=open_bucket, response=open_response)
    closed_provider = FakeProvider(bucket_start=None, response=open_response)
    registry = FakeRegistry({("US", "volume", None): open_provider})
    service = RankingsSnapshotService(provider_registry=registry)
    service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 13, 35, tzinfo=timezone.utc),
    )
    registry._providers[("US", "volume", None)] = closed_provider

    result = service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 22, 0, tzinfo=timezone.utc),
    )

    assert result.sourceType == RankingSourceType.cache
    assert result.dataFreshnessLabel == "Market is closed. Showing latest available free snapshot."


def test_provider_failure_falls_back_to_latest_snapshot(monkeypatch) -> None:
    _install_in_memory_sessionlocal(monkeypatch)
    first_bucket = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    second_bucket = datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)
    response = _response(
        market="US",
        ranking_type=RankingType.volume,
        source_type=RankingSourceType.free_unofficial,
        timestamp="2026-07-03T13:35:00+00:00",
        items=[
            _item(
                rank=1,
                stock=_stock("US:AAPL", "AAPL", exchange="NASDAQ", currency="USD", timezone_name="America/New_York"),
                timestamp="2026-07-03T09:35:00-04:00",
                volume=2_000_000,
            )
        ],
    )
    initial_provider = FakeProvider(bucket_start=first_bucket, response=response)
    failing_provider = FakeProvider(bucket_start=second_bucket, error=RuntimeError("free source down"))
    registry = FakeRegistry({("US", "volume", None): initial_provider})
    service = RankingsSnapshotService(provider_registry=registry)
    service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 13, 35, tzinfo=timezone.utc),
    )
    registry._providers[("US", "volume", None)] = failing_provider

    result = service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 14, 5, tzinfo=timezone.utc),
    )

    assert result.sourceType == RankingSourceType.cache
    assert (
        result.dataFreshnessLabel
        == "Free data source temporarily unavailable. Showing latest available snapshot if available."
    )


def test_provider_failure_without_cached_snapshot_returns_clear_unavailable_state(monkeypatch) -> None:
    _install_in_memory_sessionlocal(monkeypatch)
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc),
        error=RuntimeError("free source down"),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("US", "volume", None): provider})
    )

    result = service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 14, 5, tzinfo=timezone.utc),
    )

    assert result.items == []
    assert result.sourceType == RankingSourceType.free_unofficial
    assert (
        result.dataFreshnessLabel
        == "Free data source temporarily unavailable. No cached snapshot is available yet."
    )


def test_us_institutional_queries_remain_unsupported(monkeypatch) -> None:
    _install_in_memory_sessionlocal(monkeypatch)
    service = RankingsSnapshotService()

    net_buy = service.get_rankings("US", RankingType.net_buy, limit=10)
    net_sell = service.get_rankings("US", RankingType.net_sell, limit=10)

    assert net_buy.sourceType == RankingSourceType.unsupported
    assert net_buy.items == []
    assert net_sell.sourceType == RankingSourceType.unsupported
    assert net_sell.items == []
    assert net_buy.unsupportedReason == "US institutional net-buy/net-sell is not available in this free-only app."
    assert net_sell.unsupportedReason == "US institutional net-buy/net-sell is not available in this free-only app."


def test_tw_daily_capture_uses_free_public_then_cached_limit(monkeypatch) -> None:
    _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 2, 16, 0, tzinfo=timezone.utc)
    response = _response(
        market="TW",
        ranking_type=RankingType.net_buy,
        source_type=RankingSourceType.free_public,
        timestamp="2026-07-03T10:00:00+08:00",
        tw_board="TPEx",
        source="TPEx institutional daily data",
        warning="上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。",
        items=[
            _item(
                rank=1,
                stock=_stock("TW:6488", "6488", exchange="TPEx", currency="TWD", timezone_name="Asia/Taipei"),
                timestamp="2026-07-02T18:00:00+08:00",
                net_buy_sell_shares=600_000,
            ),
            _item(
                rank=2,
                stock=_stock("TW:8299", "8299", exchange="TPEx", currency="TWD", timezone_name="Asia/Taipei"),
                timestamp="2026-07-02T18:00:00+08:00",
                net_buy_sell_shares=200_000,
            ),
        ],
    )
    provider = FakeProvider(bucket_start=bucket_start, response=response)
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TPEx"): provider})
    )

    first = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
        tw_board="TPEx",
    )
    second = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=1,
        now=datetime(2026, 7, 3, 2, 5, tzinfo=timezone.utc),
        tw_board="TPEx",
    )

    assert first.sourceType == RankingSourceType.free_public
    assert second.sourceType == RankingSourceType.cache
    assert len(second.items) == 1
    assert second.items[0].stock.exchange == "TPEx"


def test_scheduler_skips_closed_queries_and_continues_after_exceptions(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    closed_query = RankingQuery("US", RankingType.volume)
    failing_query = RankingQuery("TW", RankingType.volume, "TWSE")
    success_query = RankingQuery("TW", RankingType.net_buy, "TWSE")
    monkeypatch.setattr(
        snapshot_service_module,
        "_scheduled_queries",
        lambda: [closed_query, failing_query, success_query],
    )
    registry = FakeRegistry(
        {
            ("US", "volume", None): FakeProvider(bucket_start=None, response=_response(
                market="US",
                ranking_type=RankingType.volume,
                source_type=RankingSourceType.free_unofficial,
                timestamp="2026-07-03T13:35:00+00:00",
                items=[],
            )),
            ("TW", "volume", "TWSE"): FakeProvider(
                bucket_start=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),
                error=RuntimeError("temporary failure"),
            ),
            ("TW", "net_buy", "TWSE"): FakeProvider(
                bucket_start=datetime(2026, 7, 2, 16, 0, tzinfo=timezone.utc),
                response=_response(
                    market="TW",
                    ranking_type=RankingType.net_buy,
                    source_type=RankingSourceType.free_public,
                    timestamp="2026-07-03T10:00:00+08:00",
                    tw_board="TWSE",
                    source="TWSE T86 daily institutional data",
                    warning="台股三大法人買賣超為日資料，非盤中即時資料。",
                    items=[
                        _item(
                            rank=1,
                            stock=_stock("TW:2330", "2330", exchange="TWSE", currency="TWD", timezone_name="Asia/Taipei"),
                            timestamp="2026-07-02T18:00:00+08:00",
                            net_buy_sell_shares=500_000,
                        )
                    ],
                ),
            ),
        }
    )
    service = RankingsSnapshotService(
        provider_registry=registry,
        scheduler_provider_registry=registry,
    )

    captured = service.capture_scheduled_snapshots(
        now=datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
        trigger="test",
    )

    assert captured == 1
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == 1


def test_scheduler_never_targets_fake_us_institutional_queries() -> None:
    assert all(
        not (
            query.market == "US"
            and query.ranking_type in {RankingType.net_buy, RankingType.net_sell}
        )
        for query in _scheduled_queries()
    )


def test_on_demand_registry_uses_external_source_while_scheduler_registry_is_bounded() -> None:
    from app.services.ranking_data_providers import (
        _DisabledExternalRankingsSource,
        ranking_provider_registry,
        scheduler_ranking_provider_registry,
    )
    from app.services.rankings_external_source import RankingsExternalSource

    on_demand_external = ranking_provider_registry._quote_provider._service._external_source
    scheduler_external = scheduler_ranking_provider_registry._quote_provider._service._external_source

    assert isinstance(on_demand_external, RankingsExternalSource)
    assert isinstance(scheduler_external, _DisabledExternalRankingsSource)


def test_start_background_sampler_skips_when_database_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_service_module, "SessionLocal", None)
    service = RankingsSnapshotService()
    service.start_background_sampler(60)
    assert service._thread is None


def test_rankings_snapshot_enabled_setting_gates_sampler_start(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "rankings_snapshot_enabled", False)
    calls: list[int] = []
    monkeypatch.setattr(
        snapshot_service_module.rankings_snapshot_service,
        "start_background_sampler",
        lambda poll_seconds: calls.append(poll_seconds),
    )

    if settings.rankings_snapshot_enabled:
        snapshot_service_module.rankings_snapshot_service.start_background_sampler(
            settings.rankings_refresh_seconds,
        )

    assert calls == []


def test_start_background_sampler_passes_poll_seconds_to_thread(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    del session_local
    captured: dict[str, int] = {}

    class FakeThread:
        def __init__(self, target, args=(), kwargs=None, daemon=False, name=None) -> None:
            captured["poll_seconds"] = args[0]
            self._target = target
            self.daemon = daemon
            self.name = name

        def is_alive(self) -> bool:
            return False

        def start(self) -> None:
            return None

        def join(self, timeout=None) -> None:
            return None

    monkeypatch.setattr(snapshot_service_module, "Thread", FakeThread)
    service = RankingsSnapshotService()
    service.start_background_sampler(1234)
    assert captured["poll_seconds"] == 1234


def test_scheduled_queries_cover_visible_ranking_tabs() -> None:
    us_types = {query.ranking_type for query in _scheduled_queries() if query.market == "US"}
    assert {RankingType.change_up, RankingType.change_down, RankingType.valuation_low_pe} <= us_types


def test_scheduled_queries_capture_volume_deltas_before_volume_baseline() -> None:
    queries = _scheduled_queries()
    us_volume_up = next(
        index
        for index, query in enumerate(queries)
        if query.market == "US" and query.ranking_type == RankingType.volume_up
    )
    us_volume = next(
        index
        for index, query in enumerate(queries)
        if query.market == "US" and query.ranking_type == RankingType.volume
    )
    assert us_volume_up < us_volume


def test_on_demand_serves_scheduler_bounded_snapshot_when_on_demand_missing(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    with session_local() as session:
        snapshot = RankingSnapshot(
            market="US",
            tw_board="",
            ranking_type="volume",
            bucket_start=bucket_start,
            source="scheduler snapshot",
            source_type="free-unofficial",
            source_freshness="scheduler",
            is_market_open=True,
            warning_message=None,
            unsupported_reason=None,
            capture_scope="scheduler_bounded",
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RankingSnapshotItem(
                snapshot_id=snapshot.id,
                rank=1,
                stock_id="US:AAPL",
                symbol="AAPL",
                name="Apple Inc.",
                last_price=200.0,
                change_percent=1.0,
                volume=1_000_000,
                volume_delta=None,
                volume_delta_percent=None,
                net_buy=None,
                net_sell=None,
                data_time="2026-07-03T09:35:00-04:00",
                raw_payload_json=None,
            )
        )
        session.commit()

    provider = FakeProvider(
        bucket_start=bucket_start,
        response=_response(
            market="US",
            ranking_type=RankingType.volume,
            source_type=RankingSourceType.free_unofficial,
            timestamp="2026-07-03T13:35:00+00:00",
            items=[
                _item(
                    rank=1,
                    stock=_stock(
                        "US:MSFT",
                        "MSFT",
                        exchange="NASDAQ",
                        currency="USD",
                        timezone_name="America/New_York",
                    ),
                    timestamp="2026-07-03T09:35:00-04:00",
                    volume=2_000_000,
                )
            ],
        ),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("US", "volume", None): provider})
    )

    result = service.get_rankings(
        "US",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 13, 35, tzinfo=timezone.utc),
    )

    assert provider.fetch_calls == 0
    assert result.sourceType == RankingSourceType.cache
    assert result.items[0].stock.symbol == "AAPL"


def test_load_latest_snapshot_returns_single_newest_row(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    query = RankingQuery("US", RankingType.volume)
    older_bucket = datetime(2026, 7, 3, 13, 0, tzinfo=timezone.utc)
    newer_bucket = datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc)
    with session_local() as session:
        for bucket_start, symbol in ((older_bucket, "MSFT"), (newer_bucket, "AAPL")):
            snapshot = RankingSnapshot(
                market="US",
                tw_board="",
                ranking_type="volume",
                bucket_start=bucket_start,
                source="test",
                source_type="free-unofficial",
                source_freshness="test",
                is_market_open=True,
                warning_message=None,
                unsupported_reason=None,
                capture_scope="on_demand",
            )
            session.add(snapshot)
            session.flush()
            session.add(
                RankingSnapshotItem(
                    snapshot_id=snapshot.id,
                    rank=1,
                    stock_id=f"US:{symbol}",
                    symbol=symbol,
                    name=f"{symbol} Corp.",
                    last_price=100.0,
                    change_percent=1.0,
                    volume=1_000_000,
                    volume_delta=None,
                    volume_delta_percent=None,
                    net_buy=None,
                    net_sell=None,
                    data_time="2026-07-03T09:35:00-04:00",
                    raw_payload_json=None,
                )
            )
        session.commit()

    service = RankingsSnapshotService()
    latest = service._load_latest_snapshot(query)

    assert latest is not None
    assert latest.snapshot.bucket_start.replace(tzinfo=timezone.utc) == newer_bucket
    assert latest.items[0].symbol == "AAPL"

