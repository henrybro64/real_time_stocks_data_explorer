from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, User
from app.repositories.watchlist_repository import WatchlistRepository


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


def test_watchlist_repository_add_list_and_remove(monkeypatch) -> None:
    session_local = _install_in_memory_sessionlocal(monkeypatch)
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.commit()

    repository = WatchlistRepository()

    assert repository.is_available() is True
    assert repository.list_ids("user-1") == []

    repository.add_if_missing("user-1", "US:AAPL")
    repository.add_if_missing("user-1", "US:MSFT")
    repository.add_if_missing("user-1", "US:AAPL")

    assert repository.list_ids("user-1") == ["US:AAPL", "US:MSFT"]

    repository.remove("user-1", "US:AAPL")

    assert repository.list_ids("user-1") == ["US:MSFT"]


def test_watchlist_repository_requires_database(monkeypatch) -> None:
    monkeypatch.setattr("app.repositories.session.SessionLocal", None)
    repository = WatchlistRepository()

    assert repository.is_available() is False
