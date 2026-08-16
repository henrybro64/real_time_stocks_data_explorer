from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import app.services.rankings_snapshot_service as snapshot_service_module
from app.db.models import Base, RankingSnapshot, RankingSnapshotItem
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services.ranking_data_providers import (
    FreeTWInstitutionalDailyProvider,
    RankingProviderRegistry,
    RankingQuery,
)
from app.services.rankings_snapshot_service import RankingsSnapshotService, _scheduled_queries
from app.services.market_clock import tw_institutional_daily_snapshot_bucket
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

_QUOTE_SOURCE = "Quote rankings from the app's free best-effort quote providers."
_VALUATION_SOURCE = (
    "Valuation rankings use the highest-volume current quote candidates, then sort by "
    "P/E from the app's available valuation sources."
)
_FALLBACK_SOURCE = "Limited hard-coded TWSE/TPEx quote fallback; not a full-board ranking."
_YAHOO_SOURCE = "Yahoo Finance pre-ranked quote rankings."


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
    source: str = _QUOTE_SOURCE,
    warning: str | None = "Free unofficial quote source; data may be delayed or unavailable.",
    tw_rankings_scope: str | None = None,
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
        twRankingsScope=tw_rankings_scope,
    )


def _tw_volume_quote_response(
    *,
    source: str,
    tw_rankings_scope: str | None,
    warning: str | None = "Free unofficial quote source; data may be delayed or unavailable.",
    timestamp: str = "2026-07-03T03:05:00+00:00",
    item_timestamp: str = "2026-07-03T11:05:00+08:00",
    stock_id: str = "TW:2330",
    symbol: str = "2330",
    volume: int = 2_000_000,
) -> RankingResponse:
    return _response(
        market="TW",
        ranking_type=RankingType.volume,
        source_type=RankingSourceType.free_unofficial,
        timestamp=timestamp,
        tw_board="TWSE",
        source=source,
        warning=warning,
        tw_rankings_scope=tw_rankings_scope,
        items=[
            _item(
                rank=1,
                stock=_stock(stock_id, symbol, exchange="TWSE", currency="TWD", timezone_name="Asia/Taipei"),
                timestamp=item_timestamp,
                volume=volume,
            )
        ],
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


@pytest.mark.parametrize(
    ("source", "scope", "stock_id", "symbol", "volume", "warning"),
    [
        (
            _FALLBACK_SOURCE,
            "fallback",
            "TW:2330",
            "2330",
            2_000_000,
            (
                "Broader Taiwan quote rankings are temporarily unavailable. "
                "This table uses a small hard-coded fallback universe, not a complete TWSE/TPEx ranking."
            ),
        ),
        (
            _YAHOO_SOURCE,
            "yahoo",
            "TW:9999",
            "9999",
            9_000_000,
            "Free unofficial quote source; data may be delayed or unavailable.",
        ),
    ],
)
def test_cached_tw_quote_snapshot_reconstructs_scope(
    monkeypatch,
    source: str,
    scope: str,
    stock_id: str,
    symbol: str,
    volume: int,
    warning: str,
) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 3, 3, 0, tzinfo=timezone.utc)
    response = _tw_volume_quote_response(
        source=source,
        tw_rankings_scope=scope,
        warning=warning,
        stock_id=stock_id,
        symbol=symbol,
        volume=volume,
    )
    provider = FakeProvider(bucket_start=bucket_start, response=response)
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "volume", "TWSE"): provider})
    )

    first = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 3, 5, tzinfo=timezone.utc),
        tw_board="TWSE",
    )
    cached = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 3, 10, tzinfo=timezone.utc),
        tw_board="TWSE",
    )

    assert first.twRankingsScope == scope
    assert first.usRankingsScope is None
    assert cached.sourceType == RankingSourceType.cache
    assert cached.source == source
    assert cached.twRankingsScope == scope
    assert cached.usRankingsScope is None
    assert provider.fetch_calls == 1
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == 1


@pytest.mark.parametrize(
    ("source", "ranking_type", "capture_scope", "stock_ids", "expected"),
    [
        (_FALLBACK_SOURCE, None, None, None, "fallback"),
        (_YAHOO_SOURCE, None, None, None, "yahoo"),
        (_QUOTE_SOURCE, RankingType.volume, "scheduler_bounded", None, "quote"),
        (_QUOTE_SOURCE, RankingType.volume, "on_demand", None, "fallback"),
        (_QUOTE_SOURCE, RankingType.change_up, "on_demand", None, "fallback"),
        (_QUOTE_SOURCE, RankingType.volume_up, "on_demand", None, "quote"),
        (_VALUATION_SOURCE, RankingType.valuation_low_pe, "scheduler_bounded", None, "quote"),
        (_VALUATION_SOURCE, RankingType.valuation_low_pe, "on_demand", ["TW:2330", "TW:2317"], "fallback"),
        (_VALUATION_SOURCE, RankingType.valuation_low_pe, "on_demand", ["TW:9999"], "yahoo"),
        ("TWSE T86 daily institutional data", None, None, None, None),
        ("TPEx institutional daily data", None, None, None, None),
    ],
)
def test_tw_rankings_scope_reconstructs_from_stored_source(
    source: str,
    ranking_type: RankingType | None,
    capture_scope: str | None,
    stock_ids: list[str] | None,
    expected: str | None,
) -> None:
    from app.services.rankings_snapshot_service import _tw_rankings_scope_from_source

    assert (
        _tw_rankings_scope_from_source(
            source,
            ranking_type=ranking_type,
            capture_scope=capture_scope,
            stock_ids=stock_ids,
            tw_board="TWSE" if stock_ids else None,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("capture_scope", "expected_scope"),
    [
        ("on_demand", "fallback"),
        ("scheduler_bounded", "quote"),
    ],
)
def test_closed_market_legacy_quote_snapshot_scope(
    monkeypatch,
    capture_scope: str,
    expected_scope: str,
) -> None:
    _install_in_memory_sessionlocal(monkeypatch)
    open_bucket = datetime(2026, 7, 3, 1, 30, tzinfo=timezone.utc)
    stored = _tw_volume_quote_response(
        source=_QUOTE_SOURCE,
        tw_rankings_scope="quote",
        timestamp="2026-07-03T01:35:00+00:00",
        item_timestamp="2026-07-03T09:35:00+08:00",
    )
    query = RankingQuery("TW", RankingType.volume, "TWSE")
    closed_provider = FakeProvider(bucket_start=None, response=stored)
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "volume", "TWSE"): closed_provider})
    )
    persisted = service._persist_snapshot(
        query,
        open_bucket,
        stored,
        capture_scope=capture_scope,
    )
    assert persisted is not None

    result = service.get_rankings(
        "TW",
        RankingType.volume,
        limit=5,
        now=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        tw_board="TWSE",
    )

    assert result.sourceType == RankingSourceType.cache
    assert result.twRankingsScope == expected_scope
    assert result.usRankingsScope is None
    assert closed_provider.fetch_calls == 0


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


def _raise_snapshot_db_error(*_args, **_kwargs):
    raise RuntimeError("snapshot database failed")


def _patch_snapshot_repo_errors(monkeypatch, *method_names: str) -> None:
    for name in method_names:
        monkeypatch.setattr(
            snapshot_service_module.ranking_snapshot_repository,
            name,
            _raise_snapshot_db_error,
        )


def _us_volume_live_response() -> RankingResponse:
    return _response(
        market="US",
        ranking_type=RankingType.volume,
        source_type=RankingSourceType.free_unofficial,
        timestamp="2026-07-03T13:35:00+00:00",
        items=[
            _item(
                rank=1,
                stock=_stock(
                    "US:AAPL",
                    "AAPL",
                    exchange="NASDAQ",
                    currency="USD",
                    timezone_name="America/New_York",
                ),
                timestamp="2026-07-03T09:35:00-04:00",
                volume=2_000_000,
            )
        ],
    )


def _live_snapshot_case(kind: str, *, error: Exception | None = None):
    if kind == "us_volume":
        provider = FakeProvider(
            bucket_start=datetime(2026, 7, 3, 13, 30, tzinfo=timezone.utc),
            response=None if error else _us_volume_live_response(),
            error=error,
        )
        return (
            provider,
            ("US", "volume", None),
            {
                "market": "US",
                "ranking_type": RankingType.volume,
                "limit": 5,
                "now": datetime(2026, 7, 3, 13, 35, tzinfo=timezone.utc),
            },
            "AAPL",
            RankingSourceType.free_unofficial,
            False,
        )
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),
        response=None
        if error
        else _tw_net_buy_response(
            timestamp="2026-07-03T10:00:00+08:00",
            item_timestamp="2026-07-02T18:00:00+08:00",
        ),
        error=error,
    )
    return (
        provider,
        ("TW", "net_buy", "TWSE"),
        {
            "market": "TW",
            "ranking_type": RankingType.net_buy,
            "limit": 5,
            "now": datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
            "tw_board": "TWSE",
        },
        "2330",
        RankingSourceType.free_public,
        True,
    )


@pytest.mark.parametrize("kind", ["us_volume", "tw_net_buy"])
@pytest.mark.parametrize(
    ("failing_methods", "expect_persisted"),
    [
        (("load_bucket_snapshot",), True),
        (("persist_snapshot",), False),
    ],
    ids=["read_error", "write_error"],
)
def test_snapshot_db_error_still_returns_live_rankings(
    monkeypatch,
    kind: str,
    failing_methods: tuple[str, ...],
    expect_persisted: bool,
) -> None:
    provider, key, get_kwargs, symbol, source_type, needs_holidays = _live_snapshot_case(kind)
    if needs_holidays:
        _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    _patch_snapshot_repo_errors(monkeypatch, *failing_methods)
    service = RankingsSnapshotService(provider_registry=FakeRegistry({key: provider}))

    result = service.get_rankings(**get_kwargs)

    assert provider.fetch_calls == 1
    assert result.sourceType == source_type
    assert result.items[0].stock.symbol == symbol
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == (1 if expect_persisted else 0)


@pytest.mark.parametrize("kind", ["us_volume", "tw_net_buy"])
def test_snapshot_read_error_after_provider_failure_returns_unavailable(
    monkeypatch,
    kind: str,
) -> None:
    provider, key, get_kwargs, _symbol, source_type, needs_holidays = _live_snapshot_case(
        kind,
        error=RuntimeError("free source down"),
    )
    if needs_holidays:
        _disable_tw_holidays(monkeypatch)
    _install_in_memory_sessionlocal(monkeypatch)
    _patch_snapshot_repo_errors(monkeypatch, "load_bucket_snapshot", "load_latest_snapshot")
    service = RankingsSnapshotService(provider_registry=FakeRegistry({key: provider}))

    result = service.get_rankings(**get_kwargs)

    assert result.items == []
    assert result.sourceType == source_type
    assert result.dataFreshnessLabel == (
        "Free data source temporarily unavailable. No cached snapshot is available yet."
    )


@pytest.mark.parametrize("kind", ["us_volume", "tw_net_buy"])
def test_scheduler_write_error_does_not_raise_or_count_capture(
    monkeypatch,
    kind: str,
) -> None:
    provider, key, get_kwargs, _symbol, _source_type, needs_holidays = _live_snapshot_case(kind)
    if needs_holidays:
        _disable_tw_holidays(monkeypatch)
    _install_in_memory_sessionlocal(monkeypatch)
    _patch_snapshot_repo_errors(monkeypatch, "persist_snapshot")
    query = RankingQuery(key[0], RankingType(key[1]), key[2])
    monkeypatch.setattr(snapshot_service_module, "_scheduled_queries", lambda: [query])
    registry = FakeRegistry({key: provider})
    service = RankingsSnapshotService(
        provider_registry=registry,
        scheduler_provider_registry=registry,
    )

    captured = service.capture_scheduled_snapshots(now=get_kwargs["now"], trigger="test")

    assert captured == 0
    assert provider.fetch_calls == 1


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
    bucket_start = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
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
    assert provider.fetch_calls == 1
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
                bucket_start=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),
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
    assert snapshots[0].bucket_start.replace(tzinfo=timezone.utc) == datetime(
        2026, 7, 2, 10, 0, tzinfo=timezone.utc
    )


def test_scheduler_skips_tw_daily_fetch_when_expected_bucket_already_cached(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    now = datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc)
    expected_bucket = tw_institutional_daily_snapshot_bucket(now)
    assert expected_bucket is not None
    _seed_tw_daily_snapshot(
        session_local,
        bucket_start=expected_bucket,
        item_timestamp="2026-07-02T18:00:00+08:00",
        capture_scope="scheduler_bounded",
    )
    query = RankingQuery("TW", RankingType.net_buy, "TWSE")
    monkeypatch.setattr(snapshot_service_module, "_scheduled_queries", lambda: [query])
    provider = FakeProvider(
        bucket_start=expected_bucket,
        error=RuntimeError("scheduler should not fetch when the expected bucket exists"),
    )
    registry = FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    service = RankingsSnapshotService(
        provider_registry=registry,
        scheduler_provider_registry=registry,
    )

    captured = service.capture_scheduled_snapshots(now=now, trigger="test")

    assert captured == 0
    assert provider.fetch_calls == 0
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
    assert ranking_provider_registry._quote_provider._service._uses_hardcoded_tw_quote_universe is True
    assert (
        scheduler_ranking_provider_registry._quote_provider._service._uses_hardcoded_tw_quote_universe
        is False
    )


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


def _disable_tw_holidays(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset(),
    )


def _tw_daily_item(
    *,
    timestamp: str,
    shares: int = 500_000,
    stock_id: str = "TW:2330",
    symbol: str = "2330",
    exchange: str = "TWSE",
) -> RankingItem:
    return _item(
        rank=1,
        stock=_stock(
            stock_id,
            symbol,
            exchange=exchange,
            currency="TWD",
            timezone_name="Asia/Taipei",
        ),
        timestamp=timestamp,
        net_buy_sell_shares=shares,
    )


def _tw_net_buy_item(*, timestamp: str, shares: int = 500_000) -> RankingItem:
    return _tw_daily_item(timestamp=timestamp, shares=shares)


def _tw_daily_response(
    *,
    ranking_type: RankingType,
    tw_board: str,
    timestamp: str,
    item_timestamp: str,
    shares: int = 500_000,
    stock_id: str = "TW:2330",
    symbol: str = "2330",
    exchange: str = "TWSE",
) -> RankingResponse:
    source = (
        "TPEx institutional daily data"
        if tw_board == "TPEx"
        else "TWSE T86 daily institutional data"
    )
    warning = (
        "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
        if tw_board == "TPEx"
        else "台股三大法人買賣超為日資料，非盤中即時資料。"
    )
    return _response(
        market="TW",
        ranking_type=ranking_type,
        source_type=RankingSourceType.free_public,
        timestamp=timestamp,
        tw_board=tw_board,
        source=source,
        warning=warning,
        items=[
            _tw_daily_item(
                timestamp=item_timestamp,
                shares=shares,
                stock_id=stock_id,
                symbol=symbol,
                exchange=exchange,
            )
        ],
    )


def _tw_net_buy_response(
    *,
    timestamp: str,
    item_timestamp: str,
    shares: int = 500_000,
) -> RankingResponse:
    return _tw_daily_response(
        ranking_type=RankingType.net_buy,
        tw_board="TWSE",
        timestamp=timestamp,
        item_timestamp=item_timestamp,
        shares=shares,
    )


def _seed_tw_daily_snapshot(
    session_local,
    *,
    bucket_start: datetime,
    item_timestamp: str,
    capture_scope: str = "on_demand",
    ranking_type: RankingType = RankingType.net_buy,
    tw_board: str = "TWSE",
    stock_id: str = "TW:2330",
    symbol: str = "2330",
    exchange: str = "TWSE",
    shares: int = 500_000,
) -> None:
    source = (
        "TPEx institutional daily data"
        if tw_board == "TPEx"
        else "TWSE T86 daily institutional data"
    )
    warning = (
        "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
        if tw_board == "TPEx"
        else "台股三大法人買賣超為日資料，非盤中即時資料。"
    )
    with session_local() as session:
        snapshot = RankingSnapshot(
            market="TW",
            tw_board=tw_board,
            ranking_type=ranking_type.value,
            bucket_start=bucket_start,
            source=source,
            source_type="free-public",
            source_freshness="Showing the latest saved TW institutional daily snapshot.",
            is_market_open=False,
            warning_message=warning,
            unsupported_reason=None,
            capture_scope=capture_scope,
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RankingSnapshotItem(
                snapshot_id=snapshot.id,
                rank=1,
                stock_id=stock_id,
                symbol=symbol,
                name=f"{symbol} Corp.",
                last_price=101.0,
                change_percent=None,
                volume=None,
                volume_delta=None,
                volume_delta_percent=None,
                net_buy=shares if ranking_type == RankingType.net_buy else None,
                net_sell=shares if ranking_type == RankingType.net_sell else None,
                data_time=item_timestamp,
                raw_payload_json=_tw_daily_item(
                    timestamp=item_timestamp,
                    shares=shares,
                    stock_id=stock_id,
                    symbol=symbol,
                    exchange=exchange,
                ).model_dump(mode="json"),
            )
        )
        session.commit()


def _seed_tw_net_buy_snapshot(
    session_local,
    *,
    bucket_start: datetime,
    item_timestamp: str,
    capture_scope: str = "on_demand",
) -> None:
    _seed_tw_daily_snapshot(
        session_local,
        bucket_start=bucket_start,
        item_timestamp=item_timestamp,
        capture_scope=capture_scope,
    )


@pytest.mark.parametrize(
    ("ranking_type", "tw_board", "stock_id", "symbol", "exchange"),
    [
        (RankingType.net_buy, "TWSE", "TW:2330", "2330", "TWSE"),
        (RankingType.net_sell, "TWSE", "TW:2330", "2330", "TWSE"),
        (RankingType.net_buy, "TPEx", "TW:6488", "6488", "TPEx"),
        (RankingType.net_sell, "TPEx", "TW:6488", "6488", "TPEx"),
    ],
)
def test_tw_daily_cache_hit_skips_live_fetch(
    monkeypatch,
    ranking_type: RankingType,
    tw_board: str,
    stock_id: str,
    symbol: str,
    exchange: str,
) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    _seed_tw_daily_snapshot(
        session_local,
        bucket_start=bucket_start,
        item_timestamp="2026-07-02T18:00:00+08:00",
        ranking_type=ranking_type,
        tw_board=tw_board,
        stock_id=stock_id,
        symbol=symbol,
        exchange=exchange,
    )
    provider = FakeProvider(
        bucket_start=bucket_start,
        response=_tw_daily_response(
            ranking_type=ranking_type,
            tw_board=tw_board,
            timestamp="2026-07-03T10:00:00+08:00",
            item_timestamp="2026-07-02T18:00:00+08:00",
            stock_id=stock_id,
            symbol=symbol,
            exchange=exchange,
        ),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", ranking_type.value, tw_board): provider})
    )

    result = service.get_rankings(
        "TW",
        ranking_type,
        limit=5,
        now=datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
        tw_board=tw_board,
    )

    assert provider.fetch_calls == 0
    assert result.sourceType == RankingSourceType.cache
    assert result.rankingType == ranking_type
    assert result.twBoard == tw_board
    assert result.dataFreshnessLabel == "Showing the latest saved TW institutional daily snapshot."
    assert result.items[0].stock.symbol == symbol
    assert result.items[0].stock.exchange == exchange
    assert result.items[0].netBuySellShares == 500_000


def test_tw_daily_cache_miss_fetches_live_and_persists(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),
        response=_tw_net_buy_response(
            timestamp="2026-07-03T10:00:00+08:00",
            item_timestamp="2026-07-02T18:00:00+08:00",
        ),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 1
    assert result.sourceType == RankingSourceType.free_public
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == 1
    assert snapshots[0].bucket_start.replace(tzinfo=timezone.utc) == datetime(
        2026, 7, 2, 10, 0, tzinfo=timezone.utc
    )


def test_tw_daily_provider_failure_falls_back_to_latest_snapshot(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
        item_timestamp="2026-07-01T18:00:00+08:00",
    )
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        error=RuntimeError("TWSE T86 unavailable"),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime(2026, 7, 3, 10, 30, tzinfo=timezone.utc),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 1
    assert result.sourceType == RankingSourceType.cache
    assert (
        result.dataFreshnessLabel
        == "Free data source temporarily unavailable. Showing latest available snapshot if available."
    )
    assert result.items[0].stock.symbol == "2330"


def test_tw_daily_provider_failure_without_snapshot_returns_unavailable(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    _install_in_memory_sessionlocal(monkeypatch)
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),
        error=RuntimeError("TWSE T86 unavailable"),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 1
    assert result.items == []
    assert (
        result.dataFreshnessLabel
        == "Free data source temporarily unavailable. No cached snapshot is available yet."
    )


def test_tw_daily_before_taipei_cutoff_reuses_previous_trading_day_snapshot(
    monkeypatch,
) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    previous_day_bucket = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=previous_day_bucket,
        item_timestamp="2026-07-02T18:00:00+08:00",
    )
    provider = FakeProvider(
        bucket_start=previous_day_bucket,
        response=_tw_net_buy_response(
            timestamp="2026-07-03T17:59:00+08:00",
            item_timestamp="2026-07-03T18:00:00+08:00",
            shares=999_000,
        ),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime.fromisoformat("2026-07-03T17:59:00+08:00"),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 0
    assert result.sourceType == RankingSourceType.cache
    assert result.items[0].netBuySellShares == 500_000


def test_tw_daily_after_taipei_cutoff_does_not_treat_previous_day_as_current(
    monkeypatch,
) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    previous_day_bucket = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=previous_day_bucket,
        item_timestamp="2026-07-02T18:00:00+08:00",
    )
    today_response = _tw_net_buy_response(
        timestamp="2026-07-03T18:05:00+08:00",
        item_timestamp="2026-07-03T18:00:00+08:00",
        shares=800_000,
    )
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        response=today_response,
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime.fromisoformat("2026-07-03T18:05:00+08:00"),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 1
    assert result.sourceType == RankingSourceType.free_public
    assert result.items[0].netBuySellShares == 800_000
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == 2


def test_tw_daily_midnight_item_timestamp_persists_under_lookup_bucket(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    now = datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc)
    expected_bucket = tw_institutional_daily_snapshot_bucket(now)
    provider = FakeProvider(
        bucket_start=datetime(2026, 7, 2, 16, 0, tzinfo=timezone.utc),
        response=_tw_net_buy_response(
            timestamp="2026-07-03T10:00:00+08:00",
            item_timestamp="2026-07-02T00:00:00+08:00",
        ),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    first = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=now,
        tw_board="TWSE",
    )
    second = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=now,
        tw_board="TWSE",
    )

    assert expected_bucket is not None
    assert provider.fetch_calls == 1
    assert first.sourceType == RankingSourceType.free_public
    assert second.sourceType == RankingSourceType.cache
    with session_local() as session:
        snapshots = list(session.execute(select(RankingSnapshot)).scalars())
    assert len(snapshots) == 1
    assert snapshots[0].bucket_start.replace(tzinfo=timezone.utc) == expected_bucket


def test_tw_institutional_provider_current_bucket_matches_snapshot_lookup(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    now = datetime.fromisoformat("2026-07-03T17:59:00+08:00")
    provider = FreeTWInstitutionalDailyProvider()
    query = RankingQuery("TW", RankingType.net_buy, "TWSE")

    assert provider.current_bucket_start(query, now) == tw_institutional_daily_snapshot_bucket(now)
    assert provider.current_bucket_start(query, now) == datetime(
        2026, 7, 2, 10, 0, tzinfo=timezone.utc
    )


def test_tw_daily_weekend_lookup_uses_friday_snapshot(monkeypatch) -> None:
    _disable_tw_holidays(monkeypatch)
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    friday_bucket = datetime(2026, 4, 3, 10, 0, tzinfo=timezone.utc)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=friday_bucket,
        item_timestamp="2026-04-03T18:00:00+08:00",
    )
    provider = FakeProvider(
        bucket_start=friday_bucket,
        error=RuntimeError("weekend requests should reuse Friday's daily snapshot"),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime.fromisoformat("2026-04-04T10:00:00+08:00"),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 0
    assert result.sourceType == RankingSourceType.cache
    assert tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-04T10:00:00+08:00"),
    ) == friday_bucket


def test_tw_daily_weekday_holiday_lookup_skips_closed_session(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: frozenset({date(2026, 4, 6)}) if year == 2026 else frozenset(),
    )
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    friday_bucket = datetime(2026, 4, 3, 10, 0, tzinfo=timezone.utc)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=friday_bucket,
        item_timestamp="2026-04-03T18:00:00+08:00",
    )
    provider = FakeProvider(
        bucket_start=friday_bucket,
        error=RuntimeError("holiday Monday should still use Friday's daily snapshot"),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=datetime.fromisoformat("2026-04-06T19:00:00+08:00"),
        tw_board="TWSE",
    )

    assert provider.fetch_calls == 0
    assert result.sourceType == RankingSourceType.cache
    assert tw_institutional_daily_snapshot_bucket(
        datetime.fromisoformat("2026-04-06T19:00:00+08:00"),
    ) == friday_bucket


def test_tw_daily_cache_lookup_skips_holiday_http_on_weekday_hit(monkeypatch) -> None:
    def _should_not_fetch(year: int) -> frozenset[date]:
        raise AssertionError(f"holiday calendar should not run for {year}")

    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        _should_not_fetch,
    )
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    bucket_start = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=bucket_start,
        item_timestamp="2026-07-02T18:00:00+08:00",
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry(
            {
                ("TW", "net_buy", "TWSE"): FakeProvider(
                    bucket_start=bucket_start,
                    error=RuntimeError("cache hit should not live-fetch"),
                )
            }
        )
    )
    query = RankingQuery("TW", RankingType.net_buy, "TWSE")

    cached = service._load_applicable_tw_daily_snapshot(
        query,
        datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
    )

    assert cached is not None
    assert cached.snapshot.bucket_start.replace(tzinfo=timezone.utc) == bucket_start


def test_tw_daily_long_closure_without_expected_bucket_reuses_latest_snapshot(monkeypatch) -> None:
    holidays = {date(2026, 1, day) for day in range(1, 32)} | {
        date(2026, 2, day) for day in range(1, 15)
    }
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: holidays if year == 2026 else frozenset(),
    )
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    last_open_bucket = datetime(2025, 12, 31, 10, 0, tzinfo=timezone.utc)
    _seed_tw_net_buy_snapshot(
        session_local,
        bucket_start=last_open_bucket,
        item_timestamp="2025-12-31T18:00:00+08:00",
    )
    provider = FakeProvider(
        bucket_start=last_open_bucket,
        error=RuntimeError("long holiday closures should reuse the latest snapshot"),
    )
    service = RankingsSnapshotService(
        provider_registry=FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    )
    now = datetime.fromisoformat("2026-02-14T19:00:00+08:00")

    result = service.get_rankings(
        "TW",
        RankingType.net_buy,
        limit=5,
        now=now,
        tw_board="TWSE",
    )

    assert tw_institutional_daily_snapshot_bucket(now) is None
    assert provider.fetch_calls == 0
    assert result.sourceType == RankingSourceType.cache
    assert result.items[0].stock.symbol == "2330"


def test_scheduler_skips_tw_daily_fetch_when_long_closure_has_latest_snapshot(monkeypatch) -> None:
    holidays = {date(2026, 1, day) for day in range(1, 32)} | {
        date(2026, 2, day) for day in range(1, 15)
    }
    monkeypatch.setattr(
        "app.services.market_clock._tw_market_holidays",
        lambda year: holidays if year == 2026 else frozenset(),
    )
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    last_open_bucket = datetime(2025, 12, 31, 10, 0, tzinfo=timezone.utc)
    _seed_tw_daily_snapshot(
        session_local,
        bucket_start=last_open_bucket,
        item_timestamp="2025-12-31T18:00:00+08:00",
        capture_scope="scheduler_bounded",
    )
    query = RankingQuery("TW", RankingType.net_buy, "TWSE")
    monkeypatch.setattr(snapshot_service_module, "_scheduled_queries", lambda: [query])
    provider = FakeProvider(
        bucket_start=last_open_bucket,
        error=RuntimeError("long holiday closures should not live-fetch"),
    )
    registry = FakeRegistry({("TW", "net_buy", "TWSE"): provider})
    service = RankingsSnapshotService(
        provider_registry=registry,
        scheduler_provider_registry=registry,
    )
    now = datetime.fromisoformat("2026-02-14T19:00:00+08:00")

    captured = service.capture_scheduled_snapshots(now=now, trigger="test")

    assert tw_institutional_daily_snapshot_bucket(now) is None
    assert captured == 0
    assert provider.fetch_calls == 0


