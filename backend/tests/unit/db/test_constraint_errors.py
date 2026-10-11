from sqlalchemy.exc import IntegrityError

from app.db.constraint_errors import (
    is_user_stock_unique_violation,
    is_username_unique_violation,
)


def _integrity(message: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception(message))


def test_postgres_unique_constraint_name_matches() -> None:
    exc = _integrity(
        'duplicate key value violates unique constraint "uq_portfolio_positions_user_stock"'
    )
    assert is_user_stock_unique_violation(
        exc,
        constraint="uq_portfolio_positions_user_stock",
        table="portfolio_positions",
    )


def test_sqlite_unique_failure_matches_table_and_stock_id() -> None:
    exc = _integrity(
        "UNIQUE constraint failed: watchlist_items.user_id, watchlist_items.stock_id"
    )
    assert is_user_stock_unique_violation(
        exc,
        constraint="uq_watchlist_items_user_stock",
        table="watchlist_items",
    )


def test_username_unique_violation_matches_postgres_and_sqlite() -> None:
    postgres = _integrity(
        'duplicate key value violates unique constraint "users_username_key"'
    )
    sqlite = _integrity("UNIQUE constraint failed: users.username")
    assert is_username_unique_violation(postgres)
    assert is_username_unique_violation(sqlite)


def test_username_helper_ignores_other_unique_failures() -> None:
    exc = _integrity(
        "UNIQUE constraint failed: watchlist_items.user_id, watchlist_items.stock_id"
    )
    assert not is_username_unique_violation(exc)


def test_foreign_key_failure_is_not_a_duplicate_stock() -> None:
    exc = _integrity(
        'insert or update on table "portfolio_positions" violates foreign key '
        'constraint "portfolio_positions_user_id_fkey"'
    )
    assert not is_user_stock_unique_violation(
        exc,
        constraint="uq_portfolio_positions_user_stock",
        table="portfolio_positions",
    )
