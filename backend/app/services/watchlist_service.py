from sqlalchemy import delete, select

from app.db.models import WatchlistItem
from app.db.session import SessionLocal
from app.services.quote_service import quote_service


class WatchlistService:
    def get_ids(self, user_id: str) -> list[str]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            rows = session.execute(
                select(WatchlistItem.stock_id)
                .where(WatchlistItem.user_id == user_id)
                .order_by(WatchlistItem.created_at.asc()),
            ).all()
        return [row[0] for row in rows]

    def add(self, user_id: str, stock_id: str) -> list[str]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        if quote_service.get_stock(stock_id) is None:
            raise ValueError(f"Unknown stock id: {stock_id}")
        with SessionLocal() as session:
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
        return self.get_ids(user_id)

    def remove(self, user_id: str, stock_id: str) -> list[str]:
        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not configured")
        with SessionLocal() as session:
            session.execute(
                delete(WatchlistItem).where(
                    WatchlistItem.user_id == user_id,
                    WatchlistItem.stock_id == stock_id,
                ),
            )
            session.commit()
        return self.get_ids(user_id)


watchlist_service = WatchlistService()
