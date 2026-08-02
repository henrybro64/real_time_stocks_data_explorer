from typing import Protocol

from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord


class StockProvider(Protocol):
    market: str

    def list_stocks(self) -> list[StockRecord]: ...

    def search(self, query: str) -> list[StockRecord]: ...

    def get_stock(self, stock_id: str) -> StockRecord | None: ...

    def get_quote(self, stock_id: str) -> QuoteRecord | None: ...

    def get_history(
        self,
        stock_id: str,
        range_name: str,
        *,
        limit: int | None = None,
    ) -> list[CandleRecord]: ...

