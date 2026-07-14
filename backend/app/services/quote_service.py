from collections.abc import Callable
import logging
from time import perf_counter

from app.core.diagnostics import elapsed_ms
from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.providers.tw_provider import TWProvider
from app.services.providers.us_provider import USProvider
from app.services.quote_redis_cache import get_cached_quote, set_cached_quote

logger = logging.getLogger(__name__)


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

    def resolve_stock_id(self, raw: str) -> str:
        """Resolve a user-facing ticker or canonical ID to ``MARKET:SYMBOL``.

        Accepts values like ``AAPL``, ``aapl``, ``US:AAPL``, and ``2330``.
        Raises ``ValueError`` when the input is unknown or ambiguous across markets.

        Shared algorithm with frontend ``resolveAlertStockId``:
        1. Canonical ``MARKET:SYMBOL`` (verify via ``get_stock``)
        2. All-digit bare input → ``TW:{digits}`` (verify via ``get_stock``)
        3. Prefer ``US:{SYMBOL}`` via ``get_stock`` before search ambiguity
        4. Exact symbol match / single search hit; refuse silent multi-match picks
        """
        query = raw.strip()
        if not query:
            raise ValueError(f"Unknown stock id: {raw}")

        if ":" in query:
            return self._resolve_canonical_stock_id(query)
        if query.isdigit():
            return self._require_existing_stock(f"TW:{query}", query)

        us_candidate = f"US:{query.upper()}"
        if self.get_stock(us_candidate) is not None:
            return us_candidate
        return self._resolve_stock_id_from_search(query)

    def _resolve_canonical_stock_id(self, query: str) -> str:
        market_raw, symbol_raw = query.split(":", 1)
        market = market_raw.strip().upper()
        symbol = symbol_raw.strip()
        if market not in self.providers:
            raise ValueError(f"Unknown stock id: {query}")
        if market == "US":
            symbol = symbol.upper()
        return self._require_existing_stock(f"{market}:{symbol}", query)

    def _require_existing_stock(self, candidate: str, display: str) -> str:
        if self.get_stock(candidate) is not None:
            return candidate
        raise ValueError(f"Unknown stock id: {display}")

    def _resolve_stock_id_from_search(self, query: str) -> str:
        matches = self.search(query)
        exact_symbol_matches = [
            stock for stock in matches if stock.symbol.upper() == query.upper()
        ]
        resolved = self._pick_unique_stock_id(exact_symbol_matches, query)
        if resolved is not None:
            return resolved
        resolved = self._pick_unique_stock_id(matches, query, limit=5)
        if resolved is not None:
            return resolved
        raise ValueError(f"Unknown stock id: {query}")

    @staticmethod
    def _pick_unique_stock_id(
        stocks: list[StockRecord],
        query: str,
        *,
        limit: int | None = None,
    ) -> str | None:
        if len(stocks) == 1:
            return stocks[0].id
        if len(stocks) > 1:
            ids = {stock.id for stock in (stocks if limit is None else stocks[:limit])}
            choices = ", ".join(sorted(ids))
            raise ValueError(
                f'Ambiguous stock symbol "{query}". Choose one of: {choices}'
            )
        return None

    def get_quote(self, stock_id: str) -> QuoteRecord | None:
        cached = get_cached_quote(stock_id)
        if cached is not None:
            logger.debug(
                "Quote cache hit",
                extra={
                    "event": "quote.cache.hit",
                    "stock_id": stock_id,
                },
            )
            return cached
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        if provider is None:
            logger.warning(
                "Quote provider is unavailable",
                extra={
                    "event": "quote.fetch.provider_missing",
                    "stock_id": stock_id,
                    "provider": market,
                },
            )
            return None

        started_at = perf_counter()
        try:
            quote = provider.get_quote(stock_id)
        except Exception as exc:
            logger.exception(
                "Quote provider raised an unexpected error",
                extra={
                    "event": "quote.fetch.failed",
                    "stock_id": stock_id,
                    "provider": market,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        if quote is not None:
            set_cached_quote(stock_id, quote)
            logger.info(
                "Quote fetched from provider",
                extra={
                    "event": "quote.fetch.completed",
                    "stock_id": stock_id,
                    "provider": market,
                    "duration_ms": elapsed_ms(started_at),
                    "cache_write": True,
                },
            )
            return quote
        logger.warning(
            "Quote provider returned no data",
            extra={
                "event": "quote.fetch.empty",
                "stock_id": stock_id,
                "provider": market,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        return quote

    def get_quotes(self, stock_ids: list[str]) -> list[tuple[StockRecord, QuoteRecord]]:
        return self._collect_quotes(stock_ids, self.get_quote)

    def get_live_quote(self, stock_id: str) -> QuoteRecord | None:
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        if provider is None:
            logger.warning(
                "Live quote provider is unavailable",
                extra={
                    "event": "quote.live.provider_missing",
                    "stock_id": stock_id,
                    "provider": market,
                },
            )
            return None

        live_quote_fetcher = getattr(provider, "get_live_quote", None)
        started_at = perf_counter()
        try:
            if market == "US" and callable(live_quote_fetcher):
                quote = live_quote_fetcher(stock_id)
            else:
                quote = provider.get_quote(stock_id)
        except Exception as exc:
            logger.exception(
                "Live quote provider raised an unexpected error",
                extra={
                    "event": "quote.live.failed",
                    "stock_id": stock_id,
                    "provider": market,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                },
            )
            raise

        logger.log(
            logging.INFO if quote is not None else logging.WARNING,
            "Live quote fetch completed",
            extra={
                "event": "quote.live.completed" if quote is not None else "quote.live.empty",
                "stock_id": stock_id,
                "provider": market,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        return quote

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
            logger.warning(
                "History provider is unavailable",
                extra={
                    "event": "quote.history.provider_missing",
                    "stock_id": stock_id,
                    "provider": market,
                    "range_name": range_name,
                },
            )
            return None
        stock = provider.get_stock(stock_id)
        if stock is None:
            logger.info(
                "History request stock was not found",
                extra={
                    "event": "quote.history.stock_missing",
                    "stock_id": stock_id,
                    "provider": market,
                    "range_name": range_name,
                },
            )
            return None
        started_at = perf_counter()
        try:
            candles = provider.get_history(stock_id, range_name)
        except Exception as exc:
            logger.exception(
                "History provider raised an unexpected error",
                extra={
                    "event": "quote.history.failed",
                    "stock_id": stock_id,
                    "provider": market,
                    "range_name": range_name,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        logger.log(
            logging.INFO if candles else logging.WARNING,
            "History fetch completed",
            extra={
                "event": "quote.history.completed" if candles else "quote.history.empty",
                "stock_id": stock_id,
                "provider": market,
                "range_name": range_name,
                "duration_ms": elapsed_ms(started_at),
                "candle_count": len(candles),
            },
        )
        return stock, candles

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
