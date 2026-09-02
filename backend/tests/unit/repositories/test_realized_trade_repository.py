from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, RealizedTrade, User
from app.repositories.realized_trade_repository import RealizedTradeRepository


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


def test_realized_trade_repository_lists_filters_and_deletes_user_scoped_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_local = _build_session_local()
    monkeypatch.setattr(
        "app.repositories.realized_trade_repository.require_session_factory",
        lambda: session_local,
    )
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.add(User(id="user-2", username="user-2", password_hash=None))
        session.commit()

    repository = RealizedTradeRepository()

    usd_trade = repository.create_trade(
        user_id="user-1",
        symbol="AAPL",
        currency="USD",
        buy_price=Decimal("180.25"),
        sell_price=Decimal("195.50"),
        shares=Decimal("10"),
        buy_date=date(2026, 7, 10),
        sell_date=date(2026, 8, 20),
    )
    twd_trade = repository.create_trade(
        user_id="user-1",
        symbol="2330",
        currency="TWD",
        buy_price=Decimal("900"),
        sell_price=Decimal("1000"),
        shares=Decimal("1000"),
        buy_date=None,
        sell_date=date(2026, 8, 1),
    )
    repository.create_trade(
        user_id="user-2",
        symbol="MSFT",
        currency="USD",
        buy_price=Decimal("300"),
        sell_price=Decimal("320"),
        shares=Decimal("5"),
        buy_date=None,
        sell_date=date(2026, 8, 15),
    )
    repository.create_trade(
        user_id="user-1",
        symbol="AAPL",
        currency="USD",
        buy_price=Decimal("150"),
        sell_price=Decimal("160"),
        shares=Decimal("2"),
        buy_date=None,
        sell_date=date(2026, 9, 1),
    )

    all_user_one = repository.list_trades("user-1")
    assert len(all_user_one) == 3
    assert all_user_one[0].sell_date == date(2026, 9, 1)
    assert all_user_one[1].sell_date == date(2026, 8, 20)
    assert all_user_one[2].sell_date == date(2026, 8, 1)
    usd_rows = repository.list_trades("user-1", currency="USD")
    assert len(usd_rows) == 2
    assert all(row.currency == "USD" for row in usd_rows)
    assert repository.list_trades("user-1", currency="TWD")[0].id == twd_trade.id
    assert len(repository.list_trades("user-2")) == 1

    assert repository.delete_trade("user-1", twd_trade.id) is True
    assert repository.delete_trade("user-1", twd_trade.id) is False
    assert repository.get_trade("user-2", twd_trade.id) is None
    assert repository.list_trades("user-1", currency="TWD") == []


def test_realized_trade_repository_update_returns_none_for_other_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_local = _build_session_local()
    monkeypatch.setattr(
        "app.repositories.realized_trade_repository.require_session_factory",
        lambda: session_local,
    )
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.add(User(id="user-2", username="user-2", password_hash=None))
        session.commit()

    repository = RealizedTradeRepository()
    trade = repository.create_trade(
        user_id="user-1",
        symbol="AAPL",
        currency="USD",
        buy_price=Decimal("10"),
        sell_price=Decimal("12"),
        shares=Decimal("1"),
        buy_date=None,
        sell_date=date(2026, 8, 1),
    )

    assert (
        repository.update_trade(
            "user-2",
            trade.id,
            symbol="AAPL",
            currency="USD",
            buy_price=Decimal("10"),
            sell_price=Decimal("12"),
            shares=Decimal("1"),
            buy_date=None,
            sell_date=date(2026, 8, 1),
        )
        is None
    )

    with session_local() as session:
        rows = list(session.query(RealizedTrade).all())
    assert len(rows) == 1
    assert rows[0].user_id == "user-1"
