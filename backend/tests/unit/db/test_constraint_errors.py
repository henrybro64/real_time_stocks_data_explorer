from sqlalchemy.exc import IntegrityError

from app.db.constraint_errors import (
    is_oauth_identity_unique_violation,
    is_user_stock_unique_violation,
    is_username_unique_violation,
    safe_constraint_name,
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


def test_oauth_identity_unique_violation_matches_postgres_and_sqlite() -> None:
    postgres = _integrity(
        'duplicate key value violates unique constraint "uq_oauth_identities_provider_subject"'
    )
    sqlite = _integrity(
        "UNIQUE constraint failed: oauth_identities.provider, oauth_identities.subject"
    )
    fk = _integrity(
        'insert or update on table "oauth_identities" violates foreign key '
        'constraint "oauth_identities_user_id_fkey"'
    )
    assert is_oauth_identity_unique_violation(postgres)
    assert is_oauth_identity_unique_violation(sqlite)
    assert not is_oauth_identity_unique_violation(fk)
    assert not is_username_unique_violation(postgres)


def test_username_helper_ignores_other_unique_failures() -> None:
    exc = _integrity(
        "UNIQUE constraint failed: watchlist_items.user_id, watchlist_items.stock_id"
    )
    assert not is_username_unique_violation(exc)


def test_safe_constraint_name_returns_the_identifier_without_the_detail() -> None:
    message = (
        'insert or update on table "oauth_identities" violates foreign key '
        'constraint "oauth_identities_user_id_fkey"\n'
        'DETAIL: Key (user_id)=(u-secret-user) is not present in table "users".'
    )
    assert safe_constraint_name(_integrity(message)) == "oauth_identities_user_id_fkey"
    assert "u-secret-user" not in (safe_constraint_name(_integrity(message)) or "")
    assert safe_constraint_name(_integrity("UNIQUE constraint failed: users.username")) is None
    assert safe_constraint_name(None) is None

    class _Diag:
        constraint_name = "users_username_key"

    class _Orig(Exception):
        diag = _Diag()

    named = IntegrityError("INSERT", {}, _Orig("DETAIL: Key (user_id)=(u-secret-user)"))
    assert safe_constraint_name(named) == "users_username_key"


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
