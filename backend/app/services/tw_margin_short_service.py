from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import math
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
    shares_to_lots,
    taiwan_row_value,
)

if TYPE_CHECKING:
    from app.services.quote_service import QuoteService

_DEFAULT_HISTORY_DAYS = 90
_RESULT_CACHE_TTL = RESULT_CACHE_TTL

_TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
_TWSE_BORROWED_SHORT_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U"
_TWSE_CLOSE_SUPPLEMENT_URL = "https://www.twse.com.tw/rwd/zh/lending/TWT72U"
_TWSE_CLOSE_SUPPLEMENT_SELECT_TYPE = "SLBNLB"

_TPEX_MARGIN_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
_TPEX_BORROWED_SHORT_URL = "https://www.tpex.org.tw/www/zh-tw/margin/sbl"

_TWSE_SOURCE = "TWSE official margin and borrowed-short daily data"
_TPEX_SOURCE = "TPEx official margin and borrowed-short daily data"

_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan margin-short data is only available for TW stocks"
_UNKNOWN_TW_MARKET_ERROR = "Taiwan margin-short market is unavailable for this stock"
_UPSTREAM_UNAVAILABLE_ERROR = (
    "Taiwan margin-short data is temporarily unavailable from official sources."
)

TWBoard = Literal["TWSE", "TPEx"]
PayloadFetcher = Callable[[date], object]
NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class TaiwanMarginShortRecord:
    date: str
    symbol: str
    name: str
    close_price: float | None
    margin_balance_lots: float
    short_margin_balance_lots: float
    borrowed_short_balance_lots: float
    borrowed_short_balance_change_lots: float | None
    borrowed_short_balance_change_amount_10k: int | None


@dataclass(frozen=True)
class TaiwanMarginShortResult:
    stock_id: str
    symbol: str
    market: TWBoard
    latest_date: str | None
    summary: TaiwanMarginShortRecord | None
    history: tuple[TaiwanMarginShortRecord, ...]
    source: str


@dataclass(frozen=True)
class _DailyLookupResult:
    record: TaiwanMarginShortRecord | None
    request_succeeded: bool


@dataclass(frozen=True)
class _ResultCacheEntry:
    cached_at: datetime
    result: TaiwanMarginShortResult


@dataclass(frozen=True)
class _MarginPoint:
    date: str
    symbol: str
    name: str
    margin_balance_lots: float
    short_margin_balance_lots: float


@dataclass(frozen=True)
class _BorrowedShortPoint:
    date: str
    symbol: str
    name: str
    borrowed_short_balance_shares: int
    previous_borrowed_short_balance_shares: int | None


@dataclass(frozen=True)
class _ClosePoint:
    date: str
    symbol: str
    market: TWBoard
    close_price: float | None


class TaiwanMarginShortService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        twse_margin_fetcher: PayloadFetcher | None = None,
        twse_borrowed_short_fetcher: PayloadFetcher | None = None,
        twse_close_fetcher: PayloadFetcher | None = None,
        tpex_margin_fetcher: PayloadFetcher | None = None,
        tpex_borrowed_short_fetcher: PayloadFetcher | None = None,
        now_provider: NowProvider | None = None,
    ) -> None:
        self._quote_source = quote_source or _default_quote_source()
        self._twse_margin_fetcher = twse_margin_fetcher or _fetch_twse_margin_payload
        self._twse_borrowed_short_fetcher = (
            twse_borrowed_short_fetcher or _fetch_twse_borrowed_short_payload
        )
        self._twse_close_fetcher = twse_close_fetcher or _fetch_twse_close_payload
        self._tpex_margin_fetcher = tpex_margin_fetcher or _fetch_tpex_margin_payload
        self._tpex_borrowed_short_fetcher = (
            tpex_borrowed_short_fetcher or _fetch_tpex_borrowed_short_payload
        )
        self._now_provider = now_provider or _utc_now
        self._result_cache: dict[tuple[str, int], _ResultCacheEntry] = {}

    def get_margin_short(
        self,
        stock_id: str,
        *,
        days: int = _DEFAULT_HISTORY_DAYS,
    ) -> TaiwanMarginShortResult:
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
        result = TaiwanMarginShortResult(
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
    ) -> tuple[list[TaiwanMarginShortRecord], int]:
        def fetch_day(current_date: date) -> tuple[TaiwanMarginShortRecord | None, bool]:
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
            return self._fetch_board_record(
                current_date,
                symbol,
                margin_fetcher=self._tpex_margin_fetcher,
                borrowed_fetcher=self._tpex_borrowed_short_fetcher,
                margin_extractor=_extract_tpex_margin_point,
                borrowed_extractor=_extract_tpex_borrowed_short_point,
                close_market="TPEx",
            )
        return self._fetch_board_record(
            current_date,
            symbol,
            margin_fetcher=self._twse_margin_fetcher,
            borrowed_fetcher=self._twse_borrowed_short_fetcher,
            margin_extractor=_extract_twse_margin_point,
            borrowed_extractor=_extract_twse_borrowed_short_point,
            close_market="TWSE",
        )

    def _fetch_board_record(
        self,
        current_date: date,
        symbol: str,
        *,
        margin_fetcher: PayloadFetcher,
        borrowed_fetcher: PayloadFetcher,
        margin_extractor: Callable[..., _MarginPoint | None],
        borrowed_extractor: Callable[..., _BorrowedShortPoint | None],
        close_market: TWBoard,
    ) -> _DailyLookupResult:
        margin_payload, margin_succeeded = _safe_payload_fetch(margin_fetcher, current_date)
        borrowed_payload, borrowed_succeeded = _safe_payload_fetch(borrowed_fetcher, current_date)
        close_payload, _ = _safe_payload_fetch(self._twse_close_fetcher, current_date)

        record = _merge_daily_points(
            margin_point=margin_extractor(
                margin_payload,
                symbol=symbol,
                current_date=current_date,
            ),
            borrowed_point=borrowed_extractor(
                borrowed_payload,
                symbol=symbol,
                current_date=current_date,
            ),
            close_point=_extract_close_supplement_point(
                close_payload,
                symbol=symbol,
                market=close_market,
                current_date=current_date,
            ),
        )
        return _DailyLookupResult(
            record=record,
            # The close supplement is optional and must not hide core upstream failures.
            request_succeeded=margin_succeeded and borrowed_succeeded,
        )


def _fetch_twse_margin_payload(current_date: date) -> object:
    return _fetch_twse_dated_payload(
        _TWSE_MARGIN_URL,
        current_date,
        operation_name="tw_margin_short_twse_margin_daily",
        select_type="ALL",
    )


def _fetch_twse_borrowed_short_payload(current_date: date) -> object:
    return _fetch_twse_dated_payload(
        _TWSE_BORROWED_SHORT_URL,
        current_date,
        operation_name="tw_margin_short_twse_borrowed_short_daily",
    )


def _fetch_twse_close_payload(current_date: date) -> object:
    return _fetch_twse_dated_payload(
        _TWSE_CLOSE_SUPPLEMENT_URL,
        current_date,
        operation_name="tw_margin_short_twse_close_daily",
        select_type=_TWSE_CLOSE_SUPPLEMENT_SELECT_TYPE,
    )


def _fetch_tpex_margin_payload(current_date: date) -> object:
    return _fetch_tpex_dated_payload(
        _TPEX_MARGIN_URL,
        current_date,
        operation_name="tw_margin_short_tpex_margin_daily",
    )


def _fetch_tpex_borrowed_short_payload(current_date: date) -> object:
    return _fetch_tpex_dated_payload(
        _TPEX_BORROWED_SHORT_URL,
        current_date,
        operation_name="tw_margin_short_tpex_borrowed_short_daily",
    )


def _fetch_twse_dated_payload(
    url: str,
    current_date: date,
    *,
    operation_name: str,
    select_type: str | None = None,
) -> object:
    params: dict[str, str] = {
        "response": "json",
        "date": current_date.strftime("%Y%m%d"),
    }
    if select_type is not None:
        params["selectType"] = select_type
    return fetch_twse_data(
        url,
        params=params,
        operation_name=operation_name,
        resource_id=current_date.isoformat(),
    )


def _fetch_tpex_dated_payload(
    url: str,
    current_date: date,
    *,
    operation_name: str,
) -> object:
    return fetch_tpex_data(
        url,
        params={"date": current_date.strftime("%Y/%m/%d")},
        operation_name=operation_name,
        resource_id=current_date.isoformat(),
    )


def _safe_payload_fetch(
    fetcher: PayloadFetcher,
    current_date: date,
) -> tuple[object | None, bool]:
    try:
        payload = fetcher(current_date)
    except HttpFetchError:
        return None, False
    if is_taiwan_no_data_payload(payload):
        return payload, True
    if not isinstance(payload, dict):
        return None, False
    return payload, True


def _extract_twse_margin_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
) -> _MarginPoint | None:
    return _extract_twse_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
        parse_row=_parse_twse_margin_row,
    )


def _extract_twse_borrowed_short_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
) -> _BorrowedShortPoint | None:
    return _extract_twse_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
        parse_row=_parse_twse_borrowed_short_row,
    )


def _extract_tpex_margin_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
) -> _MarginPoint | None:
    return _extract_tpex_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
        parse_row=_parse_tpex_margin_row,
    )


def _extract_tpex_borrowed_short_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
) -> _BorrowedShortPoint | None:
    return _extract_tpex_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
        parse_row=lambda row, fields, record_date: _parse_tpex_borrowed_short_row(
            row,
            record_date=record_date,
        ),
    )


def _extract_twse_table_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
    parse_row: Callable[..., object | None],
):
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    fields = payload.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return None

    record_date = normalize_taiwan_date(payload.get("date")) or current_date.isoformat()
    for row in rows:
        if not isinstance(row, list):
            continue
        point = parse_row(row, record_date=record_date)
        if point is not None and getattr(point, "symbol", None) == symbol:
            return point
    return None


def _extract_tpex_table_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
    parse_row: Callable[..., object | None],
):
    payload_dict = payload if isinstance(payload, dict) else None
    table = _first_tpex_table(payload)
    if table is None:
        return None

    rows = table.get("data")
    fields = table.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return None

    record_date = (
        normalize_taiwan_date(table.get("date"))
        or normalize_taiwan_date(payload_dict.get("date") if payload_dict is not None else None)
        or current_date.isoformat()
    )
    for row in rows:
        if not isinstance(row, list):
            continue
        point = parse_row(row, fields, record_date=record_date)
        if point is not None and getattr(point, "symbol", None) == symbol:
            return point
    return None


def _extract_close_supplement_point(
    payload: object | None,
    *,
    symbol: str,
    market: TWBoard,
    current_date: date,
) -> _ClosePoint | None:
    if not isinstance(payload, dict):
        return None
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return None

    point = _extract_twse_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
        parse_row=lambda row, record_date: _parse_close_supplement_row(
            row,
            fields,
            record_date=record_date,
        ),
    )
    if point is None or point.market != market:
        return None
    return point


def _first_tpex_table(payload: object | None) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return None
    for table in tables:
        if (
            isinstance(table, dict)
            and isinstance(table.get("fields"), list)
            and isinstance(table.get("data"), list)
        ):
            return table
    return None


def _parse_twse_margin_row(
    row: Sequence[object],
    *,
    record_date: str,
) -> _MarginPoint | None:
    if len(row) <= 12:
        return None
    symbol = normalize_taiwan_symbol(_row_at(row, 0))
    if symbol is None:
        return None
    return _build_margin_point(
        record_date=record_date,
        symbol=symbol,
        name=str(_row_at(row, 1) or "").strip(),
        margin_balance_lots=_parse_lots_float(_row_at(row, 6)),
        short_margin_balance_lots=_parse_lots_float(_row_at(row, 12)),
    )


def _parse_tpex_margin_row(
    row: Sequence[object],
    fields: Sequence[object],
    *,
    record_date: str,
) -> _MarginPoint | None:
    symbol = normalize_taiwan_symbol(taiwan_row_value(row, fields, "代號"))
    if symbol is None:
        return None
    return _build_margin_point(
        record_date=record_date,
        symbol=symbol,
        name=str(taiwan_row_value(row, fields, "名稱") or "").strip(),
        margin_balance_lots=_parse_lots_float(taiwan_row_value(row, fields, "資餘額")),
        short_margin_balance_lots=_parse_lots_float(taiwan_row_value(row, fields, "券餘額")),
    )


def _build_margin_point(
    *,
    record_date: str,
    symbol: str,
    name: str,
    margin_balance_lots: float | None,
    short_margin_balance_lots: float | None,
) -> _MarginPoint | None:
    if margin_balance_lots is None or short_margin_balance_lots is None:
        return None
    return _MarginPoint(
        date=record_date,
        symbol=symbol,
        name=name,
        margin_balance_lots=margin_balance_lots,
        short_margin_balance_lots=short_margin_balance_lots,
    )


def _parse_twse_borrowed_short_row(
    row: Sequence[object],
    *,
    record_date: str,
) -> _BorrowedShortPoint | None:
    return _parse_borrowed_short_balance_row(row, record_date=record_date)


def _parse_tpex_borrowed_short_row(
    row: Sequence[object],
    *,
    record_date: str,
) -> _BorrowedShortPoint | None:
    # TPEx SBL rows currently share TWSE column indices for previous/current balances.
    return _parse_borrowed_short_balance_row(row, record_date=record_date)


def _parse_borrowed_short_balance_row(
    row: Sequence[object],
    *,
    record_date: str,
) -> _BorrowedShortPoint | None:
    return _parse_borrowed_short_row(
        row,
        record_date=record_date,
        symbol_index=0,
        name_index=1,
        previous_balance_index=8,
        current_balance_index=12,
    )


def _parse_borrowed_short_row(
    row: Sequence[object],
    *,
    record_date: str,
    symbol_index: int,
    name_index: int,
    previous_balance_index: int,
    current_balance_index: int,
) -> _BorrowedShortPoint | None:
    if len(row) <= current_balance_index:
        return None
    symbol = normalize_taiwan_symbol(_row_at(row, symbol_index))
    if symbol is None:
        return None
    name = str(_row_at(row, name_index) or "").strip()
    current_balance_shares = _parse_share_int(_row_at(row, current_balance_index))
    previous_balance_shares = _parse_share_int(_row_at(row, previous_balance_index))
    if current_balance_shares is None:
        return None
    return _BorrowedShortPoint(
        date=record_date,
        symbol=symbol,
        name=name,
        borrowed_short_balance_shares=current_balance_shares,
        previous_borrowed_short_balance_shares=previous_balance_shares,
    )


def _parse_close_supplement_row(
    row: Sequence[object],
    fields: Sequence[object],
    *,
    record_date: str,
) -> _ClosePoint | None:
    symbol = normalize_taiwan_symbol(taiwan_row_value(row, fields, "證券代號"))
    market = _close_supplement_market(taiwan_row_value(row, fields, "市場別"))
    if symbol is None or market is None:
        return None
    close_price = _parse_price_float(taiwan_row_value(row, fields, "本日收盤價(5)單位：元"))
    return _ClosePoint(
        date=record_date,
        symbol=symbol,
        market=market,
        close_price=close_price,
    )


def _merge_daily_points(
    *,
    margin_point: _MarginPoint | None,
    borrowed_point: _BorrowedShortPoint | None,
    close_point: _ClosePoint | None,
) -> TaiwanMarginShortRecord | None:
    if margin_point is None or borrowed_point is None:
        return None
    if margin_point.symbol != borrowed_point.symbol:
        return None
    if margin_point.date != borrowed_point.date:
        return None

    close_price = (
        close_point.close_price
        if close_point is not None and close_point.date == margin_point.date
        else None
    )
    borrowed_short_balance_lots = _lots_from_shares(
        borrowed_point.borrowed_short_balance_shares,
        borrowed_point.symbol,
    )
    if borrowed_short_balance_lots is None:
        return None

    borrowed_short_change_lots = _borrowed_short_change_lots(
        borrowed_point.borrowed_short_balance_shares,
        borrowed_point.previous_borrowed_short_balance_shares,
        borrowed_point.symbol,
    )
    borrowed_short_change_amount_10k = _borrowed_short_change_amount_10k(
        borrowed_point.borrowed_short_balance_shares,
        borrowed_point.previous_borrowed_short_balance_shares,
        close_price,
    )

    return TaiwanMarginShortRecord(
        date=margin_point.date,
        symbol=margin_point.symbol,
        name=margin_point.name or borrowed_point.name,
        close_price=close_price,
        margin_balance_lots=margin_point.margin_balance_lots,
        short_margin_balance_lots=margin_point.short_margin_balance_lots,
        borrowed_short_balance_lots=borrowed_short_balance_lots,
        borrowed_short_balance_change_lots=borrowed_short_change_lots,
        borrowed_short_balance_change_amount_10k=borrowed_short_change_amount_10k,
    )


def _borrowed_short_change_lots(
    current_balance_shares: int,
    previous_balance_shares: int | None,
    symbol: str,
) -> float | None:
    if previous_balance_shares is None:
        return None
    return _lots_from_shares(current_balance_shares - previous_balance_shares, symbol)


def _borrowed_short_change_amount_10k(
    current_balance_shares: int,
    previous_balance_shares: int | None,
    close_price: float | None,
) -> int | None:
    if previous_balance_shares is None or close_price is None:
        return None
    change_shares = current_balance_shares - previous_balance_shares
    amount_10k = (change_shares * close_price) / 10_000.0
    return _round_nearest_int(amount_10k)


def _round_nearest_int(value: float) -> int:
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def _close_supplement_market(value: object) -> TWBoard | None:
    normalized = str(value or "").strip()
    if normalized in {"集中市場", "上市", "TWSE"}:
        return "TWSE"
    if normalized in {"櫃檯買賣中心", "上櫃市場", "TPEx", "OTC"}:
        return "TPEx"
    return None


def _lots_from_shares(value: int, symbol: str) -> float | None:
    lots = shares_to_lots(value, symbol=symbol)
    if lots is None:
        return None
    return round(lots, 3)


def _parse_lots_float(value: object) -> float | None:
    numeric = parse_taiwan_number(value)
    if numeric is None:
        return None
    return round(float(numeric), 3)


def _parse_price_float(value: object) -> float | None:
    numeric = parse_taiwan_number(value)
    if numeric is None:
        return None
    return float(numeric)


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


tw_margin_short_service = TaiwanMarginShortService()
