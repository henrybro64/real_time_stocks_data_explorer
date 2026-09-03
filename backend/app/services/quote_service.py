from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
from time import perf_counter
from typing import Literal

from app.core.diagnostics import elapsed_ms
from app.models.quote import CandleRecord, QuoteRecord
from app.models.stock import StockRecord
from app.services.http_client import (
    HttpFetchError,
    clear_last_http_fetch_error,
    get_last_http_fetch_error,
)
from app.services.providers.tw_provider import TWProvider
from app.services.providers.us_provider import USProvider
from app.services.quote_redis_cache import (
    clear_refresh_failure,
    get_cached_quote_entries,
    get_cached_quote_entry,
    record_refresh_failure,
    redis_enabled,
    refresh_failure_active,
    release_refresh_lock,
    renew_refresh_lock,
    set_cached_quote,
    try_acquire_refresh_lock,
)

logger = logging.getLogger(__name__)

RefreshStatus = Literal["idle", "queued", "refreshing", "unavailable"]
CacheSource = Literal["redis", "memory", "provider", "none"]
_UTC_Z_SUFFIX = "+00:00"

_refresh_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="quote-swr")
_quote_batch_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="quote-batch")
_QUOTE_BATCH_MAX_WORKERS = 8


@dataclass(frozen=True)
class QuoteBatchItem:
    stock: StockRecord
    quote: QuoteRecord | None
    cached_at: str | None
    age_seconds: float | None
    stale: bool
    source: CacheSource
    refresh_status: RefreshStatus


@dataclass(frozen=True)
class QuoteFetchAttempt:
    """Result of a provider fetch, including swallowed HttpFetchError metadata."""

    quote: QuoteRecord | None
    error: HttpFetchError | None = None


def _format_cached_at(cached_at) -> str:
    return cached_at.isoformat().replace(_UTC_Z_SUFFIX, "Z")


def _cache_source() -> CacheSource:
    return "redis" if redis_enabled() else "memory"


class QuoteService:
    def __init__(self) -> None:
        self.providers = {
            "US": USProvider(),
            "TW": TWProvider(),
        }
        self._refresh_scheduler = _schedule_quote_refresh

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

    def get_catalog_stock(self, stock_id: str) -> StockRecord | None:
        """Resolve a stock without per-symbol Yahoo chart fallback."""
        market = self._market_from_id(stock_id)
        provider = self.providers.get(market)
        if provider is None:
            return None
        catalog_lookup = getattr(provider, "get_catalog_stock", None)
        if callable(catalog_lookup):
            return catalog_lookup(stock_id)
        return provider.get_stock(stock_id)

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

    def get_quote(self, stock_id: str, *, allow_stale: bool = True) -> QuoteRecord | None:
        """On-demand quote: fresh cache hit, else blocking fetch.

        ``allow_stale=False`` (rankings) skips hard-TTL fallback so callers do not
        treat up-to-15-minute-old data as a current snapshot.

        When the background refresher owns upstream and ``allow_stale`` is True
        (watchlist/portfolio/detail UI), this path never calls providers — it
        serves cache and asks the refresher to warm the symbol.
        """
        cached = get_cached_quote_entry(stock_id)
        if cached is not None and not cached.stale:
            logger.debug(
                "Quote cache hit",
                extra={
                    "event": "quote.cache.hit",
                    "stock_id": stock_id,
                    "stale": False,
                },
            )
            return cached.quote

        if self._background_refresher_owns_upstream() and allow_stale:
            self._request_background_refresh(stock_id)
            if cached is not None:
                logger.debug(
                    "Quote served from stale cache; refresher owns upstream",
                    extra={
                        "event": "quote.cache.stale_serve_refresher",
                        "stock_id": stock_id,
                        "age_seconds": cached.age_seconds,
                    },
                )
                return cached.quote
            return None

        try:
            quote = self._fetch_quote_from_provider(stock_id)
        except Exception:
            if allow_stale and cached is not None:
                logger.info(
                    "Quote refresh failed; serving stale cache",
                    extra={
                        "event": "quote.cache.stale_fallback",
                        "stock_id": stock_id,
                        "age_seconds": cached.age_seconds,
                    },
                )
                return cached.quote
            raise
        if quote is not None:
            set_cached_quote(stock_id, quote)
            return quote
        if allow_stale and cached is not None:
            logger.info(
                "Quote refresh empty; serving stale cache",
                extra={
                    "event": "quote.cache.stale_fallback",
                    "stock_id": stock_id,
                    "age_seconds": cached.age_seconds,
                },
            )
            return cached.quote
        return None

    def get_quote_detail(self, stock_id: str) -> QuoteBatchItem | None:
        """Detail-page quote with accurate freshness metadata."""
        stock = self.get_stock(stock_id)
        if stock is None:
            return None

        cached = get_cached_quote_entry(stock_id)
        if cached is not None and not cached.stale:
            return self._batch_item_from_cache(
                stock, cached, stale=False, refresh_status="idle"
            )

        if self._background_refresher_owns_upstream():
            if cached is not None:
                return self._batch_item_from_cache(
                    stock,
                    cached,
                    stale=True,
                    refresh_status=self._stale_refresh_status(stock_id),
                )
            return self._empty_batch_item(
                stock,
                refresh_status=self._miss_refresh_status(stock_id),
            )

        return self._detail_from_provider(stock, cached)

    def _detail_from_provider(self, stock: StockRecord, cached) -> QuoteBatchItem | None:
        try:
            quote = self._fetch_quote_from_provider(stock.id)
        except Exception:
            if cached is None:
                raise
            return self._batch_item_from_cache(
                stock, cached, stale=True, refresh_status="idle"
            )

        if quote is not None:
            set_cached_quote(stock.id, quote)
            return QuoteBatchItem(
                stock=stock,
                quote=quote,
                cached_at=None,
                age_seconds=0.0,
                stale=False,
                source="provider",
                refresh_status="idle",
            )
        if cached is not None:
            return self._batch_item_from_cache(
                stock, cached, stale=True, refresh_status="idle"
            )
        return None

    @staticmethod
    def _batch_item_from_cache(
        stock: StockRecord,
        cached,
        *,
        stale: bool,
        refresh_status: RefreshStatus,
    ) -> QuoteBatchItem:
        return QuoteBatchItem(
            stock=stock,
            quote=cached.quote,
            cached_at=_format_cached_at(cached.cached_at),
            age_seconds=round(cached.age_seconds, 3),
            stale=stale,
            source=_cache_source(),
            refresh_status=refresh_status,
        )

    @staticmethod
    def _empty_batch_item(
        stock: StockRecord,
        *,
        refresh_status: RefreshStatus,
    ) -> QuoteBatchItem:
        return QuoteBatchItem(
            stock=stock,
            quote=None,
            cached_at=None,
            age_seconds=None,
            stale=True,
            source="none",
            refresh_status=refresh_status,
        )

    def fetch_quote_for_refresh(self, stock_id: str) -> QuoteFetchAttempt:
        """Provider fetch for the background refresher with preserved failure metadata.

        US/TW providers often swallow ``HttpFetchError`` and return ``None``. This
        method recovers the last per-thread HTTP failure so Retry-After / 429 / 403
        classifications still reach the refresher circuit breaker.
        """
        clear_last_http_fetch_error()
        try:
            quote = self._fetch_quote_from_provider(stock_id)
        except HttpFetchError as exc:
            return QuoteFetchAttempt(quote=None, error=exc)
        if quote is not None:
            return QuoteFetchAttempt(quote=quote)
        return QuoteFetchAttempt(quote=None, error=get_last_http_fetch_error())

    def _request_background_refresh(self, stock_id: str) -> None:
        try:
            from app.services.quote_refresher_service import quote_refresher_service

            quote_refresher_service.request_immediate_refresh(stock_id)
        except Exception as exc:  # noqa: BLE001 - never fail reads for enqueue hints
            logger.debug(
                "Could not request background quote refresh",
                extra={
                    "event": "quote.refresh.request_failed",
                    "stock_id": stock_id,
                    "error_type": type(exc).__name__,
                },
            )

    def get_quotes(self, stock_ids: list[str]) -> list[tuple[StockRecord, QuoteRecord]]:
        """Blocking batch for rankings: require fresh/provider data, not hard-stale."""
        return self._collect_quotes(
            stock_ids,
            lambda stock_id: self.get_quote(stock_id, allow_stale=False),
            concurrent=True,
        )

    def get_quotes_cache_first(self, stock_ids: list[str]) -> list[QuoteBatchItem]:
        """Watchlist/stream path: return cache immediately and refresh asynchronously."""
        return self._cached_quote_batch_items(stock_ids, enqueue_refresh=True)

    def peek_cached_quotes(self, stock_ids: list[str]) -> list[QuoteBatchItem]:
        """Read cached quotes without scheduling a refresh.

        Rankings enrichment uses this so a later blocking ``get_quotes`` is the
        only provider fetch for stale or missing ids.
        """
        return self._cached_quote_batch_items(stock_ids, enqueue_refresh=False)

    def _cached_quote_batch_items(
        self,
        stock_ids: list[str],
        *,
        enqueue_refresh: bool,
    ) -> list[QuoteBatchItem]:
        unique_ids = list(dict.fromkeys(stock_ids))
        cached_by_id = get_cached_quote_entries(unique_ids)
        items: list[QuoteBatchItem] = []

        for stock_id in unique_ids:
            stock = self.get_stock(stock_id)
            if stock is None:
                continue
            cached = cached_by_id.get(stock_id)
            if cached is None:
                refresh_status: RefreshStatus = (
                    self._miss_refresh_status(stock_id) if enqueue_refresh else "idle"
                )
                items.append(
                    self._empty_batch_item(stock, refresh_status=refresh_status)
                )
                continue

            refresh_status = "idle"
            if cached.stale and enqueue_refresh:
                refresh_status = self._stale_refresh_status(stock_id)
            items.append(
                self._batch_item_from_cache(
                    stock,
                    cached,
                    stale=cached.stale,
                    refresh_status=refresh_status,
                )
            )
        return items

    def _miss_refresh_status(self, stock_id: str) -> RefreshStatus:
        """Cache miss: queue async refresh, or mark unavailable when no provider exists."""
        market = self._market_from_id(stock_id)
        if self.providers.get(market) is None:
            return "unavailable"
        if refresh_failure_active(stock_id):
            return "unavailable"
        if self._background_refresher_owns_upstream():
            self._request_background_refresh(stock_id)
            return "queued"
        return self._refresh_status_after_enqueue(stock_id)

    def _stale_refresh_status(self, stock_id: str) -> RefreshStatus:
        """Stale hit: enqueue refresh unless a recent failure is still backing off."""
        if refresh_failure_active(stock_id):
            return "idle"
        if self._background_refresher_owns_upstream():
            self._request_background_refresh(stock_id)
            return "queued"
        return self._refresh_status_after_enqueue(stock_id)

    @staticmethod
    def _background_refresher_owns_upstream() -> bool:
        from app.core.config import settings

        if not (
            settings.quote_refresher_enabled and settings.quote_refresher_owns_upstream
        ):
            return False
        try:
            from app.services.quote_refresher_service import quote_refresher_service

            # Only suppress request-path upstream when the sampler is actually alive.
            return quote_refresher_service.is_sampler_running()
        except Exception:
            return False

    def _refresh_status_after_enqueue(self, stock_id: str) -> RefreshStatus:
        """Queue a refresh when possible; report in-flight work when lock is held."""
        if self._enqueue_refresh(stock_id):
            return "queued"
        return "refreshing"

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

    def get_live_quotes(
        self,
        stock_ids: list[str],
        *,
        concurrent: bool = True,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        """Batch live quotes. Concurrent by default for ranking fallback (#182)."""
        return self._collect_quotes(
            stock_ids,
            self.get_live_quote,
            concurrent=concurrent,
        )

    def _collect_quotes(
        self,
        stock_ids: list[str],
        fetcher: Callable[[str], QuoteRecord | None],
        *,
        concurrent: bool = False,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        unique_ids = list(dict.fromkeys(stock_ids))
        if not unique_ids:
            return []

        stocks_by_id: dict[str, StockRecord | None] = {}
        for stock_id in unique_ids:
            try:
                stocks_by_id[stock_id] = self.get_stock(stock_id)
            except Exception:
                logger.exception(
                    "Quote batch stock lookup failed for one symbol",
                    extra={
                        "event": "quote.batch.stock_failed",
                        "stock_id": stock_id,
                    },
                )
                stocks_by_id[stock_id] = None

        def _one(stock_id: str) -> tuple[StockRecord, QuoteRecord] | None:
            stock = stocks_by_id.get(stock_id)
            if stock is None:
                return None
            try:
                quote = fetcher(stock_id)
            except Exception:
                logger.exception(
                    "Quote batch lookup failed for one symbol",
                    extra={
                        "event": "quote.batch.symbol_failed",
                        "stock_id": stock_id,
                    },
                )
                return None
            if quote is None:
                return None
            return (stock, quote)

        worker_count = min(_QUOTE_BATCH_MAX_WORKERS, len(unique_ids))
        if not concurrent or worker_count <= 1:
            results = [_one(stock_id) for stock_id in unique_ids]
        else:
            futures = [
                _quote_batch_executor.submit(_one, stock_id)
                for stock_id in unique_ids
            ]
            results = [future.result() for future in futures]
        return [envelope for envelope in results if envelope is not None]

    def _enqueue_refresh(self, stock_id: str) -> bool:
        token = try_acquire_refresh_lock(stock_id)
        if token is None:
            return False
        self._refresh_scheduler(self, stock_id, token)
        return True

    def _refresh_quote_in_background(self, stock_id: str, token: str) -> None:
        # Renew/reclaim when the worker actually starts so queued tasks cannot
        # outlive the lease and allow duplicate provider fetches.
        if not renew_refresh_lock(stock_id, token):
            return
        try:
            quote = self._fetch_quote_from_provider(stock_id)
            if quote is not None:
                set_cached_quote(stock_id, quote)
                clear_refresh_failure(stock_id)
                logger.info(
                    "Background quote refresh completed",
                    extra={
                        "event": "quote.refresh.completed",
                        "stock_id": stock_id,
                    },
                )
            else:
                record_refresh_failure(stock_id)
                logger.warning(
                    "Background quote refresh returned no data",
                    extra={
                        "event": "quote.refresh.empty",
                        "stock_id": stock_id,
                    },
                )
        except Exception as exc:
            record_refresh_failure(stock_id)
            logger.warning(
                "Background quote refresh failed",
                extra={
                    "event": "quote.refresh.failed",
                    "stock_id": stock_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
        finally:
            release_refresh_lock(stock_id, token)

    def _fetch_quote_from_provider(self, stock_id: str) -> QuoteRecord | None:
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

    def get_history(
        self,
        stock_id: str,
        range_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[StockRecord, list[CandleRecord]] | None:
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
            candles = provider.get_history(stock_id, range_name, limit=limit)
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


def _schedule_quote_refresh(service: QuoteService, stock_id: str, token: str) -> None:
    _refresh_executor.submit(service._refresh_quote_in_background, stock_id, token)


quote_service = QuoteService()
