from collections.abc import Callable

from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.providers.tw_provider import TWProvider
from app.services.providers.us_provider import USProvider
from app.services.quote_redis_cache import get_cached_quote, set_cached_quote


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
        normalized_query = query.strip()
        if not normalized_query:
            return self.list_stocks()
        matches: list[StockRecord] = []
        for provider in self._providers_for_search_query(normalized_query):
            matches.extend(provider.search(normalized_query))
        return matches

    def get_stock(self, stock_id: str) -> StockRecord | None:
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        return provider.get_stock(stock_id) if provider else None

    def get_quote(self, stock_id: str) -> QuoteRecord | None:
        cached = get_cached_quote(stock_id)
        if cached is not None:
            return cached
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        quote = provider.get_quote(stock_id) if provider else None
        if quote is not None:
            set_cached_quote(stock_id, quote)
        return quote

    def get_quotes(self, stock_ids: list[str]) -> list[tuple[StockRecord, QuoteRecord]]:
        return self._collect_quotes(stock_ids, self.get_quote)

    def get_live_quote(self, stock_id: str) -> QuoteRecord | None:
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        if provider is None:
            return None

        live_quote_fetcher = getattr(provider, "get_live_quote", None)
        if market == "US" and callable(live_quote_fetcher):
            return live_quote_fetcher(stock_id)

        return provider.get_quote(stock_id)

    def get_live_quotes(self, stock_ids: list[str]) -> list[tuple[StockRecord, QuoteRecord]]:
        return self._collect_quotes(stock_ids, self.get_live_quote)

    def _collect_quotes(
        self,
        stock_ids: list[str],
        fetcher: Callable[[str], QuoteRecord | None],
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        envelopes: list[tuple[StockRecord, QuoteRecord]] = []
        for stock_id in stock_ids:
            stock = self.get_stock(stock_id)
            quote = fetcher(stock_id)
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

    def _providers_for_search_query(self, query: str):
        # Taiwan equities/ETFs use numeric symbols; avoid slow, irrelevant US catalog
        # lookups when the user is clearly searching for a Taiwan code like "2330".
        if query.isdigit():
            tw_provider = self.providers.get("TW")
            return [tw_provider] if tw_provider is not None else []
        return list(self.providers.values())


quote_service = QuoteService()
