from app.repositories.watchlist_repository import watchlist_repository
from app.services.quote_refresher_service import quote_refresher_service
from app.services.quote_service import quote_service


class WatchlistService:
    def get_ids(self, user_id: str) -> list[str]:
        return watchlist_repository.list_ids(user_id)

    def add(self, user_id: str, stock_id: str) -> list[str]:
        if quote_service.get_stock(stock_id) is None:
            raise ValueError(f"Unknown stock id: {stock_id}")
        watchlist_repository.add_if_missing(user_id, stock_id)
        quote_refresher_service.request_immediate_refresh(stock_id)
        return self.get_ids(user_id)

    def remove(self, user_id: str, stock_id: str) -> list[str]:
        watchlist_repository.remove(user_id, stock_id)
        return self.get_ids(user_id)


watchlist_service = WatchlistService()
