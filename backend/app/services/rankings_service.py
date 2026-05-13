from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services.http_client import HttpFetchError, fetch_json
from app.services.market_clock import is_market_open
from app.services.quote_service import QuoteService, quote_service
from app.services.rankings_volume_baseline_cache import (
    VolumeBaselineStore,
    default_volume_baseline_store,
)


RankingMarket = Literal["US", "TW"]

US_RANKING_STOCK_IDS = [
    "US:AAPL",
    "US:MSFT",
    "US:NVDA",
    "US:MU",
    "US:TSLA",
    "US:AMD",
    "US:META",
    "US:AMZN",
    "US:GOOGL",
]

TW_RANKING_STOCK_IDS = [
    "TW:0050",
    "TW:2330",
    "TW:2317",
    "TW:2454",
    "TW:2303",
    "TW:2881",
    "TW:2882",
]

MARKET_UNIVERSES: dict[RankingMarket, list[str]] = {
    "US": US_RANKING_STOCK_IDS,
    "TW": TW_RANKING_STOCK_IDS,
}

_UNSUPPORTED_REASONS: dict[RankingType, str] = {
    RankingType.net_buy: "US institutional net-buy data is not available from free-only sources in this app.",
    RankingType.net_sell: "US institutional net-sell data is not available from free-only sources in this app.",
}

_QUOTE_SOURCE = "Quote rankings from the app's free best-effort quote providers."
_QUOTE_WARNING = "Free unofficial quote source; data may be delayed or unavailable."
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
    ) -> None:
        self.quote_source = quote_source or quote_service
        self._volume_baselines = volume_baseline_store or default_volume_baseline_store

    def get_rankings(
        self,
        market: RankingMarket,
        ranking_type: RankingType | str,
        limit: int,
        now: datetime | None = None,
    ) -> RankingResponse:
        current = now or datetime.now(timezone.utc)
        normalized_type = RankingType(ranking_type)
        if normalized_type in {RankingType.net_buy, RankingType.net_sell}:
            if market == "US":
                return self._unsupported_response(market, normalized_type, current)
            return self._build_tw_institutional_ranking(normalized_type, limit, current)
        return self._build_quote_ranking(market, normalized_type, limit, current)

    def _build_quote_ranking(
        self,
        market: RankingMarket,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
    ) -> RankingResponse:
        market_open = is_market_open(market, now)
        candidates: list[_RankingCandidate] = []
        baseline_missing = False

        for stock, quote in self._get_quote_rankings_snapshot(market):
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
            items=items,
            timestamp=now.isoformat(),
            source=_QUOTE_SOURCE,
            sourceType=RankingSourceType.free_unofficial,
            isMarketOpen=market_open,
            dataFreshnessLabel=freshness,
            warningMessage=_QUOTE_WARNING,
            unsupportedReason=None,
        )

    def _get_quote_rankings_snapshot(
        self,
        market: RankingMarket,
    ) -> list[tuple[StockRecord, QuoteRecord]]:
        stock_ids = MARKET_UNIVERSES[market]
        if market == "US":
            get_live_quotes = getattr(self.quote_source, "get_live_quotes", None)
            if callable(get_live_quotes):
                return get_live_quotes(stock_ids)
        return self.quote_source.get_quotes(stock_ids)

    def _build_tw_institutional_ranking(
        self,
        ranking_type: RankingType,
        limit: int,
        now: datetime,
    ) -> RankingResponse:
        market_open = is_market_open("TW", now)
        institutional_snapshot = self._fetch_tw_institutional_snapshot(now)
        if institutional_snapshot is None:
            return RankingResponse(
                market="TW",
                rankingType=ranking_type,
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
            if stock is None:
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
