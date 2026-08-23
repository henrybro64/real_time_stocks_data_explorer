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
