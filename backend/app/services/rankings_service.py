from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
import math
from time import perf_counter
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from app.core.diagnostics import elapsed_ms
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
    RankingUniverse,
    TwBoard,
    UsUniverse,
)
from app.schemas.stock import StockResponse
from app.services.http_client import HttpFetchError, fetch_json
from app.services.key_stats_service import get_valuation_snapshots
from app.services.market_clock import (
    TW_DAILY_PUBLISH_TIME,
    TW_INSTITUTIONAL_DAILY_LOOKBACK_DAYS,
    is_market_open,
    tw_institutional_daily_bucket_for_date,
)
from app.services.quote_service import QuoteService, quote_service
from app.services.rankings_external_source import (
    RankingsExternalSource,
    rankings_external_source,
)
from app.services.rankings_volume_baseline_cache import (
    VolumeBaselineStore,
    default_volume_baseline_store,
)
from app.services.providers.us_provider import yahoo_v7_quotes_batch
from app.services.index_constituents import (
    load_djia_symbols,
    load_nasdaq100_symbols,
    load_sp500_symbols,
)
from app.services.taiwan_history_scan import iter_candidate_weekdays

logger = logging.getLogger(__name__)

RankingMarket = Literal["US", "TW"]

TW_TWSE_RANKING_STOCK_IDS = [
    "TW:0050",
    "TW:2330",
    "TW:2317",
    "TW:2454",
    "TW:2303",
    "TW:2881",
    "TW:2882",
]

TW_TPEX_RANKING_STOCK_IDS = [
    "TW:6488",
    "TW:8299",
    "TW:8271",
    "TW:6215",
    "TW:6643",
    "TW:6562",
    "TW:6451",
    "TW:6462",
]


def _tw_quote_universe_ids(board: TwBoard) -> list[str]:
    return TW_TPEX_RANKING_STOCK_IDS if board == "TPEx" else TW_TWSE_RANKING_STOCK_IDS


_UNSUPPORTED_REASONS: dict[RankingType, str] = {
    RankingType.net_buy: "US institutional net-buy data is not available from free-only sources in this app.",
    RankingType.net_sell: "US institutional net-sell data is not available from free-only sources in this app.",
}

_QUOTE_SOURCE = "Quote rankings from the app's free best-effort quote providers."
_DJIA_QUOTE_SOURCE = (
    "DJIA constituent quote rankings from the app's free best-effort quote providers."
)
_NASDAQ100_QUOTE_SOURCE = (
    "Nasdaq-100 constituent quote rankings from the app's free best-effort quote providers."
)
_QUOTE_WARNING = "Free unofficial quote source; data may be delayed or unavailable."
_TW_QUOTE_FALLBACK_SOURCE = (
    "Limited hard-coded TWSE/TPEx quote fallback; not a full-board ranking."
)
_TW_QUOTE_FALLBACK_WARNING = (
    "Broader Taiwan quote rankings are temporarily unavailable. "
    "This table uses a small hard-coded fallback universe, not a complete TWSE/TPEx ranking."
)
_TW_QUOTE_FALLBACK_FRESHNESS_OPEN = (
    "Market open; showing a limited hard-coded quote fallback because the broader "
    "Taiwan ranking source is unavailable."
)
_TW_QUOTE_FALLBACK_FRESHNESS_CLOSED = (
    "Market closed; showing a limited hard-coded quote fallback because the broader "
    "Taiwan ranking source is unavailable."
)


def _tw_hardcoded_fallback_copy(market_open: bool) -> tuple[str, str, str]:
    freshness = (
        _TW_QUOTE_FALLBACK_FRESHNESS_OPEN
        if market_open
        else _TW_QUOTE_FALLBACK_FRESHNESS_CLOSED
    )
    return _TW_QUOTE_FALLBACK_SOURCE, _TW_QUOTE_FALLBACK_WARNING, freshness


def _quote_path_freshness(
    ranking_type: RankingType,
    market_open: bool,
    baseline_missing: bool,
) -> str:
    if ranking_type == RankingType.volume:
        if market_open:
            return "Market open; rankings use the latest quote snapshot."
        return "Market closed; rankings use the latest available quote snapshot."
    if ranking_type in {RankingType.change_up, RankingType.change_down}:
        if market_open:
            return "Market open; price-move rankings use the latest quote snapshot."
        return "Market closed; price-move rankings use the latest available quote snapshot."
    if baseline_missing:
        return (
            "Baseline unavailable on the first snapshot; volume change percent stays empty until a later refresh."
        )
    if market_open:
        return "Volume change uses the previous quote snapshot baseline."
    return "Market closed; volume change uses the previous quote snapshot baseline."


def _us_universe_quote_source(us_universe: UsUniverse | None) -> str:
    if us_universe == "DJIA":
        return _DJIA_QUOTE_SOURCE
    if us_universe == "NASDAQ100":
        return _NASDAQ100_QUOTE_SOURCE
    return _QUOTE_SOURCE


def _us_universe_scope(us_universe: UsUniverse | None) -> str | None:
    if us_universe == "DJIA":
        return "djia"
    if us_universe == "NASDAQ100":
        return "nasdaq100"
    return None


def _synthetic_us_stock(symbol: str) -> StockRecord:
    return StockRecord(
        id=f"US:{symbol}",
        market="US",
        symbol=symbol,
        name=symbol,
        name_en=symbol,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def _effective_ranking_limit(limit: int, us_universe: UsUniverse | None) -> int:
    if us_universe == "DJIA":
        return min(limit, 30)
    return limit


def _response_universe(
    market: RankingMarket,
    tw_board: TwBoard | None,
    us_universe: UsUniverse | None,
) -> RankingUniverse | None:
    if market == "TW":
        return tw_board
    return us_universe


def _unofficial_ranking_response(
    market: RankingMarket,
    ranking_type: RankingType,
    tw_board: TwBoard | None,
    items: list,
    now: datetime,
    market_open: bool,
    source: str,
    freshness: str,
    warning: str | None,
    us_rankings_scope: str | None = None,
    tw_rankings_scope: str | None = None,
    source_type: RankingSourceType = RankingSourceType.free_unofficial,
    us_universe: UsUniverse | None = None,
) -> RankingResponse:
    payload = {
        "market": market,
        "rankingType": ranking_type,
        "twBoard": tw_board if market == "TW" else None,
        "universe": _response_universe(market, tw_board, us_universe),
        "items": items,
        "timestamp": now.isoformat(),
        "source": source,
        "sourceType": source_type,
        "isMarketOpen": market_open,
        "dataFreshnessLabel": freshness,
        "warningMessage": warning,
        "unsupportedReason": None,
        "usRankingsScope": us_rankings_scope if market == "US" else None,
        "twRankingsScope": tw_rankings_scope if market == "TW" else None,
    }
    return RankingResponse.model_validate(payload)


_VALUATION_SOURCE = (
    "Valuation rankings use the highest-volume current quote candidates, then sort by "
    "P/E from the app's available valuation sources."
)
_PRE_RANKED_SOURCE = "Yahoo Finance pre-ranked quote rankings."
_SP500_CHANGE_SOURCE = (
    "S&P 500 movers: constituent snapshot plus batched Yahoo Finance v7 quote fields "
    "(free best-effort; not the same universe as Yahoo's predefined gainers/losers screeners)."
)
_VALUATION_FRESHNESS_OPEN = (
    "Market open; valuation rankings use the latest quote snapshot filtered to the highest-volume "
    "candidates, then sort by the latest available P/E."
)
_VALUATION_FRESHNESS_CLOSED = (
    "Market closed; valuation rankings use the latest available quote snapshot filtered to the "
    "highest-volume candidates, then sort by the latest available P/E."
)
_PRE_RANKED_TYPES = {RankingType.volume, RankingType.change_up, RankingType.change_down}
_TW_DAILY_WARNING = "台股三大法人買賣超為日資料，非盤中即時資料。"
_TWSE_INSTITUTIONAL_SOURCE = "TWSE T86 daily institutional data"
_TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"
_TWSE_T86_SELECT_TYPE = "ALLBUT0999"
_TW_INSTITUTIONAL_UNAVAILABLE_FRESHNESS = (
    "TW daily institutional ranking is unavailable from the free public source right now."
)
_TW_INSTITUTIONAL_UNAVAILABLE_REASON = (
    "TW institutional daily data is temporarily unavailable from the free public source."
)
_TPEX_DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
_TPEX_DAILY_TRADE_SECT = "EW"
_TPEX_INSTITUTIONAL_SOURCE = "TPEx institutional daily data"
_TPEX_DAILY_WARNING = "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
_TPEX_INSTITUTIONAL_UNAVAILABLE_FRESHNESS = (
    "TPEx institutional daily ranking is unavailable from the free public source right now."
)
_TPEX_INSTITUTIONAL_UNAVAILABLE_REASON = (
    "TPEx institutional daily data is temporarily unavailable from the free public source."
)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TW_TIMEZONE = ZoneInfo("Asia/Taipei")
# Rankings must stay interactive: walking many prior dates with long per-request
# timeouts can stall the page for well over a minute on a bad upstream day.
_TW_INSTITUTIONAL_MAX_WEEKDAY_PROBES = 5
_TW_INSTITUTIONAL_FETCH_BUDGET_SECONDS = 6.0
_TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS = 2.5
_TW_INSTITUTIONAL_REQUEST_RETRIES = 0
_TW_INSTITUTIONAL_MIN_PROBE_SECONDS = 0.4
# Bound wait time even when httpx per-operation timeouts allow a trickling body.
_TW_INSTITUTIONAL_PROBE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="tw-inst-rankings",
)

_T = TypeVar("_T")


@dataclass
class _RankingCandidate:
    stock: StockResponse
    last_price: float | None
    change_percent: float | None
    volume: int | None
    volume_delta_percent: float | None
    net_buy_sell_shares: int | None
    pe_ratio: float | None
    pe_ratio_type: str | None
    pe_ratio_source: str | None
    valuation_as_of: str | None
    valuation_warning: str | None
    timestamp: str


class RankingsService:
    def __init__(
        self,
        quote_source: QuoteService | None = None,
        volume_baseline_store: VolumeBaselineStore | None = None,
        external_source: RankingsExternalSource | None = None,
        *,
        sp500_quote_batch: Callable[[list[StockRecord]], dict[str, QuoteRecord]] | None = None,
        sp500_symbol_loader: Callable[[], tuple[str, ...]] | None = None,
        tw_stock_ids_resolver: Callable[[TwBoard], list[str]] | None = None,
    ) -> None:
        self.quote_source = quote_source or quote_service
        self._volume_baselines = volume_baseline_store or default_volume_baseline_store
        self._external_source = external_source or rankings_external_source
        self._sp500_quote_batch = sp500_quote_batch or yahoo_v7_quotes_batch
        self._sp500_symbol_loader = sp500_symbol_loader or load_sp500_symbols
        self._tw_stock_ids_resolver = tw_stock_ids_resolver or _tw_quote_universe_ids
        # Scheduler injects a bounded settings universe; only the default hard-coded
        # TWSE/TPEx lists should be advertised as a limited fallback.
        self._uses_hardcoded_tw_quote_universe = (
            tw_stock_ids_resolver is None or tw_stock_ids_resolver is _tw_quote_universe_ids
        )

    def get_rankings(
        self,
        market: RankingMarket,
        ranking_type: RankingType | str,
        limit: int,
        now: datetime | None = None,
        tw_board: TwBoard | None = None,
        us_universe: UsUniverse | None = None,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse:
        started_at = perf_counter()
        current = now or datetime.now(timezone.utc)
        normalized_type = RankingType(ranking_type)
        effective_tw_board: TwBoard | None = tw_board if market == "TW" else None
        if market == "TW" and effective_tw_board is None:
            effective_tw_board = "TWSE"
        effective_us_universe: UsUniverse | None = us_universe if market == "US" else None
        effective_limit = _effective_ranking_limit(limit, effective_us_universe)
        if normalized_type in {RankingType.net_buy, RankingType.net_sell}:
            if market == "US":
                response = self._unsupported_response(
                    market, normalized_type, current, effective_us_universe
                )
            else:
                assert effective_tw_board is not None
                response = self._build_tw_institutional_ranking(
                    normalized_type, effective_limit, current, effective_tw_board
                )
        elif normalized_type == RankingType.valuation_low_pe:
            response = self._build_valuation_ranking(
                market,
                normalized_type,
                effective_limit,
                current,
                effective_tw_board,
                effective_us_universe,
                allow_hardcoded_fallback,
            )
        else:
            response = self._build_quote_ranking(
                market,
                normalized_type,
                effective_limit,
                current,
                effective_tw_board,
                effective_us_universe,
                allow_hardcoded_fallback,
            )
        logger.info(
            "Rankings request completed",
            extra={
                "event": "rankings.completed",
                "market": market,
                "ranking_type": normalized_type.value,
                "limit": effective_limit,
                "tw_board": effective_tw_board,
                "universe": effective_us_universe or effective_tw_board,
                "duration_ms": elapsed_ms(started_at),
                "item_count": len(response.items),
                "source_type": response.sourceType.value,
                "is_market_open": response.isMarketOpen,
            },
        )
        return response

    def _build_quote_ranking(
        self,
        market: RankingMarket,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
        tw_board: TwBoard | None,
        us_universe: UsUniverse | None = None,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse:
        market_open = is_market_open(market, now)
        scoped_us_universe = us_universe is not None

        if (
            market == "US"
            and not scoped_us_universe
            and ranking_type in {RankingType.change_up, RankingType.change_down}
        ):
            sp500_ranking = self._try_build_us_sp500_price_movers(
                ranking_type, limit, now, market_open
            )
            if sp500_ranking is not None and sp500_ranking.items:
                return sp500_ranking

        if ranking_type in _PRE_RANKED_TYPES and not scoped_us_universe:
            external_envelopes = self._external_source.fetch(
                market=market,
                ranking_type=ranking_type,
                limit=limit,
                now=now,
                tw_board=tw_board if market == "TW" else None,
            )
            if external_envelopes:
                return self._build_pre_ranked_response(
                    market=market,
                    ranking_type=ranking_type,
                    envelopes=external_envelopes,
                    market_open=market_open,
                    now=now,
                    source_type=RankingSourceType.free_unofficial,
                    us_rankings_scope="yahoo" if market == "US" else None,
                    tw_board=tw_board if market == "TW" else None,
                    us_universe=us_universe,
                )

        candidates: list[_RankingCandidate] = []
        baseline_missing = False

        for stock, quote in self._get_quote_rankings_snapshot(market, tw_board, us_universe):
            volume_delta_percent: float | None = None
            if ranking_type in {RankingType.volume_up, RankingType.volume_down}:
                baseline_volume = self._volume_baselines.get(stock.id)
                if (
                    quote.volume is not None
                    and baseline_volume is not None
                    and baseline_volume > 0
                ):
                    volume_delta_percent = round(
                        ((quote.volume - baseline_volume) / baseline_volume) * 100,
                        2,
                    )
                else:
                    baseline_missing = True

            if quote.volume is not None:
                self._volume_baselines.set(stock.id, quote.volume)

            candidates.append(
                _RankingCandidate(
                    stock=_stock_schema(stock),
                    last_price=quote.last_price,
                    change_percent=quote.change_percent,
                    volume=quote.volume,
                    volume_delta_percent=volume_delta_percent,
                    net_buy_sell_shares=None,
                    pe_ratio=None,
                    pe_ratio_type=None,
                    pe_ratio_source=None,
                    valuation_as_of=None,
                    valuation_warning=None,
                    timestamp=quote.timestamp,
                )
            )

        candidates = self._filter_quote_candidates_for_ranking_type(candidates, ranking_type)
        ranked_candidates = self._sort_quote_candidates(candidates, ranking_type)[:limit]
        items = [
            _ranking_item(index, candidate)
            for index, candidate in enumerate(ranked_candidates, start=1)
        ]

        used_hardcoded_fallback = (
            market == "TW"
            and ranking_type in _PRE_RANKED_TYPES
            and self._uses_hardcoded_tw_quote_universe
        )
        if used_hardcoded_fallback and not allow_hardcoded_fallback:
            source = _QUOTE_SOURCE
            warning = _QUOTE_WARNING
            freshness = _quote_path_freshness(ranking_type, market_open, baseline_missing)
            tw_scope = None
            items = []
        elif used_hardcoded_fallback:
            source, warning, freshness = _tw_hardcoded_fallback_copy(market_open)
            tw_scope = "fallback"
        else:
            source = _us_universe_quote_source(us_universe) if market == "US" else _QUOTE_SOURCE
            warning = _QUOTE_WARNING
            freshness = _quote_path_freshness(ranking_type, market_open, baseline_missing)
            tw_scope = "quote" if market == "TW" else None

        return _unofficial_ranking_response(
            market, ranking_type, tw_board, items, now, market_open,
            source, freshness, warning,
            _us_universe_scope(us_universe) or ("quote" if market == "US" else None),
            tw_scope,
            us_universe=us_universe,
        )

    def _build_valuation_ranking(
        self,
        market: RankingMarket,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
        tw_board: TwBoard | None,
        us_universe: UsUniverse | None = None,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse:
        market_open = is_market_open(market, now)
        envelopes, us_rankings_scope, tw_rankings_scope = self._get_valuation_universe_snapshot(
            market,
            now,
            tw_board,
            universe_limit=max(100, limit),
            us_universe=us_universe,
        )
        if tw_rankings_scope == "fallback" and not allow_hardcoded_fallback:
            return _unofficial_ranking_response(
                market, ranking_type, tw_board, [], now, market_open,
                _QUOTE_SOURCE,
                _VALUATION_FRESHNESS_OPEN if market_open else _VALUATION_FRESHNESS_CLOSED,
                _QUOTE_WARNING,
                us_rankings_scope,
                None,
                us_universe=us_universe,
            )
        candidates: list[_RankingCandidate] = []
        valuations = get_valuation_snapshots(envelopes)

        for stock, quote in envelopes:
            valuation = valuations[stock.id]
            pe_ratio = _positive_float(valuation.get("peRatio"))
            if pe_ratio is None:
                continue
            candidates.append(
                _RankingCandidate(
                    stock=_stock_schema(stock),
                    last_price=quote.last_price,
                    change_percent=quote.change_percent,
                    volume=quote.volume,
                    volume_delta_percent=None,
                    net_buy_sell_shares=None,
                    pe_ratio=pe_ratio,
                    pe_ratio_type=_string_or_none(valuation.get("peRatioType")),
                    pe_ratio_source=_string_or_none(valuation.get("peRatioSource")),
                    valuation_as_of=_string_or_none(valuation.get("valuationAsOf")),
                    valuation_warning=_string_or_none(valuation.get("valuationWarning")),
                    timestamp=quote.timestamp,
                )
            )

        ranked_candidates = self._sort_valuation_candidates(candidates)[:limit]
        items = [
            _ranking_item(index, candidate)
            for index, candidate in enumerate(ranked_candidates, start=1)
        ]
        if tw_rankings_scope == "fallback":
            source, warning, freshness = _tw_hardcoded_fallback_copy(market_open)
        else:
            freshness = _VALUATION_FRESHNESS_OPEN if market_open else _VALUATION_FRESHNESS_CLOSED
            source = (
                _us_universe_quote_source(us_universe)
                if us_universe is not None
                else _VALUATION_SOURCE
            )
            warning = _QUOTE_WARNING

        return _unofficial_ranking_response(
            market, ranking_type, tw_board, items, now, market_open,
            source, freshness, warning,
            us_rankings_scope or _us_universe_scope(us_universe),
            tw_rankings_scope,
            us_universe=us_universe,
        )

    def _get_valuation_universe_snapshot(
        self,
        market: RankingMarket,
        now: datetime,
        tw_board: TwBoard | None,
        *,
        universe_limit: int,
        us_universe: UsUniverse | None = None,
    ) -> tuple[list[tuple[StockRecord, QuoteRecord]], str | None, str | None]:
        if us_universe is None:
            external_envelopes = self._external_source.fetch(
                market=market,
                ranking_type=RankingType.volume,
                limit=universe_limit,
                now=now,
                tw_board=tw_board if market == "TW" else None,
            )
            if external_envelopes:
                return (
                    external_envelopes,
                    "yahoo" if market == "US" else None,
                    "yahoo" if market == "TW" else None,
                )

        fallback = self._sort_envelopes_by_volume(
            self._get_quote_rankings_snapshot(market, tw_board, us_universe)
        )[:universe_limit]
        tw_scope = None
        if market == "TW":
            tw_scope = (
                "fallback" if self._uses_hardcoded_tw_quote_universe else "quote"
            )
        return (
            fallback,
            _us_universe_scope(us_universe) or ("quote" if market == "US" else None),
            tw_scope,
        )

    @staticmethod
    def _sort_envelopes_by_volume(
        envelopes: list[tuple[StockRecord, QuoteRecord]],
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        return sorted(
            envelopes,
            key=lambda pair: (
                pair[1].volume is None,
                -(pair[1].volume or -1),
                pair[0].symbol,
            ),
        )

    def _get_quote_rankings_snapshot(
        self,
        market: RankingMarket,
        tw_board: TwBoard | None,
        us_universe: UsUniverse | None = None,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        if market == "US":
            return self._us_quote_snapshot_envelopes(us_universe)
        assert tw_board is not None
        stock_ids = self._tw_stock_ids_resolver(tw_board)
        return self.quote_source.get_quotes(stock_ids)

    def _us_symbols_for_universe(self, us_universe: UsUniverse | None) -> tuple[str, ...]:
        if us_universe == "DJIA":
            return load_djia_symbols()
        if us_universe == "NASDAQ100":
            return load_nasdaq100_symbols()
        return self._sp500_symbol_loader()

    def _catalog_us_stock(self, stock_id: str) -> StockRecord | None:
        lookup = getattr(self.quote_source, "get_catalog_stock", None)
        if callable(lookup):
            return lookup(stock_id)
        return None

    def _us_stock_records_for_symbols(self, symbols: tuple[str, ...]) -> list[StockRecord]:
        stocks: list[StockRecord] = []
        for symbol in symbols:
            stock_id = f"US:{symbol}"
            known = self._catalog_us_stock(stock_id)
            stocks.append(known if known is not None else _synthetic_us_stock(symbol))
        return stocks

    def _us_quote_snapshot_envelopes(
        self,
        us_universe: UsUniverse | None = None,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        """US quote-path rankings: selected index constituents; omit symbols with no quote."""
        symbols = self._us_symbols_for_universe(us_universe)
        if not symbols:
            return []
        stocks = self._us_stock_records_for_symbols(symbols)
        quotes_by_id = self._sp500_quote_batch(stocks)
        if quotes_by_id:
            return [(s, quotes_by_id[s.id]) for s in stocks if s.id in quotes_by_id]
        # v7 batch empty: per-symbol live path (bypasses quote Redis cache).
        ids = [s.id for s in stocks]
        return self.quote_source.get_live_quotes(ids)

    def _try_build_us_sp500_price_movers(
        self,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
        market_open: bool,
    ) -> RankingResponse | None:
        symbols = self._sp500_symbol_loader()
        if not symbols:
            return None
        stocks = self._us_stock_records_for_symbols(symbols)
        quotes_by_id = self._sp500_quote_batch(stocks)
        if not quotes_by_id:
            return None

        envelopes: list[tuple[StockRecord, QuoteRecord]] = [
            (s, quotes_by_id[s.id]) for s in stocks if s.id in quotes_by_id
        ]
        filtered: list[tuple[StockRecord, QuoteRecord]] = []
        for st, q in envelopes:
            if q.change_percent is None:
                continue
            if ranking_type == RankingType.change_up and q.change_percent > 0:
                filtered.append((st, q))
            elif ranking_type == RankingType.change_down and q.change_percent < 0:
                filtered.append((st, q))

        if ranking_type == RankingType.change_up:
            filtered.sort(
                key=lambda t: (
                    -(t[1].change_percent or -10**9),
                    -(t[1].volume or -1),
                    t[0].symbol,
                ),
            )
        else:
            filtered.sort(
                key=lambda t: (
                    t[1].change_percent if t[1].change_percent is not None else 10**9,
                    -(t[1].volume or -1),
                    t[0].symbol,
                ),
            )

        envelopes_ranked = filtered[:limit]
        if not envelopes_ranked:
            return None

        return self._build_pre_ranked_response(
            market="US",
            ranking_type=ranking_type,
            envelopes=envelopes_ranked,
            market_open=market_open,
            now=now,
            source_type=RankingSourceType.free_unofficial,
            rankings_source_text=_SP500_CHANGE_SOURCE,
            us_rankings_scope="sp500",
            change_freshness_open=(
                "Market open; S&P 500 movers ranked by batched Yahoo quote change % (regular session fields)."
            ),
            change_freshness_closed=(
                "Market closed; S&P 500 movers ranked by latest batched Yahoo quote change % (regular session fields)."
            ),
        )

    def _build_pre_ranked_response(
        self,
        *,
        market: RankingMarket,
        ranking_type: RankingType,
        envelopes: list[tuple[StockRecord, QuoteRecord]],
        market_open: bool,
        now: datetime,
        source_type: RankingSourceType,
        rankings_source_text: str | None = None,
        us_rankings_scope: str | None = None,
        change_freshness_open: str | None = None,
        change_freshness_closed: str | None = None,
        tw_board: TwBoard | None = None,
        us_universe: UsUniverse | None = None,
    ) -> RankingResponse:
        """Render rankings that arrive already ordered (Yahoo screeners or S&P 500 movers)."""
        # We still want to keep the volume baseline cache warm even when we
        # do not surface volume_up/volume_down for this market, so future
        # requests for those ranking types have a baseline to compare to.
        for stock, quote in envelopes:
            if quote.volume is not None:
                self._volume_baselines.set(stock.id, quote.volume)

        candidates = [
            _RankingCandidate(
                stock=_stock_schema(stock),
                last_price=quote.last_price,
                change_percent=quote.change_percent,
                volume=quote.volume,
                volume_delta_percent=None,
                net_buy_sell_shares=None,
                pe_ratio=None,
                pe_ratio_type=None,
                pe_ratio_source=None,
                valuation_as_of=None,
                valuation_warning=None,
                timestamp=quote.timestamp,
            )
            for stock, quote in envelopes
        ]
        items = [
            _ranking_item(index, candidate)
            for index, candidate in enumerate(candidates, start=1)
        ]

        if ranking_type == RankingType.volume:
            freshness = (
                "Market open; volume rankings come from Yahoo Finance and refresh every few minutes."
                if market_open
                else "Market closed; volume rankings come from Yahoo Finance's latest snapshot."
            )
        else:
            if change_freshness_open is not None and change_freshness_closed is not None:
                freshness = change_freshness_open if market_open else change_freshness_closed
            else:
                freshness = (
                    "Market open; price-move rankings come from Yahoo Finance and refresh every few minutes."
                    if market_open
                    else "Market closed; price-move rankings come from Yahoo Finance's latest snapshot."
                )

        return _unofficial_ranking_response(
            market, ranking_type, tw_board, items, now, market_open,
            rankings_source_text or _PRE_RANKED_SOURCE,
            freshness,
            _QUOTE_WARNING,
            us_rankings_scope,
            "yahoo" if market == "TW" else None,
            source_type,
            us_universe=us_universe,
        )

    def _build_tw_institutional_ranking(
        self,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
        tw_board: TwBoard,
    ) -> RankingResponse:
        market_open = is_market_open("TW", now)
        institutional_snapshot = self._fetch_tw_institutional_snapshot(now, tw_board)
        if institutional_snapshot is None:
            return RankingResponse(
                market="TW",
                rankingType=ranking_type,
                twBoard=tw_board,
                universe=tw_board,
                items=[],
                timestamp=now.isoformat(),
                source=self._tw_institutional_source(tw_board),
                sourceType=RankingSourceType.unsupported,
                isMarketOpen=market_open,
                dataFreshnessLabel=self._tw_institutional_unavailable_freshness(tw_board),
                warningMessage=self._tw_institutional_warning(tw_board),
                unsupportedReason=self._tw_institutional_unavailable_reason(tw_board),
            )

        ranking_date, institutional_rows = institutional_snapshot
        candidates: list[_RankingCandidate] = []
        ranking_timestamp = tw_institutional_daily_bucket_for_date(ranking_date).astimezone(
            _TW_TIMEZONE
        ).isoformat()
        for stock_id, net_buy_sell_shares in institutional_rows.items():
            stock = self.quote_source.get_stock(stock_id)
            if stock is None or stock.exchange != tw_board:
                continue
            candidates.append(
                _RankingCandidate(
                    stock=_stock_schema(stock),
                    last_price=None,
                    change_percent=None,
                    volume=None,
                    volume_delta_percent=None,
                    net_buy_sell_shares=net_buy_sell_shares,
                    pe_ratio=None,
                    pe_ratio_type=None,
                    pe_ratio_source=None,
                    valuation_as_of=None,
                    valuation_warning=None,
                    timestamp=ranking_timestamp,
                )
            )

        ranked_candidates = self._sort_tw_institutional_candidates(candidates, ranking_type)[:limit]
        quotes_by_id = self._fetch_tw_institutional_enrichment_quotes(
            [candidate.stock.id for candidate in ranked_candidates]
        )
        for candidate in ranked_candidates:
            quote = quotes_by_id.get(candidate.stock.id)
            if quote is None:
                continue
            candidate.last_price = quote.last_price
            candidate.change_percent = quote.change_percent
            candidate.volume = quote.volume

        items = [
            _ranking_item(index, candidate)
            for index, candidate in enumerate(ranked_candidates, start=1)
        ]

        freshness = self._tw_institutional_freshness_label(
            tw_board,
            ranking_type,
            market_open=market_open,
        )

        return RankingResponse(
            market="TW",
            rankingType=ranking_type,
            twBoard=tw_board,
            universe=tw_board,
            items=items,
            timestamp=ranking_timestamp,
            source=self._tw_institutional_source(tw_board),
            sourceType=RankingSourceType.free_public,
            isMarketOpen=market_open,
            dataFreshnessLabel=freshness,
            warningMessage=self._tw_institutional_warning(tw_board),
            unsupportedReason=None,
        )

    def _fetch_tw_institutional_enrichment_quotes(
        self,
        stock_ids: list[str],
    ) -> dict[str, QuoteRecord]:
        """Attach display quotes after ranking, without a serial provider loop.

        Prefer a cache-only bulk read when the quote source supports it so
        stale/missing ids are not also enqueued for SWR refresh. Only fresh
        (non-stale) cache hits skip ``get_quotes``; stale and missing ids are
        filled with one bounded-concurrent lookup batch. Ranking order is
        applied by the caller, not by quote completion order.
        """
        if not stock_ids:
            return {}
        quotes_by_id: dict[str, QuoteRecord] = {}
        cache_reader = getattr(self.quote_source, "peek_cached_quotes", None)
        if not callable(cache_reader):
            cache_reader = getattr(self.quote_source, "get_quotes_cache_first", None)
        if callable(cache_reader):
            items = cache_reader(stock_ids)
            for item in items:
                quote = _fresh_cached_enrichment_quote(item)
                if quote is None:
                    continue
                quotes_by_id[item.stock.id] = quote
        missing = [stock_id for stock_id in stock_ids if stock_id not in quotes_by_id]
        if not missing:
            return quotes_by_id
        quotes_by_id.update(
            {
                stock.id: quote
                for stock, quote in self.quote_source.get_quotes(missing)
            }
        )
        return quotes_by_id

    @staticmethod
    def _tw_institutional_source(tw_board: TwBoard) -> str:
        return _TPEX_INSTITUTIONAL_SOURCE if tw_board == "TPEx" else _TWSE_INSTITUTIONAL_SOURCE

    @staticmethod
    def _tw_institutional_warning(tw_board: TwBoard) -> str:
        return _TPEX_DAILY_WARNING if tw_board == "TPEx" else _TW_DAILY_WARNING

    @staticmethod
    def _tw_institutional_unavailable_freshness(tw_board: TwBoard) -> str:
        return (
            _TPEX_INSTITUTIONAL_UNAVAILABLE_FRESHNESS
            if tw_board == "TPEx"
            else _TW_INSTITUTIONAL_UNAVAILABLE_FRESHNESS
        )

    @staticmethod
    def _tw_institutional_unavailable_reason(tw_board: TwBoard) -> str:
        return (
            _TPEX_INSTITUTIONAL_UNAVAILABLE_REASON
            if tw_board == "TPEx"
            else _TW_INSTITUTIONAL_UNAVAILABLE_REASON
        )

    @staticmethod
    def _tw_institutional_freshness_label(
        tw_board: TwBoard,
        ranking_type: RankingType,
        *,
        market_open: bool,
    ) -> str:
        market_label = "TPEx daily" if tw_board == "TPEx" else "TW daily"
        freshness = (
            f"{market_label} institutional ranking sorted by highest 三大法人買賣超股數."
            if ranking_type == RankingType.net_buy
            else f"{market_label} institutional ranking sorted by most negative 三大法人買賣超股數."
        )
        return freshness if market_open else f"Market closed; {freshness}"

    def _unsupported_response(
        self,
        market: RankingMarket,
        ranking_type: RankingType,
        now: datetime,
        us_universe: UsUniverse | None = None,
    ) -> RankingResponse:
        return RankingResponse(
            market=market,
            rankingType=ranking_type,
            universe=us_universe if market == "US" else None,
            items=[],
            timestamp=now.isoformat(),
            source="Unsupported in Phase 1 free-only rankings",
            sourceType=RankingSourceType.unsupported,
            isMarketOpen=is_market_open(market, now),
            dataFreshnessLabel="Phase 1 free-only limitation.",
            warningMessage=None,
            unsupportedReason=_UNSUPPORTED_REASONS[ranking_type],
        )

    def _fetch_tw_institutional_snapshot(
        self,
        now: datetime,
        tw_board: TwBoard = "TWSE",
    ) -> tuple[date, dict[str, int]] | None:
        if tw_board == "TPEx":
            return self._fetch_tpex_institutional_snapshot(now)
        return self._fetch_twse_institutional_snapshot(now)

    def _fetch_twse_institutional_snapshot(
        self,
        now: datetime,
    ) -> tuple[date, dict[str, int]] | None:
        return self._probe_tw_institutional_dates(
            now,
            board="TWSE",
            probe=self._probe_twse_institutional_date,
        )

    def _fetch_tpex_institutional_snapshot(
        self,
        now: datetime,
    ) -> tuple[date, dict[str, int]] | None:
        return self._probe_tw_institutional_dates(
            now,
            board="TPEx",
            probe=self._probe_tpex_institutional_date,
        )

    def _probe_twse_institutional_date(
        self,
        current_date: date,
        timeout_seconds: float,
    ) -> tuple[date, dict[str, int]] | None:
        rows = self._fetch_twse_institutional_rows(
            current_date,
            timeout_seconds=timeout_seconds,
        )
        if not rows:
            return None
        return current_date, rows

    def _probe_tpex_institutional_date(
        self,
        current_date: date,
        timeout_seconds: float,
    ) -> tuple[date, dict[str, int]] | None:
        snapshot = self._fetch_tpex_institutional_rows(
            current_date,
            timeout_seconds=timeout_seconds,
        )
        if snapshot is None:
            return None
        resolved_date, rows = snapshot
        if resolved_date > current_date or not rows:
            return None
        return resolved_date, rows

    def _probe_tw_institutional_dates(
        self,
        now: datetime,
        *,
        board: str,
        probe: Callable[[date, float], _T | None],
    ) -> _T | None:
        """Walk publishable TW weekdays under a shared interactive wall-clock budget."""
        started_at = perf_counter()
        # Materialize weekdays first so candidate generation never blocks the
        # budget on TWSE holiday-calendar HTTP (cold cache / outage).
        candidate_dates = list(self._iter_tw_institutional_candidate_dates(now))
        probes = 0
        for current_date in candidate_dates:
            if probes >= _TW_INSTITUTIONAL_MAX_WEEKDAY_PROBES:
                break
            remaining = _TW_INSTITUTIONAL_FETCH_BUDGET_SECONDS - (
                perf_counter() - started_at
            )
            if remaining < _TW_INSTITUTIONAL_MIN_PROBE_SECONDS:
                logger.info(
                    "TW institutional rankings stopped date walk-back after budget",
                    extra={
                        "event": "rankings.tw_institutional.budget_exhausted",
                        "board": board,
                        "probes": probes,
                        "duration_ms": elapsed_ms(started_at),
                    },
                )
                break
            probes += 1
            probe_timeout = min(_TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS, remaining)
            result = self._call_tw_institutional_probe_with_deadline(
                probe,
                current_date,
                probe_timeout,
                board=board,
            )
            if result is not None:
                return result
        return None

    @staticmethod
    def _call_tw_institutional_probe_with_deadline(
        probe: Callable[[date, float], _T | None],
        current_date: date,
        timeout_seconds: float,
        *,
        board: str,
    ) -> _T | None:
        """Wait at most ``timeout_seconds`` wall-clock for one date probe.

        ``httpx.Timeout`` bounds idle connect/read/write, not total response
        duration. A trickling body can therefore outlive the interactive budget
        unless the caller enforces an end-to-end wait deadline.
        """
        future = _TW_INSTITUTIONAL_PROBE_EXECUTOR.submit(
            probe,
            current_date,
            timeout_seconds,
        )
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeout:
            logger.info(
                "TW institutional rankings probe exceeded wall-clock deadline",
                extra={
                    "event": "rankings.tw_institutional.probe_deadline",
                    "board": board,
                    "date": current_date.isoformat(),
                    "timeout_seconds": timeout_seconds,
                },
            )
            return None

    @staticmethod
    def _iter_tw_institutional_candidate_dates(now: datetime) -> Iterator[date]:
        """Weekday dates only, starting from the latest publishable day.

        Weekends are skipped without network I/O so Saturday/Sunday do not burn
        the shared fetch budget. Weekday exchange holidays are not filtered here:
        consulting the TWSE holiday page can stall longer than the whole budget
        when the cache is cold, so a closed weekday is absorbed as one bounded
        probe instead.
        """
        local_now = now.astimezone(_TW_TIMEZONE)
        candidate_date = local_now.date()
        if local_now.time().replace(second=0, microsecond=0) < TW_DAILY_PUBLISH_TIME:
            candidate_date -= timedelta(days=1)
        return iter_candidate_weekdays(
            candidate_date,
            TW_INSTITUTIONAL_DAILY_LOOKBACK_DAYS,
        )

    def _fetch_twse_institutional_rows(
        self,
        current_date: date,
        *,
        timeout_seconds: float = _TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, int]:
        try:
            payload = fetch_json(
                _TWSE_T86_URL,
                params={
                    "response": "json",
                    "date": current_date.strftime("%Y%m%d"),
                    "selectType": _TWSE_T86_SELECT_TYPE,
                },
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout_seconds=timeout_seconds,
                retries=_TW_INSTITUTIONAL_REQUEST_RETRIES,
                provider_name="twse",
                operation_name="rankings_tw_institutional_daily",
                resource_id=current_date.isoformat(),
            )
        except HttpFetchError:
            return {}
        if not isinstance(payload, dict):
            return {}

        rows = payload.get("data")
        fields = payload.get("fields")
        if not isinstance(rows, list) or not isinstance(fields, list) or not rows:
            return {}

        code_index = _field_index(fields, "證券代號")
        net_buy_sell_index = _field_index(fields, "三大法人買賣超股數")
        if code_index is None or net_buy_sell_index is None:
            return {}

        parsed: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, list):
                continue
            if code_index >= len(row) or net_buy_sell_index >= len(row):
                continue
            raw_symbol = str(row[code_index]).strip()
            raw_net_value = str(row[net_buy_sell_index]).strip()
            net_value = _parse_int(raw_net_value)
            if not raw_symbol or net_value is None:
                continue
            parsed[f"TW:{raw_symbol}"] = net_value
        return parsed

    def _fetch_tpex_institutional_rows(
        self,
        current_date: date,
        *,
        timeout_seconds: float = _TW_INSTITUTIONAL_REQUEST_TIMEOUT_SECONDS,
    ) -> tuple[date, dict[str, int]] | None:
        try:
            payload = fetch_json(
                _TPEX_DAILY_TRADE_URL,
                method="POST",
                data={
                    "response": "json",
                    "type": "Daily",
                    "sect": _TPEX_DAILY_TRADE_SECT,
                    "date": current_date.strftime("%Y/%m/%d"),
                },
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                },
                timeout_seconds=timeout_seconds,
                retries=_TW_INSTITUTIONAL_REQUEST_RETRIES,
                provider_name="tpex",
                operation_name="rankings_tpex_institutional_daily",
                resource_id=current_date.isoformat(),
            )
        except HttpFetchError:
            return None
        if not isinstance(payload, dict):
            return None

        tables = payload.get("tables")
        if not isinstance(tables, list):
            return None

        table = next(
            (
                row
                for row in tables
                if isinstance(row, dict)
                and isinstance(row.get("fields"), list)
                and isinstance(row.get("data"), list)
            ),
            None,
        )
        if table is None:
            return None

        fields = table.get("fields")
        rows = table.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            return None

        code_index = _field_index(fields, "代號")
        net_buy_sell_index = _field_index(fields, "三大法人買賣超股數合計")
        if code_index is None:
            return None
        if net_buy_sell_index is None:
            net_buy_sell_index = _field_index(fields, "三大法人買賣超股數")
        if net_buy_sell_index is None:
            return None

        parsed: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, list):
                continue
            if code_index >= len(row) or net_buy_sell_index >= len(row):
                continue
            raw_symbol = str(row[code_index]).strip()
            raw_net_value = str(row[net_buy_sell_index]).strip()
            net_value = _parse_int(raw_net_value)
            if not raw_symbol or net_value is None:
                continue
            parsed[f"TW:{raw_symbol}"] = net_value

        resolved_date = (
            _parse_roc_date(str(table.get("date", "")))
            or _parse_calendar_date(str(payload.get("date", "")))
            or current_date
        )
        return resolved_date, parsed

    @staticmethod
    def _filter_quote_candidates_for_ranking_type(
        candidates: list[_RankingCandidate],
        ranking_type: RankingType,
    ) -> list[_RankingCandidate]:
        """Drop rows that cannot be ranked honestly for this metric (quote path only).

        Pre-ranked Yahoo responses skip this filter; they are already scoped to the tab.
        """
        if ranking_type == RankingType.volume:
            return [c for c in candidates if c.volume is not None]
        if ranking_type == RankingType.change_up:
            return [
                c
                for c in candidates
                if c.change_percent is not None and c.change_percent > 0
            ]
        if ranking_type == RankingType.change_down:
            return [
                c
                for c in candidates
                if c.change_percent is not None and c.change_percent < 0
            ]
        if ranking_type == RankingType.volume_up:
            return [c for c in candidates if c.volume_delta_percent is not None]
        if ranking_type == RankingType.volume_down:
            return [
                c
                for c in candidates
                if c.volume_delta_percent is not None and c.volume_delta_percent < 0
            ]
        return candidates

    @staticmethod
    def _sort_quote_candidates(
        candidates: list[_RankingCandidate],
        ranking_type: RankingType,
    ) -> list[_RankingCandidate]:
        if ranking_type == RankingType.volume:
            return sorted(
                candidates,
                key=lambda item: (
                    item.volume is None,
                    -(item.volume or -1),
                    item.stock.symbol,
                ),
            )
        if ranking_type == RankingType.change_up:
            return sorted(
                candidates,
                key=lambda item: (
                    item.change_percent is None,
                    -(
                        item.change_percent
                        if item.change_percent is not None
                        else -10**9
                    ),
                    -(item.volume or -1),
                    item.stock.symbol,
                ),
            )
        if ranking_type == RankingType.change_down:
            return sorted(
                candidates,
                key=lambda item: (
                    item.change_percent is None,
                    item.change_percent if item.change_percent is not None else 10**9,
                    -(item.volume or -1),
                    item.stock.symbol,
                ),
            )
        if ranking_type == RankingType.volume_up:
            return sorted(
                candidates,
                key=lambda item: (
                    item.volume_delta_percent is None,
                    -(
                        item.volume_delta_percent
                        if item.volume_delta_percent is not None
                        else -10**9
                    ),
                    -(item.volume or -1),
                    item.stock.symbol,
                ),
            )
        return sorted(
            candidates,
            key=lambda item: (
                item.volume_delta_percent is None,
                item.volume_delta_percent if item.volume_delta_percent is not None else 10**9,
                -(item.volume or -1),
                item.stock.symbol,
            ),
        )

    @staticmethod
    def _sort_valuation_candidates(candidates: list[_RankingCandidate]) -> list[_RankingCandidate]:
        return sorted(
            candidates,
            key=lambda item: (
                item.pe_ratio is None,
                item.pe_ratio if item.pe_ratio is not None else 10**9,
                -(item.volume or -1),
                item.stock.symbol,
            ),
        )

    @staticmethod
    def _sort_tw_institutional_candidates(
        candidates: list[_RankingCandidate],
        ranking_type: RankingType,
    ) -> list[_RankingCandidate]:
        if ranking_type == RankingType.net_buy:
            return sorted(
                candidates,
                key=lambda item: (
                    item.net_buy_sell_shares is None,
                    -(
                        item.net_buy_sell_shares
                        if item.net_buy_sell_shares is not None
                        else -10**9
                    ),
                    item.stock.symbol,
                ),
            )
        return sorted(
            candidates,
            key=lambda item: (
                item.net_buy_sell_shares is None,
                item.net_buy_sell_shares if item.net_buy_sell_shares is not None else 10**9,
                item.stock.symbol,
            ),
        )


def _fresh_cached_enrichment_quote(item: object) -> QuoteRecord | None:
    """Return a cache-first quote only when it is present and not stale."""
    quote = getattr(item, "quote", None)
    if quote is None or getattr(item, "stale", False):
        return None
    if not isinstance(quote, QuoteRecord):
        return None
    return quote


def _stock_schema(stock: StockRecord) -> StockResponse:
    return StockResponse(
        id=stock.id,
        market=stock.market,
        symbol=stock.symbol,
        name=stock.name,
        exchange=stock.exchange,
        currency=stock.currency,
        timezone=stock.timezone,
        nameEn=stock.name_en,
        nameZhTw=stock.name_zh_tw,
    )


def _ranking_item(rank: int, candidate: _RankingCandidate) -> RankingItem:
    return RankingItem(
        rank=rank,
        stock=candidate.stock,
        lastPrice=candidate.last_price,
        changePercent=candidate.change_percent,
        volume=candidate.volume,
        volumeDeltaPercent=candidate.volume_delta_percent,
        netBuySellShares=candidate.net_buy_sell_shares,
        peRatio=candidate.pe_ratio,
        peRatioType=candidate.pe_ratio_type,
        peRatioSource=candidate.pe_ratio_source,
        valuationAsOf=candidate.valuation_as_of,
        valuationWarning=candidate.valuation_warning,
        timestamp=candidate.timestamp,
    )


def _field_index(fields: list[object], label: str) -> int | None:
    for index, field in enumerate(fields):
        if str(field).strip() == label:
            return index
    return None


def _parse_int(value: str) -> int | None:
    normalized = value.replace(",", "").strip()
    if normalized in {"", "--", "---"}:
        return None
    try:
        return int(float(normalized))
    except ValueError:
        return None


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) and numeric > 0 else None
    if isinstance(value, str):
        try:
            numeric = float(value.replace(",", "").strip())
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) and numeric > 0 else None
    return None


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _parse_roc_date(value: str) -> date | None:
    parts = [segment for segment in value.strip().split("/") if segment]
    if len(parts) != 3:
        return None
    try:
        year = 1911 + int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
        return date(year, month, day)
    except ValueError:
        return None


def _parse_calendar_date(value: str) -> date | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) != 8:
        return None
    try:
        return date(
            int(digits[0:4]),
            int(digits[4:6]),
            int(digits[6:8]),
        )
    except ValueError:
        return None


rankings_service = RankingsService()
