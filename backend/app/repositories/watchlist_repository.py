from sqlalchemy import delete, select

from app.db.models import WatchlistItem

from .session import database_is_configured, require_session_factory


class WatchlistRepository:
    def is_available(self) -> bool:
        return database_is_configured()

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
        session_factory = require_session_factory()
        with session_factory() as session:
            existing = session.execute(
                select(WatchlistItem).where(
                    WatchlistItem.user_id == user_id,
                    WatchlistItem.stock_id == stock_id,
                ),
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    WatchlistItem(
                        user_id=user_id,
                        stock_id=stock_id,
                    ),
                )
                session.commit()

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

