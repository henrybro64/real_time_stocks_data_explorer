from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
from time import perf_counter
from typing import Literal
from zoneinfo import ZoneInfo

from app.core.diagnostics import elapsed_ms
from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
    TwBoard,
)
from app.schemas.stock import StockResponse
from app.services.http_client import HttpFetchError, fetch_json
from app.services.market_clock import is_market_open
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
from app.services.sp500_constituents import load_sp500_symbols

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
_QUOTE_WARNING = "Free unofficial quote source; data may be delayed or unavailable."
_PRE_RANKED_SOURCE = "Yahoo Finance pre-ranked quote rankings."
_SP500_CHANGE_SOURCE = (
    "S&P 500 movers: constituent snapshot plus batched Yahoo Finance v7 quote fields "
    "(free best-effort; not the same universe as Yahoo's predefined gainers/losers screeners)."
)
_PRE_RANKED_TYPES = {RankingType.volume, RankingType.change_up, RankingType.change_down}
_TW_DAILY_WARNING = "台股三大法人買賣超為日資料，非盤中即時資料。"
_TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"
_TWSE_T86_SELECT_TYPE = "ALLBUT0999"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TW_TIMEZONE = ZoneInfo("Asia/Taipei")


@dataclass
class _RankingCandidate:
    stock: StockResponse
    last_price: float | None
    change_percent: float | None
    volume: int | None
    volume_delta_percent: float | None
    net_buy_sell_shares: int | None
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
    ) -> None:
        self.quote_source = quote_source or quote_service
        self._volume_baselines = volume_baseline_store or default_volume_baseline_store
        self._external_source = external_source or rankings_external_source
        self._sp500_quote_batch = sp500_quote_batch or yahoo_v7_quotes_batch
        self._sp500_symbol_loader = sp500_symbol_loader or load_sp500_symbols

    def get_rankings(
        self,
        market: RankingMarket,
        ranking_type: RankingType | str,
        limit: int,
        now: datetime | None = None,
        tw_board: TwBoard | None = None,
    ) -> RankingResponse:
        started_at = perf_counter()
        current = now or datetime.now(timezone.utc)
        normalized_type = RankingType(ranking_type)
        effective_tw_board: TwBoard | None = tw_board if market == "TW" else None
        if market == "TW" and effective_tw_board is None:
            effective_tw_board = "TWSE"
        if normalized_type in {RankingType.net_buy, RankingType.net_sell}:
            if market == "US":
                response = self._unsupported_response(market, normalized_type, current)
            else:
                assert effective_tw_board is not None
                response = self._build_tw_institutional_ranking(
                    normalized_type, limit, current, effective_tw_board
                )
        else:
            response = self._build_quote_ranking(
                market, normalized_type, limit, current, effective_tw_board
            )
        logger.info(
            "Rankings request completed",
            extra={
                "event": "rankings.completed",
                "market": market,
                "ranking_type": normalized_type.value,
                "limit": limit,
                "tw_board": effective_tw_board,
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
    ) -> RankingResponse:
        market_open = is_market_open(market, now)

        if (
            market == "US"
            and ranking_type in {RankingType.change_up, RankingType.change_down}
        ):
            sp500_ranking = self._try_build_us_sp500_price_movers(
                ranking_type, limit, now, market_open
            )
            if sp500_ranking is not None and sp500_ranking.items:
                return sp500_ranking

        if ranking_type in _PRE_RANKED_TYPES:
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
                )

        candidates: list[_RankingCandidate] = []
        baseline_missing = False

        for stock, quote in self._get_quote_rankings_snapshot(market, tw_board):
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
                    timestamp=quote.timestamp,
                )
            )

        candidates = self._filter_quote_candidates_for_ranking_type(candidates, ranking_type)
        ranked_candidates = self._sort_quote_candidates(candidates, ranking_type)[:limit]
        items = [
            _ranking_item(index, candidate)
            for index, candidate in enumerate(ranked_candidates, start=1)
        ]

        if ranking_type == RankingType.volume:
            if market_open:
                freshness = "Market open; rankings use the latest quote snapshot."
            else:
                freshness = "Market closed; rankings use the latest available quote snapshot."
        elif ranking_type in {RankingType.change_up, RankingType.change_down}:
            if market_open:
                freshness = "Market open; price-move rankings use the latest quote snapshot."
            else:
                freshness = (
                    "Market closed; price-move rankings use the latest available quote snapshot."
                )
        elif baseline_missing:
            freshness = (
                "Baseline unavailable on the first snapshot; volume change percent stays empty until a later refresh."
            )
        elif market_open:
            freshness = "Volume change uses the previous quote snapshot baseline."
        else:
            freshness = "Market closed; volume change uses the previous quote snapshot baseline."

        return RankingResponse(
            market=market,
            rankingType=ranking_type,
            twBoard=tw_board if market == "TW" else None,
            items=items,
            timestamp=now.isoformat(),
            source=_QUOTE_SOURCE,
            sourceType=RankingSourceType.free_unofficial,
            isMarketOpen=market_open,
            dataFreshnessLabel=freshness,
            warningMessage=_QUOTE_WARNING,
            unsupportedReason=None,
            usRankingsScope="quote" if market == "US" else None,
        )

    def _get_quote_rankings_snapshot(
        self,
        market: RankingMarket,
        tw_board: TwBoard | None,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        if market == "US":
            return self._us_sp500_quote_snapshot_envelopes()
        assert tw_board is not None
        stock_ids = _tw_quote_universe_ids(tw_board)
        return self.quote_source.get_quotes(stock_ids)

    def _us_sp500_quote_snapshot_envelopes(self) -> list[tuple[StockRecord, QuoteRecord]]:
        """US quote-path rankings: S&P 500 constituent snapshot; omit symbols with no quote."""
        symbols = self._sp500_symbol_loader()
        if not symbols:
            return []
        stocks: list[StockRecord] = []
        for sym in symbols:
            sid = f"US:{sym}"
            stock = self.quote_source.get_stock(sid)
            if stock is None:
                continue
            stocks.append(stock)

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
        stocks: list[StockRecord] = []
        for sym in symbols:
            sid = f"US:{sym}"
            stock = self.quote_source.get_stock(sid)
            if stock is None:
                continue
            stocks.append(stock)

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

        return RankingResponse(
            market=market,
            rankingType=ranking_type,
            twBoard=tw_board if market == "TW" else None,
            items=items,
            timestamp=now.isoformat(),
            source=rankings_source_text or _PRE_RANKED_SOURCE,
            sourceType=source_type,
            isMarketOpen=market_open,
            dataFreshnessLabel=freshness,
            warningMessage=_QUOTE_WARNING,
            unsupportedReason=None,
            usRankingsScope=us_rankings_scope if market == "US" else None,
        )

    def _build_tw_institutional_ranking(
        self,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
        tw_board: TwBoard,
    ) -> RankingResponse:
        market_open = is_market_open("TW", now)
        institutional_snapshot = self._fetch_tw_institutional_snapshot(now)
        if institutional_snapshot is None:
            return RankingResponse(
                market="TW",
                rankingType=ranking_type,
                twBoard=tw_board,
                items=[],
                timestamp=now.isoformat(),
                source="TWSE T86 daily institutional data",
                sourceType=RankingSourceType.unsupported,
                isMarketOpen=market_open,
                dataFreshnessLabel="TW daily institutional ranking is unavailable from the free public source right now.",
                warningMessage=_TW_DAILY_WARNING,
                unsupportedReason="TW institutional daily data is temporarily unavailable from the free public source.",
            )

        ranking_date, institutional_rows = institutional_snapshot
        candidates: list[_RankingCandidate] = []
        ranking_timestamp = datetime.combine(
            ranking_date,
            time(18, 0),
            tzinfo=_TW_TIMEZONE,
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
                    timestamp=ranking_timestamp,
                )
            )

        ranked_candidates = self._sort_tw_institutional_candidates(candidates, ranking_type)[:limit]
        quotes_by_id = {
            stock.id: quote
            for stock, quote in self.quote_source.get_quotes(
                [candidate.stock.id for candidate in ranked_candidates]
            )
        }
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

        freshness = (
            "TW daily institutional ranking sorted by highest 三大法人買賣超股數."
            if ranking_type == RankingType.net_buy
            else "TW daily institutional ranking sorted by most negative 三大法人買賣超股數."
        )
        if not market_open:
            freshness = f"Market closed; {freshness}"

        return RankingResponse(
            market="TW",
            rankingType=ranking_type,
            twBoard=tw_board,
            items=items,
            timestamp=now.isoformat(),
            source="TWSE T86 daily institutional data",
            sourceType=RankingSourceType.free_public,
            isMarketOpen=market_open,
            dataFreshnessLabel=freshness,
            warningMessage=_TW_DAILY_WARNING,
            unsupportedReason=None,
        )

    def _unsupported_response(
        self,
        market: RankingMarket,
        ranking_type: RankingType,
        now: datetime,
    ) -> RankingResponse:
        return RankingResponse(
            market=market,
            rankingType=ranking_type,
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
    ) -> tuple[date, dict[str, int]] | None:
        local_now = now.astimezone(_TW_TIMEZONE)
        candidate_date = local_now.date()
        if local_now.time() < time(18, 0):
            candidate_date -= timedelta(days=1)

        for day_offset in range(7):
            current_date = candidate_date - timedelta(days=day_offset)
            rows = self._fetch_tw_institutional_rows(current_date)
            if rows:
                return current_date, rows
        return None

    def _fetch_tw_institutional_rows(self, current_date: date) -> dict[str, int]:
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
                timeout_seconds=8,
                retries=1,
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


rankings_service = RankingsService()
