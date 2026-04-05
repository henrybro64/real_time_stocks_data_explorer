from __future__ import annotations

from app.db.init_ids import DEFAULT_USER_ID
from app.models.watchlist import WatchlistRecord


class WatchlistStore:
    """Per-user in-memory watchlists (MVP). When auth is off, only default-user is used."""

    def __init__(self) -> None:
        self._by_user: dict[str, WatchlistRecord] = {
            DEFAULT_USER_ID: WatchlistRecord(ids=["US:AAPL", "TW:2330"]),
        }

    def record_for(self, user_id: str) -> WatchlistRecord:
        if user_id not in self._by_user:
            self._by_user[user_id] = WatchlistRecord(ids=[])
        return self._by_user[user_id]


watchlist_store = WatchlistStore()
