from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from app.core.config import settings
from app.schemas.rankings import (
    RankingResponse,
    RankingSourceType,
    RankingType,
    RankingUniverse,
    TwBoard,
    UsUniverse,
)
from app.services.market_clock import (
    current_rankings_snapshot_bucket,
    is_market_open,
    tw_institutional_daily_snapshot_bucket,
)
from app.services.providers.us_provider import yahoo_v7_quotes_batch
from app.services.quote_service import QuoteService, quote_service
from app.services.rankings_external_source import rankings_external_source
from app.services.rankings_service import RankingsService, _tw_quote_universe_ids
from app.services.sp500_constituents import load_sp500_symbols


RankingMarket = Literal["US", "TW"]
_UNSUPPORTED_US_INSTITUTIONAL_REASON = (
    "US institutional net-buy/net-sell is not available in this free-only app."
)


@dataclass(frozen=True)
class RankingQuery:
    market: RankingMarket
    ranking_type: RankingType
    tw_board: TwBoard | None = None
    universe: RankingUniverse | None = None


class RankingDataProvider(Protocol):
    def current_bucket_start(
        self,
        query: RankingQuery,
        now: datetime,
    ) -> datetime | None: ...

    def fetch(
        self,
        query: RankingQuery,
        *,
        limit: int,
        now: datetime,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse: ...


class _DisabledExternalRankingsSource:
    def fetch(  # noqa: D401
        self,
        market,
        ranking_type,
        limit,
        now=None,
        tw_board=None,
    ):
        return None


def _empty_sp500_quote_batch(_stocks):
    return {}


def _resolve_bounded_tw_universe(
    quote_source: QuoteService,
    tw_board: TwBoard,
) -> list[str]:
    stock_ids: list[str] = []
    for symbol in settings.parsed_rankings_universe_tw:
        stock_id = symbol if symbol.startswith("TW:") else f"TW:{symbol}"
        stock = quote_source.get_stock(stock_id)
        if stock is None or stock.exchange != tw_board:
            continue
        stock_ids.append(stock_id)
    return stock_ids


def _build_quote_rankings_service(
    *,
    use_bounded_universe: bool,
    quote_source: QuoteService | None = None,
) -> RankingsService:
    resolved_quote_source = quote_source or quote_service
    if use_bounded_universe:
        return RankingsService(
            quote_source=resolved_quote_source,
            external_source=_DisabledExternalRankingsSource(),
            sp500_quote_batch=_empty_sp500_quote_batch,
            sp500_symbol_loader=lambda: settings.parsed_rankings_universe_us,
            tw_stock_ids_resolver=lambda board: _resolve_bounded_tw_universe(
                resolved_quote_source,
                board,
            ),
        )
    return RankingsService(
        quote_source=resolved_quote_source,
        external_source=rankings_external_source,
        sp500_quote_batch=yahoo_v7_quotes_batch,
        sp500_symbol_loader=load_sp500_symbols,
        tw_stock_ids_resolver=_tw_quote_universe_ids,
    )


class FreeQuoteRankingProvider:
    def __init__(
        self,
        quote_source: QuoteService | None = None,
        *,
        use_bounded_universe: bool = False,
    ) -> None:
        self._quote_source = quote_source or quote_service
        self._service = _build_quote_rankings_service(
            use_bounded_universe=use_bounded_universe,
            quote_source=self._quote_source,
        )

    def current_bucket_start(
        self,
        query: RankingQuery,
        now: datetime,
    ) -> datetime | None:
        return current_rankings_snapshot_bucket(query.market, now)

    def fetch(
        self,
        query: RankingQuery,
        *,
        limit: int,
        now: datetime,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse:
        us_universe: UsUniverse | None = None
        if query.market == "US" and query.universe in {"DJIA", "NASDAQ100"}:
            us_universe = query.universe
        return self._service.get_rankings(
            query.market,
            query.ranking_type,
            limit=limit,
            now=now,
            tw_board=query.tw_board,
            us_universe=us_universe,
            allow_hardcoded_fallback=allow_hardcoded_fallback,
        )


class FreeTWInstitutionalDailyProvider:
    def __init__(
        self,
        quote_source: QuoteService | None = None,
    ) -> None:
        self._service = RankingsService(quote_source=quote_source or quote_service)

    def current_bucket_start(
        self,
        query: RankingQuery,
        now: datetime,
    ) -> datetime | None:
        del query
        return tw_institutional_daily_snapshot_bucket(now)

    def fetch(
        self,
        query: RankingQuery,
        *,
        limit: int,
        now: datetime,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse:
        del allow_hardcoded_fallback
        return self._service.get_rankings(
            "TW",
            query.ranking_type,
            limit=limit,
            now=now,
            tw_board=query.tw_board,
        )


class UnsupportedUSInstitutionalProvider:
    def current_bucket_start(
        self,
        query: RankingQuery,
        now: datetime,
    ) -> datetime | None:
        del query, now
        return None

    def fetch(
        self,
        query: RankingQuery,
        *,
        limit: int,
        now: datetime,
        allow_hardcoded_fallback: bool = True,
    ) -> RankingResponse:
        del limit, allow_hardcoded_fallback
        return RankingResponse(
            market="US",
            rankingType=query.ranking_type,
            twBoard=None,
            universe=query.universe if query.universe in {"DJIA", "NASDAQ100"} else "DJIA",
            items=[],
            timestamp=now.astimezone(timezone.utc).isoformat(),
            source="Unsupported in this free-only app",
            sourceType=RankingSourceType.unsupported,
            isMarketOpen=is_market_open("US", now),
            dataFreshnessLabel="Unsupported in this free-only app.",
            warningMessage=None,
            unsupportedReason=_UNSUPPORTED_US_INSTITUTIONAL_REASON,
            usRankingsScope=None,
        )


class RankingProviderRegistry:
    def __init__(
        self,
        quote_provider: RankingDataProvider | None = None,
        tw_institutional_provider: RankingDataProvider | None = None,
        unsupported_us_institutional_provider: RankingDataProvider | None = None,
        *,
        use_bounded_universe: bool = False,
    ) -> None:
        self._quote_provider = quote_provider or FreeQuoteRankingProvider(
            use_bounded_universe=use_bounded_universe,
        )
        self._tw_institutional_provider = (
            tw_institutional_provider or FreeTWInstitutionalDailyProvider()
        )
        self._unsupported_us_institutional_provider = (
            unsupported_us_institutional_provider or UnsupportedUSInstitutionalProvider()
        )

    def get_provider(self, query: RankingQuery) -> RankingDataProvider:
        if query.ranking_type in {RankingType.net_buy, RankingType.net_sell}:
            if query.market == "US":
                return self._unsupported_us_institutional_provider
            return self._tw_institutional_provider
        return self._quote_provider


ranking_provider_registry = RankingProviderRegistry(use_bounded_universe=False)
scheduler_ranking_provider_registry = RankingProviderRegistry(use_bounded_universe=True)
