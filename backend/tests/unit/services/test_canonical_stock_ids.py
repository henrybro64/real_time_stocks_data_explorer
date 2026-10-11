from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import User
from app.repositories.watchlist_repository import WatchlistRepository
from app.services.portfolio_service import portfolio_service
from app.services.stock_ids import canonical_stored_stock_id, removal_stock_ids
from app.services.watchlist_service import watchlist_service
from tests.support import install_in_memory_sessionlocal, stock_record


def test_canonical_stored_stock_id_uppercases_us_symbols() -> None:
    assert canonical_stored_stock_id("US:aapl") == "US:AAPL"
    assert canonical_stored_stock_id(" tw:2330 ") == "TW:2330"
    assert removal_stock_ids("US:aapl", "US:AAPL") == ["US:aapl", "US:AAPL"]


def test_watchlist_add_and_remove_share_the_canonical_id(monkeypatch) -> None:
    session_local = install_in_memory_sessionlocal(monkeypatch)
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.commit()

    apple = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    monkeypatch.setattr(
        "app.services.watchlist_service.quote_service.get_stock",
        lambda stock_id: apple if "aapl" in stock_id.lower() else None,
    )
    monkeypatch.setattr(
        "app.services.watchlist_service.quote_refresher_service.request_immediate_refresh",
        lambda stock_id: None,
    )

    assert watchlist_service.add("user-1", "US:aapl") == ["US:AAPL"]
    assert watchlist_service.remove("user-1", "US:aapl") == []


def test_watchlist_unique_race_is_ignored_and_foreign_key_is_not(monkeypatch) -> None:
    session_local = install_in_memory_sessionlocal(monkeypatch)
    with session_local() as session:
        session.add(User(id="user-1", username="user-1", password_hash=None))
        session.commit()

    repository = WatchlistRepository()
    unique = IntegrityError(
        "INSERT",
        {},
        Exception(
            "UNIQUE constraint failed: watchlist_items.user_id, watchlist_items.stock_id"
        ),
    )
    foreign_key = IntegrityError(
        "INSERT",
        {},
        Exception("FOREIGN KEY constraint failed"),
    )

    def raise_unique(self):
        raise unique

    monkeypatch.setattr(Session, "commit", raise_unique)
    repository.add_if_missing("user-1", "US:AAPL")

    def raise_foreign_key(self):
        raise foreign_key

    monkeypatch.setattr(Session, "commit", raise_foreign_key)
    try:
        repository.add_if_missing("user-1", "US:MSFT")
    except IntegrityError as exc:
        assert exc is foreign_key
    else:
        raise AssertionError("foreign key IntegrityError should propagate")


def test_add_position_stores_canonical_id_and_maps_only_unique_conflicts(
    monkeypatch,
) -> None:
    apple = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.portfolio_service.quote_service.get_stock",
        lambda stock_id: apple,
    )
    monkeypatch.setattr(
        "app.services.portfolio_service.portfolio_repository.find_position_by_stock",
        lambda user_id, stock_id: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_service.portfolio_repository.count_positions",
        lambda user_id: 0,
    )
    monkeypatch.setattr(
        "app.services.portfolio_service.enforce_portfolio_position_cap",
        lambda user_id, current_count: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_service.quote_refresher_service.request_immediate_refresh",
        lambda stock_id: None,
    )

    def create_position(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1, **kwargs)

    monkeypatch.setattr(
        "app.services.portfolio_service.portfolio_repository.create_position",
        create_position,
    )
    portfolio_service.add_position("user-1", "US:aapl", 1, 10, "USD", None)
    assert captured["stock_id"] == "US:AAPL"

    def unique_conflict(**kwargs):
        raise IntegrityError(
            "INSERT",
            {},
            Exception(
                'duplicate key value violates unique constraint '
                '"uq_portfolio_positions_user_stock"'
            ),
        )

    monkeypatch.setattr(
        "app.services.portfolio_service.portfolio_repository.create_position",
        unique_conflict,
    )
    try:
        portfolio_service.add_position("user-1", "US:aapl", 1, 10, "USD", None)
    except ValueError as exc:
        assert "US:AAPL" in str(exc)
    else:
        raise AssertionError("unique conflict should be a ValueError")

    def foreign_key_conflict(**kwargs):
        raise IntegrityError(
            "INSERT",
            {},
            Exception(
                'violates foreign key constraint "portfolio_positions_user_id_fkey"'
            ),
        )

    monkeypatch.setattr(
        "app.services.portfolio_service.portfolio_repository.create_position",
        foreign_key_conflict,
    )
    try:
        portfolio_service.add_position("user-1", "US:aapl", 1, 10, "USD", None)
    except IntegrityError:
        return
    raise AssertionError("foreign key IntegrityError should propagate")
