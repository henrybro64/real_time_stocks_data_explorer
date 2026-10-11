from sqlalchemy import delete, func, select

from app.db.models import WatchlistItem

from .session import database_is_configured, require_session_factory


class WatchlistRepository:
    def is_available(self) -> bool:
        return database_is_configured()

    def count_for_user(self, user_id: str) -> int:
        session_factory = require_session_factory()
        with session_factory() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(WatchlistItem)
                    .where(WatchlistItem.user_id == user_id),
                ).scalar_one()
            )

    def list_ids(self, user_id: str) -> list[str]:
        session_factory = require_session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(WatchlistItem.stock_id)
                .where(WatchlistItem.user_id == user_id)
                .order_by(WatchlistItem.created_at.asc()),
            ).all()
        return [row[0] for row in rows]

    def list_distinct_stock_ids(self) -> list[str]:
        """Return every stock ID present on any user's watchlist (deduplicated)."""
        session_factory = require_session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(WatchlistItem.stock_id)
                .distinct()
                .order_by(WatchlistItem.stock_id.asc()),
            ).all()
        return [row[0] for row in rows]

    def add_if_missing(self, user_id: str, stock_id: str) -> None:
        from app.services.user_quota_service import (
            enforce_watchlist_cap,
            lock_owner_row,
            user_quota_write,
        )

        with user_quota_write("watchlist", user_id):
            session_factory = require_session_factory()
            with session_factory() as session:
                lock_owner_row(session, user_id)
                existing = session.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.user_id == user_id,
                        WatchlistItem.stock_id == stock_id,
                    ),
                ).scalar_one_or_none()
                if existing is not None:
                    return
                current_count = int(
                    session.execute(
                        select(func.count())
                        .select_from(WatchlistItem)
                        .where(WatchlistItem.user_id == user_id),
                    ).scalar_one()
                )
                enforce_watchlist_cap(user_id, current_count=current_count)
                session.add(
                    WatchlistItem(
                        user_id=user_id,
                        stock_id=stock_id,
                    ),
                )
                try:
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    # Concurrent adds race past the existence check (#275).
                    from app.db.constraint_errors import is_user_stock_unique_violation

                    if is_user_stock_unique_violation(
                        exc,
                        constraint="uq_watchlist_items_user_stock",
                        table="watchlist_items",
                    ):
                        return
                    raise

    def remove(self, user_id: str, stock_id: str) -> None:
        session_factory = require_session_factory()
        with session_factory() as session:
            session.execute(
                delete(WatchlistItem).where(
                    WatchlistItem.user_id == user_id,
                    WatchlistItem.stock_id == stock_id,
                ),
            )
            session.commit()


watchlist_repository = WatchlistRepository()

