from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, User
from app.repositories.portfolio_repository import PortfolioRepository


def _install_in_memory_sessionlocal(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("app.repositories.session.SessionLocal", session_local)
    return session_local


def test_portfolio_repository_list_distinct_position_stock_ids(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
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
