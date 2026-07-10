from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Callable, Literal

from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError
from app.services.taiwan_history_scan import (
    RESULT_CACHE_TTL,
    candidate_start_date,
    collect_dated_history,
)
from app.services.taiwan_market_data import (
    TaiwanMarket,
    fetch_tpex_data,
    fetch_twse_data,
    is_taiwan_no_data_payload,
    normalize_taiwan_date,
    normalize_taiwan_symbol,
    parse_taiwan_number,
    resolve_taiwan_market,
    taiwan_field_index,
    taiwan_row_value,
)

if TYPE_CHECKING:
    from app.services.quote_service import QuoteService

# Keep aligned with rankings_service / official TWSE fund endpoints.
_TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"
_TWSE_T86_SELECT_TYPE = "ALLBUT0999"
_TPEX_DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
_TPEX_DAILY_TRADE_SECT = "EW"
_TWSE_SOURCE = "TWSE T86 daily institutional data"
_TPEX_SOURCE = "TPEx institutional daily data"
_DEFAULT_HISTORY_DAYS = 90
_RESULT_CACHE_TTL = RESULT_CACHE_TTL

_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan institutional data is only available for TW stocks"
_UNKNOWN_TW_MARKET_ERROR = "Taiwan institutional market is unavailable for this stock"
_UPSTREAM_UNAVAILABLE_ERROR = (
    "Taiwan institutional data is temporarily unavailable from official sources."
)

_TPEX_FOREIGN_NET_OFFSET = 2
_TPEX_INVESTMENT_TRUST_NET_OFFSET = 11
_TPEX_DEALER_SELF_NET_OFFSET = 14
_TPEX_DEALER_HEDGE_NET_OFFSET = 17
_TPEX_DEALER_NET_OFFSET = 20

TWBoard = Literal["TWSE", "TPEx"]
PayloadFetcher = Callable[[date], object]
NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class TaiwanInstitutionalInvestorRecord:
    date: str
    symbol: str
    name: str
    foreign_net_shares: int
    investment_trust_net_shares: int
    dealer_self_net_shares: int | None
    dealer_hedge_net_shares: int | None
    dealer_net_shares: int
    total_net_shares: int


@dataclass(frozen=True)
class TaiwanInstitutionalInvestorsResult:
    stock_id: str
    symbol: str
    market: TWBoard
    latest_date: str | None
    summary: TaiwanInstitutionalInvestorRecord | None
    history: tuple[TaiwanInstitutionalInvestorRecord, ...]
    source: str


@dataclass(frozen=True)
class _DailyLookupResult:
    record: TaiwanInstitutionalInvestorRecord | None
    request_succeeded: bool


@dataclass(frozen=True)
class _ResultCacheEntry:
    cached_at: datetime
    result: TaiwanInstitutionalInvestorsResult


class TaiwanInstitutionalInvestorsService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        twse_fetcher: PayloadFetcher | None = None,
        tpex_fetcher: PayloadFetcher | None = None,
        now_provider: NowProvider | None = None,
    ) -> None:
        self._quote_source = quote_source or _default_quote_source()
        self._twse_fetcher = twse_fetcher or _fetch_twse_payload
        self._tpex_fetcher = tpex_fetcher or _fetch_tpex_payload
        self._now_provider = now_provider or _utc_now
        self._result_cache: dict[tuple[str, int], _ResultCacheEntry] = {}

    def get_institutional_investors(
        self,
        stock_id: str,
        *,
        days: int = _DEFAULT_HISTORY_DAYS,
    ) -> TaiwanInstitutionalInvestorsResult:
        requested_days = max(days, 1)
        cache_key = (stock_id, requested_days)
        now = self._now_provider()
        cached = self._result_cache.get(cache_key)
        if cached is not None and now - cached.cached_at < _RESULT_CACHE_TTL:
            return cached.result

        stock = self._quote_source.get_stock(stock_id)
        if stock is None:
            raise ValueError(_STOCK_NOT_FOUND_ERROR)
        if stock.market != "TW":
            raise ValueError(_NON_TW_STOCK_ERROR)

        market, symbol = self._resolve_market_and_symbol(stock)
        history, successful_fetches = self._collect_history(
            market=market,
            symbol=symbol,
            requested_days=requested_days,
            start_date=candidate_start_date(now),
        )

        if not history and successful_fetches == 0:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)

        history.reverse()
        latest = history[-1] if history else None
        result = TaiwanInstitutionalInvestorsResult(
            stock_id=stock.id,
            symbol=symbol,
            market=market,
            latest_date=latest.date if latest is not None else None,
            summary=latest,
            history=tuple(history),
            source=_source_for_market(market),
        )
        self._result_cache[cache_key] = _ResultCacheEntry(cached_at=now, result=result)
        return result

    def _resolve_market_and_symbol(self, stock: StockRecord) -> tuple[TWBoard, str]:
        market = resolve_taiwan_market(
            stock.id,
            quote_source=self._quote_source,
            known_stocks=[stock],
        )
        if market not in {"TWSE", "TPEx"}:
            raise ValueError(_UNKNOWN_TW_MARKET_ERROR)

        symbol = normalize_taiwan_symbol(stock.symbol) or normalize_taiwan_symbol(stock.id)
        if symbol is None:
            raise ValueError(_UNKNOWN_TW_MARKET_ERROR)
        return market, symbol

    def _collect_history(
        self,
        *,
        market: TWBoard,
        symbol: str,
        requested_days: int,
        start_date: date,
    ) -> tuple[list[TaiwanInstitutionalInvestorRecord], int]:
        def fetch_day(
            current_date: date,
        ) -> tuple[TaiwanInstitutionalInvestorRecord | None, bool]:
            lookup = self._fetch_record_for_day(market, current_date, symbol)
            return lookup.record, lookup.request_succeeded

        return collect_dated_history(
            requested_days=requested_days,
            start_date=start_date,
            fetch_day=fetch_day,
            record_date=lambda record: record.date,
        )

    def _fetch_record_for_day(
        self,
        market: TWBoard,
        current_date: date,
        symbol: str,
    ) -> _DailyLookupResult:
        if market == "TPEx":
            return self._fetch_tpex_record(current_date, symbol)
        return self._fetch_twse_record(current_date, symbol)

    def _fetch_twse_record(
        self,
        current_date: date,
        symbol: str,
    ) -> _DailyLookupResult:
        try:
            payload = self._twse_fetcher(current_date)
        except HttpFetchError:
            return _DailyLookupResult(record=None, request_succeeded=False)

        if is_taiwan_no_data_payload(payload):
            return _DailyLookupResult(record=None, request_succeeded=True)
        if not isinstance(payload, dict):
            return _DailyLookupResult(record=None, request_succeeded=False)

        rows = payload.get("data")
        fields = payload.get("fields")
        if not isinstance(rows, list) or not isinstance(fields, list):
            return _DailyLookupResult(record=None, request_succeeded=False)

        record_date = normalize_taiwan_date(payload.get("date")) or current_date.isoformat()
        for row in rows:
            if not isinstance(row, list):
                continue
            record = _parse_twse_row(row, fields, record_date=record_date)
            if record is None or record.symbol != symbol:
                continue
            return _DailyLookupResult(record=record, request_succeeded=True)
        return _DailyLookupResult(record=None, request_succeeded=True)

    def _fetch_tpex_record(
        self,
        current_date: date,
        symbol: str,
    ) -> _DailyLookupResult:
        try:
            payload = self._tpex_fetcher(current_date)
        except HttpFetchError:
            return _DailyLookupResult(record=None, request_succeeded=False)

        if is_taiwan_no_data_payload(payload):
            return _DailyLookupResult(record=None, request_succeeded=True)
        if not isinstance(payload, dict):
            return _DailyLookupResult(record=None, request_succeeded=False)

        tables = payload.get("tables")
        if not isinstance(tables, list):
            return _DailyLookupResult(record=None, request_succeeded=False)

        table = next(
            (
                item
                for item in tables
                if isinstance(item, dict)
                and isinstance(item.get("fields"), list)
                and isinstance(item.get("data"), list)
            ),
            None,
        )
        if table is None:
            return _DailyLookupResult(record=None, request_succeeded=False)

        rows = table.get("data")
        fields = table.get("fields")
        if not isinstance(rows, list) or not isinstance(fields, list):
            return _DailyLookupResult(record=None, request_succeeded=False)

        record_date = (
            normalize_taiwan_date(table.get("date"))
            or normalize_taiwan_date(payload.get("date"))
            or current_date.isoformat()
        )
        for row in rows:
            if not isinstance(row, list):
                continue
            record = _parse_tpex_row(row, fields, record_date=record_date)
            if record is None or record.symbol != symbol:
                continue
            return _DailyLookupResult(record=record, request_succeeded=True)
        return _DailyLookupResult(record=None, request_succeeded=True)


def _fetch_twse_payload(current_date: date) -> object:
    return fetch_twse_data(
        _TWSE_T86_URL,
        params={
            "response": "json",
            "date": current_date.strftime("%Y%m%d"),
            "selectType": _TWSE_T86_SELECT_TYPE,
        },
        operation_name="tw_institutional_twse_daily",
        resource_id=current_date.isoformat(),
    )


def _fetch_tpex_payload(current_date: date) -> object:
    return fetch_tpex_data(
        _TPEX_DAILY_TRADE_URL,
        method="POST",
        data={
            "response": "json",
            "type": "Daily",
            "sect": _TPEX_DAILY_TRADE_SECT,
            "date": current_date.strftime("%Y/%m/%d"),
        },
        operation_name="tw_institutional_tpex_daily",
        resource_id=current_date.isoformat(),
    )


def _parse_twse_row(
    row: Sequence[object],
    fields: Sequence[object],
    *,
    record_date: str,
) -> TaiwanInstitutionalInvestorRecord | None:
    symbol = normalize_taiwan_symbol(taiwan_row_value(row, fields, "證券代號"))
    if symbol is None:
        return None

    name = str(taiwan_row_value(row, fields, "證券名稱") or "").strip()
    foreign_net_shares = _parse_share_int(
        taiwan_row_value(
            row,
            fields,
            "外陸資買賣超股數(不含外資自營商)",
        )
    )
    investment_trust_net_shares = _parse_share_int(
        taiwan_row_value(row, fields, "投信買賣超股數")
    )
    dealer_net_shares = _parse_share_int(
        taiwan_row_value(row, fields, "自營商買賣超股數")
    )
    dealer_self_net_shares = _parse_share_int(
        taiwan_row_value(row, fields, "自營商買賣超股數(自行買賣)")
    )
    dealer_hedge_net_shares = _parse_share_int(
        taiwan_row_value(row, fields, "自營商買賣超股數(避險)")
    )

    return _build_record(
        symbol=symbol,
        name=name,
        record_date=record_date,
        foreign_net_shares=foreign_net_shares,
        investment_trust_net_shares=investment_trust_net_shares,
        dealer_self_net_shares=dealer_self_net_shares,
        dealer_hedge_net_shares=dealer_hedge_net_shares,
        official_dealer_net_shares=dealer_net_shares,
    )


def _parse_tpex_row(
    row: Sequence[object],
    fields: Sequence[object],
    *,
    record_date: str,
) -> TaiwanInstitutionalInvestorRecord | None:
    code_index = taiwan_field_index(fields, "代號")
    name_index = taiwan_field_index(fields, "名稱")
    total_index = taiwan_field_index(fields, "三大法人買賣超股數合計", "三大法人買賣超股數")
    if code_index is None or name_index is None or total_index is None:
        return None

    # TPEx flattens grouped headers into repeated "買進/賣出/買賣超" columns.
    # The group order is stable in the official payload, so the offsets are positional.
    group_start = max(code_index, name_index) + 1
    if len(row) <= group_start + _TPEX_DEALER_NET_OFFSET:
        return None

    symbol = normalize_taiwan_symbol(row[code_index])
    if symbol is None:
        return None
    name = str(row[name_index]).strip()

    foreign_net_shares = _parse_share_int(_row_at(row, group_start + _TPEX_FOREIGN_NET_OFFSET))
    investment_trust_net_shares = _parse_share_int(
        _row_at(row, group_start + _TPEX_INVESTMENT_TRUST_NET_OFFSET)
    )
    dealer_self_net_shares = _parse_share_int(_row_at(row, group_start + _TPEX_DEALER_SELF_NET_OFFSET))
    dealer_hedge_net_shares = _parse_share_int(
        _row_at(row, group_start + _TPEX_DEALER_HEDGE_NET_OFFSET)
    )
    dealer_net_shares = _parse_share_int(_row_at(row, group_start + _TPEX_DEALER_NET_OFFSET))

    return _build_record(
        symbol=symbol,
        name=name,
        record_date=record_date,
        foreign_net_shares=foreign_net_shares,
        investment_trust_net_shares=investment_trust_net_shares,
        dealer_self_net_shares=dealer_self_net_shares,
        dealer_hedge_net_shares=dealer_hedge_net_shares,
        official_dealer_net_shares=dealer_net_shares,
    )


def _build_record(
    *,
    symbol: str,
    name: str,
    record_date: str,
    foreign_net_shares: int | None,
    investment_trust_net_shares: int | None,
    dealer_self_net_shares: int | None,
    dealer_hedge_net_shares: int | None,
    official_dealer_net_shares: int | None,
) -> TaiwanInstitutionalInvestorRecord | None:
    if foreign_net_shares is None or investment_trust_net_shares is None:
        return None

    dealer_net_shares = _dealer_net_shares(
        dealer_self_net_shares,
        dealer_hedge_net_shares,
        official_dealer_net_shares,
    )
    if dealer_net_shares is None:
        return None

    total_net_shares = (
        foreign_net_shares
        + investment_trust_net_shares
        + dealer_net_shares
    )
    return TaiwanInstitutionalInvestorRecord(
        date=record_date,
        symbol=symbol,
        name=name,
        foreign_net_shares=foreign_net_shares,
        investment_trust_net_shares=investment_trust_net_shares,
        dealer_self_net_shares=dealer_self_net_shares,
        dealer_hedge_net_shares=dealer_hedge_net_shares,
        dealer_net_shares=dealer_net_shares,
        total_net_shares=total_net_shares,
    )


def _dealer_net_shares(
    dealer_self_net_shares: int | None,
    dealer_hedge_net_shares: int | None,
    official_dealer_net_shares: int | None,
) -> int | None:
    # Prefer the official combined 自營商買賣超 when present; keep split fields for UI.
    if official_dealer_net_shares is not None:
        return official_dealer_net_shares
    if dealer_self_net_shares is not None and dealer_hedge_net_shares is not None:
        return dealer_self_net_shares + dealer_hedge_net_shares
    if dealer_self_net_shares is not None or dealer_hedge_net_shares is not None:
        return (dealer_self_net_shares or 0) + (dealer_hedge_net_shares or 0)
    return None


def _parse_share_int(value: object) -> int | None:
    numeric = parse_taiwan_number(value)
    if numeric is None:
        return None
    return int(numeric)


def _row_at(row: Sequence[object], index: int) -> object | None:
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _source_for_market(market: TaiwanMarket) -> str:
    return _TPEX_SOURCE if market == "TPEx" else _TWSE_SOURCE


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_quote_source() -> QuoteService:
    from app.services.quote_service import quote_service

    return quote_service


tw_institutional_service = TaiwanInstitutionalInvestorsService()
