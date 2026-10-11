from app.repositories.watchlist_repository import watchlist_repository
from app.services.quote_refresher_service import quote_refresher_service
from app.services.quote_service import quote_service
from app.services.stock_ids import removal_stock_ids
from app.services.user_quota_service import enforce_watchlist_cap


class WatchlistService:
    def get_ids(self, user_id: str) -> list[str]:
        return watchlist_repository.list_ids(user_id)

    def add(self, user_id: str, stock_id: str) -> list[str]:
        stock = quote_service.get_stock(stock_id)
        if stock is None:
            raise ValueError(f"Unknown stock id: {stock_id}")
        # Persist the canonical id so US:aapl and US:AAPL share one row (#275).
        canonical_id = stock.id
        current = self.get_ids(user_id)
        if canonical_id in current:
            return current
        enforce_watchlist_cap(user_id, current_count=len(current))
        watchlist_repository.add_if_missing(user_id, canonical_id)
        quote_refresher_service.request_immediate_refresh(canonical_id)
        return self.get_ids(user_id)

    def remove(self, user_id: str, stock_id: str) -> list[str]:
        stock = quote_service.get_stock(stock_id)
        for candidate in removal_stock_ids(
            stock_id,
            stock.id if stock is not None else None,
        ):
            watchlist_repository.remove(user_id, candidate)
        return self.get_ids(user_id)


watchlist_service = WatchlistService()
