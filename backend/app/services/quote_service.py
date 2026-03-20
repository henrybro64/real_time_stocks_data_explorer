from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.providers.tw_provider import TWProvider
from app.services.providers.us_provider import USProvider


class QuoteService:
    def __init__(self) -> None:
        self.providers = {
            "US": USProvider(),
            "TW": TWProvider(),
        }

    def list_stocks(self) -> list[StockRecord]:
        stocks: list[StockRecord] = []
        for provider in self.providers.values():
            stocks.extend(provider.list_stocks())
        return stocks

    def search(self, query: str) -> list[StockRecord]:
        if not query.strip():
            return self.list_stocks()
        matches: list[StockRecord] = []
        for provider in self.providers.values():
            matches.extend(provider.search(query))
        return matches

    def get_stock(self, stock_id: str) -> StockRecord | None:
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        return provider.get_stock(stock_id) if provider else None

    def get_quote(self, stock_id: str) -> QuoteRecord | None:
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        return provider.get_quote(stock_id) if provider else None

    def get_quotes(self, stock_ids: list[str]) -> list[tuple[StockRecord, QuoteRecord]]:
        envelopes: list[tuple[StockRecord, QuoteRecord]] = []
        for stock_id in stock_ids:
            stock = self.get_stock(stock_id)
            quote = self.get_quote(stock_id)
            if stock and quote:
                envelopes.append((stock, quote))
        return envelopes

    def get_history(self, stock_id: str, range_name: str) -> tuple[StockRecord, list[CandleRecord]] | None:
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        if provider is None:
            return None
        stock = provider.get_stock(stock_id)
        if stock is None:
            return None
        return stock, provider.get_history(stock_id, range_name)

    @staticmethod
    def _market_from_id(stock_id: str) -> str:
        return stock_id.split(":", 1)[0]


quote_service = QuoteService()

