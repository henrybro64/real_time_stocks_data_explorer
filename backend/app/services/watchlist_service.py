from app.db.memory_store import watchlist_store
from app.services.quote_service import quote_service


class WatchlistService:
    def get_ids(self) -> list[str]:
        return watchlist_store.ids

    def add(self, stock_id: str) -> list[str]:
        if quote_service.get_stock(stock_id) is None:
            raise ValueError(f"Unknown stock id: {stock_id}")
        if stock_id not in watchlist_store.ids:
            watchlist_store.ids.append(stock_id)
        return watchlist_store.ids

    def remove(self, stock_id: str) -> list[str]:
        watchlist_store.ids = [item for item in watchlist_store.ids if item != stock_id]
        return watchlist_store.ids


watchlist_service = WatchlistService()

