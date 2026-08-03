from __future__ import annotations

from app.db.models import User
from app.repositories.portfolio_repository import PortfolioRepository
from tests.support import install_in_memory_sessionlocal


def test_portfolio_repository_list_distinct_position_stock_ids(monkeypatch) -> None:
    session_local = install_in_memory_sessionlocal(monkeypatch)
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.add(User(id="user-2", username="user-2", password_hash=None))
        session.commit()

    repository = PortfolioRepository()
    repository.create_position(
        user_id="user-1",
        stock_id="US:AAPL",
        quantity=1,
        average_cost=10,
        currency="USD",
        notes=None,
    )
    repository.create_position(
        user_id="user-1",
        stock_id="US:MSFT",
        quantity=2,
        average_cost=20,
        currency="USD",
        notes=None,
    )
    repository.create_position(
        user_id="user-2",
        stock_id="US:AAPL",
        quantity=3,
        average_cost=30,
        currency="USD",
        notes=None,
    )

    assert repository.list_distinct_position_stock_ids() == ["US:AAPL", "US:MSFT"]
