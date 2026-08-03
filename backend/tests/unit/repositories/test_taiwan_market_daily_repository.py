from __future__ import annotations

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

import pytest

from app.db.models import Base, TaiwanInstitutionalDaily, TaiwanMarginShortDaily
from app.repositories.taiwan_market_daily_repository import (
    TaiwanInstitutionalDailyRepository,
    TaiwanMarginShortDailyRepository,
)
from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord
from app.services.tw_margin_short_service import TaiwanMarginShortRecord


def _build_session_local():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    return session_local


def _institutional_record(
    *,
    symbol: str = "2330",
    trade_date: str = "2026-07-09",
    foreign_net_shares: int = 100,
) -> TaiwanInstitutionalInvestorRecord:
    return TaiwanInstitutionalInvestorRecord(
        date=trade_date,
        symbol=symbol,
        name="台積電",
        foreign_net_shares=foreign_net_shares,
        investment_trust_net_shares=50,
        dealer_self_net_shares=10,
        dealer_hedge_net_shares=20,
        dealer_net_shares=30,
        total_net_shares=foreign_net_shares + 80,
    )


def _margin_short_record(
    *,
    symbol: str = "2330",
    trade_date: str = "2026-07-09",
    margin_balance_lots: float | None = 12.0,
    short_margin_balance_lots: float | None = 3.0,
    borrowed_short_balance_lots: float | None = 4.0,
    borrowed_short_balance_change_lots: float | None = 0.5,
    borrowed_short_balance_change_amount_10k: int | None = 2,
    close_price: float | None = 950.0,
) -> TaiwanMarginShortRecord:
    return TaiwanMarginShortRecord(
        date=trade_date,
        symbol=symbol,
        name="台積電",
        close_price=close_price,
        margin_balance_lots=margin_balance_lots,
        short_margin_balance_lots=short_margin_balance_lots,
        borrowed_short_balance_lots=borrowed_short_balance_lots,
        borrowed_short_balance_change_lots=borrowed_short_balance_change_lots,
        borrowed_short_balance_change_amount_10k=borrowed_short_balance_change_amount_10k,
    )


def test_institutional_repository_upsert_is_idempotent() -> None:
    session_local = _build_session_local()
    repository = TaiwanInstitutionalDailyRepository()
    repository.set_session_factory_override(session_local)

    first = repository.upsert_records(
        market="TWSE",
        source="twse-t86",
        records=[_institutional_record(foreign_net_shares=100)],
    )
    second = repository.upsert_records(
        market="TWSE",
        source="twse-t86-updated",
        records=[_institutional_record(foreign_net_shares=250)],
    )

    assert first.inserted == 1
    assert first.updated == 0
    assert second.inserted == 0
    assert second.updated == 1

    history = repository.list_history(market="TWSE", symbol="2330", limit=5)
    assert len(history) == 1
    assert history[0].foreign_net_shares == 250
    assert history[0].source == "twse-t86-updated"
    assert history[0].total_net_shares == 330

    with session_local() as session:
        rows = session.execute(select(TaiwanInstitutionalDaily)).scalars().all()
        assert len(rows) == 1


def test_institutional_repository_allows_same_symbol_different_market() -> None:
    session_local = _build_session_local()
    repository = TaiwanInstitutionalDailyRepository()
    repository.set_session_factory_override(session_local)

    repository.upsert_records(
        market="TWSE",
        source="twse",
        records=[_institutional_record(symbol="6488")],
    )
    repository.upsert_records(
        market="TPEx",
        source="tpex",
        records=[_institutional_record(symbol="6488")],
    )

    assert len(repository.list_history(market="TWSE", symbol="6488", limit=5)) == 1
    assert len(repository.list_history(market="TPEx", symbol="6488", limit=5)) == 1


def test_margin_short_repository_upsert_is_idempotent() -> None:
    session_local = _build_session_local()
    repository = TaiwanMarginShortDailyRepository()
    repository.set_session_factory_override(session_local)

    first = repository.upsert_records(
        market="TWSE",
        source="twse-margin",
        records=[_margin_short_record(margin_balance_lots=12.0)],
    )
    second = repository.upsert_records(
        market="TWSE",
        source="twse-margin-updated",
        records=[_margin_short_record(margin_balance_lots=18.5)],
    )

    assert first.inserted == 1
    assert first.updated == 0
    assert second.inserted == 0
    assert second.updated == 1

    history = repository.list_history(market="TWSE", symbol="2330", limit=5)
    assert len(history) == 1
    assert history[0].margin_balance_lots == pytest.approx(18.5)
    assert history[0].source == "twse-margin-updated"

    with session_local() as session:
        rows = session.execute(select(TaiwanMarginShortDaily)).scalars().all()
        assert len(rows) == 1


def test_margin_short_repository_preserves_populated_fields_on_partial_upsert() -> None:
    session_local = _build_session_local()
    repository = TaiwanMarginShortDailyRepository()
    repository.set_session_factory_override(session_local)

    repository.upsert_records(
        market="TWSE",
        source="twse-complete",
        records=[_margin_short_record()],
    )
    repository.upsert_records(
        market="TWSE",
        source="twse-margin-only",
        records=[
            _margin_short_record(
                margin_balance_lots=20.0,
                short_margin_balance_lots=1.0,
                borrowed_short_balance_lots=None,
                borrowed_short_balance_change_lots=None,
                borrowed_short_balance_change_amount_10k=None,
                close_price=None,
            )
        ],
    )

    history = repository.list_history(market="TWSE", symbol="2330", limit=5)
    assert len(history) == 1
    assert history[0].margin_balance_lots == pytest.approx(20.0)
    assert history[0].short_margin_balance_lots == pytest.approx(1.0)
    assert history[0].borrowed_short_balance_lots == pytest.approx(4.0)
    assert history[0].borrowed_short_balance_change_lots == pytest.approx(0.5)
    assert history[0].borrowed_short_balance_change_amount_10k == 2
    assert history[0].close_price == pytest.approx(950.0)
    assert history[0].source == "twse-margin-only"


def test_repositories_empty_records_noop() -> None:
    session_local = _build_session_local()
    institutional = TaiwanInstitutionalDailyRepository()
    institutional.set_session_factory_override(session_local)
    margin = TaiwanMarginShortDailyRepository()
    margin.set_session_factory_override(session_local)

    assert institutional.upsert_records(market="TWSE", source="x", records=[]).inserted == 0
    assert margin.upsert_records(market="TWSE", source="x", records=[]).inserted == 0
