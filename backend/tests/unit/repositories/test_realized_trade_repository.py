from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.realized_trade_model import RealizedTrade
from app.repositories.realized_trade_repository import RealizedTradeRepository
from tests.support import install_in_memory_sessionlocal, seed_test_users


def _create_trade(
    repository: RealizedTradeRepository,
    *,
    user_id: str = "user-1",
    symbol: str = "AAPL",
    currency: str = "USD",
    buy_price: Decimal = Decimal("100"),
    sell_price: Decimal = Decimal("120"),
    shares: Decimal = Decimal("1"),
    cumulative_dividends: Decimal = Decimal("0"),
    buy_date: date | None = date(2026, 7, 1),
    sell_date: date = date(2026, 8, 1),
) -> RealizedTrade:
    return repository.create_trade(
        user_id=user_id,
        symbol=symbol,
        currency=currency,
        buy_price=buy_price,
        sell_price=sell_price,
        shares=shares,
        cumulative_dividends=cumulative_dividends,
        buy_date=buy_date,
        sell_date=sell_date,
    )


@pytest.fixture
def realized_trade_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RealizedTradeRepository, sessionmaker]:
    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-1", "user-2")
    return RealizedTradeRepository(), session_local


def test_realized_trade_repository_lists_filters_and_deletes_user_scoped_rows(
    realized_trade_repository: tuple[RealizedTradeRepository, sessionmaker],
) -> None:
    repository, _session_local = realized_trade_repository

    usd_trade = _create_trade(
        repository,
        buy_price=Decimal("180.25"),
        sell_price=Decimal("195.50"),
        shares=Decimal("10"),
        buy_date=date(2026, 7, 10),
        sell_date=date(2026, 8, 20),
    )
    twd_trade = _create_trade(
        repository,
        symbol="2330",
        currency="TWD",
        buy_price=Decimal("900"),
        sell_price=Decimal("1000"),
        shares=Decimal("1000"),
        buy_date=None,
        sell_date=date(2026, 8, 1),
    )
    _create_trade(
        repository,
        user_id="user-2",
        symbol="MSFT",
        buy_price=Decimal("300"),
        sell_price=Decimal("320"),
        shares=Decimal("5"),
        buy_date=None,
        sell_date=date(2026, 8, 15),
    )
    _create_trade(
        repository,
        sell_price=Decimal("160"),
        shares=Decimal("2"),
        buy_price=Decimal("150"),
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


def test_realized_trade_repository_update_applies_only_provided_fields(
    realized_trade_repository: tuple[RealizedTradeRepository, sessionmaker],
) -> None:
    repository, _session_local = realized_trade_repository
    trade = _create_trade(repository, shares=Decimal("2"))

    updated = repository.update_trade(
        "user-1",
        trade.id,
        {"sell_price": Decimal("150")},
    )

    assert updated is not None
    assert updated.sell_price == Decimal("150")
    assert updated.symbol == "AAPL"
    assert updated.buy_price == Decimal("100")
    assert updated.shares == Decimal("2")
    assert updated.buy_date == date(2026, 7, 1)
    assert updated.cumulative_dividends == Decimal("0")


def test_realized_trade_repository_persists_and_updates_cumulative_dividends(
    realized_trade_repository: tuple[RealizedTradeRepository, sessionmaker],
) -> None:
    repository, _session_local = realized_trade_repository
    trade = _create_trade(
        repository,
        cumulative_dividends=Decimal("18000"),
        buy_price=Decimal("800"),
        sell_price=Decimal("900"),
        shares=Decimal("1000"),
        currency="TWD",
        symbol="2330",
    )
    assert trade.cumulative_dividends == Decimal("18000")

    updated = repository.update_trade(
        "user-1",
        trade.id,
        {"cumulative_dividends": Decimal("20000")},
    )
    assert updated is not None
    assert updated.cumulative_dividends == Decimal("20000")


def test_realized_trade_repository_rejects_negative_cumulative_dividends(
    realized_trade_repository: tuple[RealizedTradeRepository, sessionmaker],
) -> None:
    repository, _session_local = realized_trade_repository
    with pytest.raises(IntegrityError):
        _create_trade(repository, cumulative_dividends=Decimal("-1"))


def test_realized_trade_repository_update_returns_none_for_other_user(
    realized_trade_repository: tuple[RealizedTradeRepository, sessionmaker],
) -> None:
    repository, session_local = realized_trade_repository
    trade = _create_trade(
        repository,
        buy_price=Decimal("10"),
        sell_price=Decimal("12"),
        buy_date=None,
    )

    assert (
        repository.update_trade(
            "user-2",
            trade.id,
            {
                "symbol": "AAPL",
                "currency": "USD",
                "buy_price": Decimal("10"),
                "sell_price": Decimal("12"),
                "shares": Decimal("1"),
                "buy_date": None,
                "sell_date": date(2026, 8, 1),
            },
        )
        is None
    )

    with session_local() as session:
        rows = list(session.query(RealizedTrade).all())
    assert len(rows) == 1
    assert rows[0].user_id == "user-1"
