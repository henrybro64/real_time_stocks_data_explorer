"""Unit tests for rankings Azure SQL archive repository (#166)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.archive_models import ArchiveBase
from app.repositories.rankings_archive_repository import RankingsArchiveRepository
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse


def _session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    ArchiveBase.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _stock() -> StockResponse:
    return StockResponse(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def _response(*, last_price: float = 190.0) -> RankingResponse:
    return RankingResponse(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        universe="DJIA",
        items=[
            RankingItem(
                rank=1,
                stock=_stock(),
                lastPrice=last_price,
                changePercent=1.25,
                volume=1_000_000,
                volumeDeltaPercent=3.0,
                netBuySellShares=None,
                peRatio=28.5,
                peRatioType="trailing",
                peRatioSource="yahoo",
                valuationAsOf="2026-08-18",
                valuationWarning=None,
                timestamp="2026-08-18T16:00:00-04:00",
            )
        ],
        timestamp="2026-08-18T20:00:00+00:00",
        source="unit-test",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="market closed",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope="DJIA",
        twRankingsScope=None,
    )


def test_upsert_eod_snapshot_is_idempotent() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())

    trading_date = date(2026, 8, 18)
    first = repo.upsert_eod_snapshot(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        trading_date=trading_date,
        response=_response(last_price=190.0),
        captured_at=datetime(2026, 8, 18, 20, 45, tzinfo=timezone.utc),
    )
    second = repo.upsert_eod_snapshot(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        trading_date=trading_date,
        response=_response(last_price=999.0),
    )

    assert first is not None and second is not None
    assert first.snapshot.id == second.snapshot.id
    assert len(second.items) == 1
    assert second.items[0].last_price == 190.0
    assert second.items[0].pe_ratio == 28.5


def test_load_eod_snapshot_returns_none_when_missing() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())

    loaded = repo.load_eod_snapshot(
        market="TW",
        universe="TWSE",
        ranking_type=RankingType.net_buy,
        trading_date=date(2026, 8, 18),
    )
    assert loaded is None


def test_unavailable_archive_fails_gracefully() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(None)

    assert repo.is_available() is False
    assert (
        repo.upsert_eod_snapshot(
            market="US",
            universe="DJIA",
            ranking_type=RankingType.volume,
            trading_date=date(2026, 8, 18),
            response=_response(),
        )
        is None
    )


def test_to_ranking_response_preserves_rank_order() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())

    response = RankingResponse(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        universe="DJIA",
        items=[
            RankingItem(
                rank=1,
                stock=_stock(),
                lastPrice=190.0,
                changePercent=1.25,
                volume=1_000_000,
                volumeDeltaPercent=3.0,
                netBuySellShares=None,
                peRatio=28.5,
                peRatioType="trailing",
                peRatioSource="yahoo",
                valuationAsOf="2026-08-18",
                valuationWarning=None,
                timestamp="2026-08-18T16:00:00-04:00",
            ),
            RankingItem(
                rank=2,
                stock=StockResponse(
                    id="US:MSFT",
                    market="US",
                    symbol="MSFT",
                    name="Microsoft",
                    exchange="NASDAQ",
                    currency="USD",
                    timezone="America/New_York",
                ),
                lastPrice=400.0,
                changePercent=0.5,
                volume=800_000,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2026-08-18T16:00:00-04:00",
            ),
        ],
        timestamp="2026-08-18T20:00:00+00:00",
        source="unit-test",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="market closed",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope="djia",
        twRankingsScope=None,
    )
    envelope = repo.upsert_eod_snapshot(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        trading_date=date(2026, 8, 18),
        response=response,
        captured_at=datetime(2026, 8, 18, 20, 45, tzinfo=timezone.utc),
    )
    assert envelope is not None
    reconstructed = repo.to_ranking_response(envelope)
    assert [item.stock.symbol for item in reconstructed.items] == ["AAPL", "MSFT"]
    assert reconstructed.universe == "DJIA"
    assert reconstructed.usRankingsScope == "djia"
    assert reconstructed.items[0].peRatio == 28.5
    assert reconstructed.items[0].stock.exchange == "NASDAQ"
    # SQLite returns naive captured_at; reconstruct must treat it as UTC (not local TZ).
    assert reconstructed.timestamp.startswith("2026-08-18T20:45:00")


def test_to_ranking_response_preserves_exchange_and_tw_yahoo_scope() -> None:
    repo = RankingsArchiveRepository()
    repo.set_session_factory_override(_session_factory())

    us = RankingResponse(
        market="US",
        rankingType=RankingType.volume,
        twBoard=None,
        universe="DJIA",
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id="US:IBM",
                    market="US",
                    symbol="IBM",
                    name="IBM",
                    exchange="NYSE",
                    currency="USD",
                    timezone="America/New_York",
                ),
                lastPrice=180.0,
                changePercent=0.2,
                volume=1000,
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
        usRankingsScope="djia",
        twRankingsScope=None,
    )
    us_env = repo.upsert_eod_snapshot(
        market="US",
        universe="DJIA",
        ranking_type=RankingType.volume,
        trading_date=date(2026, 8, 18),
        response=us,
    )
    assert us_env is not None
    assert repo.to_ranking_response(us_env).items[0].stock.exchange == "NYSE"

    tw = RankingResponse(
        market="TW",
        rankingType=RankingType.volume,
        twBoard="TWSE",
        universe="TWSE",
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
                lastPrice=1000.0,
                changePercent=1.0,
                volume=100,
                volumeDeltaPercent=None,
                netBuySellShares=None,
                peRatio=None,
                peRatioType=None,
                peRatioSource=None,
                valuationAsOf=None,
                valuationWarning=None,
                timestamp="2026-08-18T13:30:00+08:00",
            )
        ],
        timestamp="2026-08-18T06:00:00+00:00",
        source="yahoo-pre-ranked",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=False,
        dataFreshnessLabel="closed",
        warningMessage=None,
        unsupportedReason=None,
        usRankingsScope=None,
        twRankingsScope="yahoo",
    )
    tw_env = repo.upsert_eod_snapshot(
        market="TW",
        universe="TWSE",
        ranking_type=RankingType.volume,
        trading_date=date(2026, 8, 18),
        response=tw,
    )
    assert tw_env is not None
    assert repo.to_ranking_response(tw_env).twRankingsScope == "yahoo"
