from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
import logging
import math
from typing import TYPE_CHECKING, Callable, Literal

from app.services.http_client import HttpFetchError
from app.services.taiwan_history_scan import (
    RESULT_CACHE_TTL,
    candidate_start_date,
    collect_dated_history,
    get_cached_or_compute,
)
from app.services.taiwan_market_data import (
    TaiwanMarket,
    fetch_tpex_data,
    fetch_twse_data,
    is_taiwan_no_data_payload,
    normalize_taiwan_date,
    normalize_taiwan_symbol,
    parse_taiwan_number,
    require_taiwan_stock,
    require_twse_or_tpex_symbol,
    shares_to_lots,
    taiwan_field_index,
    taiwan_field_indices,
    taiwan_row_value,
    taiwan_row_value_at,
)

logger = logging.getLogger(__name__)

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
    margin_balance_lots: float | None
    short_margin_balance_lots: float | None
    borrowed_short_balance_lots: float | None
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


SourceStatus = Literal[
    "found",
    "symbol_absent",
    "parse_error",
    "no_data",
    "schema_error",
    "http_error",
]

_SOFT_EMPTY_STATUSES = frozenset({"symbol_absent", "no_data"})


@dataclass(frozen=True)
class _DailyLookupResult:
    record: TaiwanMarginShortRecord | None
    request_succeeded: bool


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


@dataclass(frozen=True)
class _SourceOutcome:
    status: SourceStatus
    point: object | None = None
    reason: str | None = None
    row_count: int | None = None
    field_count: int | None = None


@dataclass(frozen=True)
class _TpexBorrowedShortColumns:
    symbol_index: int
    name_index: int
    previous_balance_index: int
    current_balance_index: int
    used_index_fallback: bool = False


_TPEX_BORROWED_SYMBOL_LABELS = ("股票代號", "證券代號", "代號")
_TPEX_BORROWED_NAME_LABELS = ("股票名稱", "證券名稱", "名稱")
_TPEX_BORROWED_PREV_UNIQUE_LABELS = ("借券賣出前日餘額", "借券前日餘額")
_TPEX_BORROWED_CURR_UNIQUE_LABELS = (
    "借券賣出當日餘額",
    "借券當日餘額",
    "借券賣出餘額",
)
_TPEX_BORROWED_PREV_SHORT_LABELS = ("前日餘額", *_TPEX_BORROWED_PREV_UNIQUE_LABELS)
_TPEX_BORROWED_CURR_SHORT_LABELS = ("當日餘額", *_TPEX_BORROWED_CURR_UNIQUE_LABELS)
_TPEX_BORROWED_SECTION_MARKERS = (
    "當日還券",
    "當日調整數額",
    "次一營業日可借券賣出限額",
    "借券賣出當日還券",
    "借券賣出當日調整數額",
    "借券賣出當日賣出",
)
# Documented fallback for TWSE-aligned TPEx layouts when field labels cannot be resolved.
_TPEX_BORROWED_FALLBACK_PREV_INDEX = 8
_TPEX_BORROWED_FALLBACK_CURR_INDEX = 12


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
        self._result_cache = {}
        self._close_fetch_enabled = True

    def get_margin_short(
        self,
        stock_id: str,
        *,
        days: int = _DEFAULT_HISTORY_DAYS,
    ) -> TaiwanMarginShortResult:
        requested_days = max(days, 1)
        now = self._now_provider()

        def compute() -> TaiwanMarginShortResult:
            stock = require_taiwan_stock(
                stock_id,
                quote_source=self._quote_source,
                not_found_error=_STOCK_NOT_FOUND_ERROR,
                non_taiwan_error=_NON_TW_STOCK_ERROR,
            )
            market, symbol = require_twse_or_tpex_symbol(
                stock,
                quote_source=self._quote_source,
                unknown_market_error=_UNKNOWN_TW_MARKET_ERROR,
            )
            self._close_fetch_enabled = True

            def fetch_day(current_date: date) -> tuple[TaiwanMarginShortRecord | None, bool]:
                lookup = self._fetch_record_for_day(
                    market,
                    current_date,
                    symbol,
                    stock_id=stock.id,
                )
                return lookup.record, lookup.request_succeeded

            history, successful_fetches = collect_dated_history(
                requested_days=requested_days,
                start_date=candidate_start_date(now),
                fetch_day=fetch_day,
                record_date=lambda record: record.date,
            )
            if not history and successful_fetches == 0:
                raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)
            return self._to_result(stock.id, symbol, market, history)

        return get_cached_or_compute(
            self._result_cache,
            (stock_id, requested_days),
            now=now,
            ttl=_RESULT_CACHE_TTL,
            compute=compute,
        )

    def _to_result(
        self,
        stock_id: str,
        symbol: str,
        market: TWBoard,
        history: list[TaiwanMarginShortRecord],
    ) -> TaiwanMarginShortResult:
        ordered = list(reversed(history))
        latest = ordered[-1] if ordered else None
        return TaiwanMarginShortResult(
            stock_id=stock_id,
            symbol=symbol,
            market=market,
            latest_date=latest.date if latest is not None else None,
            summary=latest,
            history=tuple(ordered),
            source=_source_for_market(market),
        )

    def _fetch_record_for_day(
        self,
        market: TWBoard,
        current_date: date,
        symbol: str,
        *,
        stock_id: str,
    ) -> _DailyLookupResult:
        if market == "TPEx":
            return self._fetch_board_record(
                current_date,
                symbol,
                stock_id=stock_id,
                margin_fetcher=self._tpex_margin_fetcher,
                borrowed_fetcher=self._tpex_borrowed_short_fetcher,
                margin_extractor=_extract_tpex_margin_point,
                borrowed_extractor=_extract_tpex_borrowed_short_point,
                close_market="TPEx",
                payload_kind="tpex",
            )
        return self._fetch_board_record(
            current_date,
            symbol,
            stock_id=stock_id,
            margin_fetcher=self._twse_margin_fetcher,
            borrowed_fetcher=self._twse_borrowed_short_fetcher,
            margin_extractor=_extract_twse_margin_point,
            borrowed_extractor=_extract_twse_borrowed_short_point,
            close_market="TWSE",
            payload_kind="twse",
        )

    def _fetch_board_record(
        self,
        current_date: date,
        symbol: str,
        *,
        stock_id: str,
        margin_fetcher: PayloadFetcher,
        borrowed_fetcher: PayloadFetcher,
        margin_extractor: Callable[..., _SourceOutcome],
        borrowed_extractor: Callable[..., _SourceOutcome],
        close_market: TWBoard,
        payload_kind: Literal["twse", "tpex"],
    ) -> _DailyLookupResult:
        margin_outcome = _fetch_and_extract_core_source(
            margin_fetcher,
            current_date,
            payload_kind=payload_kind,
            source_type="margin",
            market=close_market,
            symbol=symbol,
            stock_id=stock_id,
            extractor=margin_extractor,
        )
        borrowed_outcome = _fetch_and_extract_core_source(
            borrowed_fetcher,
            current_date,
            payload_kind=payload_kind,
            source_type="borrowed-short",
            market=close_market,
            symbol=symbol,
            stock_id=stock_id,
            extractor=borrowed_extractor,
        )

        margin_point = (
            margin_outcome.point if isinstance(margin_outcome.point, _MarginPoint) else None
        )
        borrowed_point = (
            borrowed_outcome.point
            if isinstance(borrowed_outcome.point, _BorrowedShortPoint)
            else None
        )
        close_point = self._optional_close_point(
            current_date,
            symbol=symbol,
            market=close_market,
            borrowed_point=borrowed_point,
        )
        record = _merge_daily_points(
            margin_point=margin_point,
            borrowed_point=borrowed_point,
            close_point=close_point,
        )
        completeness = _record_completeness(record)
        request_succeeded = _core_request_succeeded(margin_outcome, borrowed_outcome)
        logger.info(
            "Taiwan margin-short daily merge",
            extra={
                "event": "tw_margin_short.daily_merge",
                "stock_id": stock_id,
                "symbol": symbol,
                "exchange": close_market,
                "requested_date": current_date.isoformat(),
                "margin_status": margin_outcome.status,
                "borrowed_short_status": borrowed_outcome.status,
                "record_completeness": completeness,
                "request_succeeded": request_succeeded,
                "discarded": record is None,
            },
        )
        return _DailyLookupResult(
            record=record,
            request_succeeded=request_succeeded,
        )

    def _optional_close_point(
        self,
        current_date: date,
        *,
        symbol: str,
        market: TWBoard,
        borrowed_point: _BorrowedShortPoint | None,
    ) -> _ClosePoint | None:
        # Close is only useful for borrowed-short amount; skip when unavailable.
        if not self._close_fetch_enabled or borrowed_point is None:
            return None

        close_payload, close_ok = _safe_optional_payload_fetch(
            self._twse_close_fetcher,
            current_date,
        )
        if not close_ok:
            self._close_fetch_enabled = False
            logger.info(
                "Taiwan margin-short close supplement unavailable",
                extra={
                    "event": "tw_margin_short.close_unavailable",
                    "symbol": symbol,
                    "exchange": market,
                    "requested_date": current_date.isoformat(),
                    "source_type": "close",
                },
            )
            return None
        return _extract_close_supplement_point(
            close_payload,
            symbol=symbol,
            market=market,
            current_date=current_date,
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
        params={
            "response": "json",
            "date": current_date.strftime("%Y/%m/%d"),
        },
        operation_name=operation_name,
        resource_id=current_date.isoformat(),
    )


def _fetch_and_extract_core_source(
    fetcher: PayloadFetcher,
    current_date: date,
    *,
    payload_kind: Literal["twse", "tpex"],
    source_type: str,
    market: TWBoard,
    symbol: str,
    stock_id: str,
    extractor: Callable[..., _SourceOutcome],
) -> _SourceOutcome:
    log_base = {
        "stock_id": stock_id,
        "symbol": symbol,
        "exchange": market,
        "requested_date": current_date.isoformat(),
        "source_type": source_type,
    }
    try:
        payload = fetcher(current_date)
    except HttpFetchError as exc:
        logger.warning(
            "Taiwan margin-short upstream HTTP failure",
            extra={
                **log_base,
                "event": "tw_margin_short.fetch_http_error",
                "http_success": False,
                "parser_failure_reason": str(exc),
            },
        )
        return _SourceOutcome(status="http_error", reason=str(exc))

    if is_taiwan_no_data_payload(payload):
        logger.info(
            "Taiwan margin-short upstream returned no-data",
            extra={
                **log_base,
                "event": "tw_margin_short.fetch_no_data",
                "http_success": True,
            },
        )
        return _SourceOutcome(status="no_data", reason="official_no_data")

    if not _has_expected_table_shape(payload, payload_kind=payload_kind):
        logger.warning(
            "Taiwan margin-short unexpected table shape",
            extra={
                **log_base,
                "event": "tw_margin_short.fetch_schema_error",
                "http_success": True,
                "parser_failure_reason": "unexpected_table_shape",
            },
        )
        return _SourceOutcome(status="schema_error", reason="unexpected_table_shape")

    outcome = extractor(payload, symbol=symbol, current_date=current_date)
    logger.info(
        "Taiwan margin-short source parse",
        extra={
            **log_base,
            "event": "tw_margin_short.source_parse",
            "http_success": True,
            "status": outcome.status,
            "row_count": outcome.row_count,
            "field_count": outcome.field_count,
            "matching_symbol_found": outcome.status in {"found", "parse_error"},
            "parser_failure_reason": outcome.reason,
        },
    )
    return outcome


def _safe_optional_payload_fetch(
    fetcher: PayloadFetcher,
    current_date: date,
) -> tuple[object | None, bool]:
    try:
        payload = fetcher(current_date)
    except HttpFetchError:
        return None, False
    if is_taiwan_no_data_payload(payload):
        return payload, True
    if not _has_expected_table_shape(payload, payload_kind="twse"):
        return None, False
    return payload, True


def _has_expected_table_shape(
    payload: object,
    *,
    payload_kind: Literal["twse", "tpex"],
) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload_kind == "tpex":
        return _first_tpex_table(payload) is not None
    return isinstance(payload.get("data"), list) and isinstance(payload.get("fields"), list)


def _core_request_succeeded(
    margin_outcome: _SourceOutcome,
    borrowed_outcome: _SourceOutcome,
) -> bool:
    """Return whether this calendar day counts as a successful upstream probe.

    - Any ``found`` core source succeeds (partial rows are valid).
    - Both sides soft-empty (``no_data`` / ``symbol_absent``) is legitimate empty.
    - Hard failures (``http_error`` / ``schema_error`` / ``parse_error``) without a
      compensating ``found`` must not look like ordinary no-data.
    """
    if margin_outcome.status == "found" or borrowed_outcome.status == "found":
        return True
    return (
        margin_outcome.status in _SOFT_EMPTY_STATUSES
        and borrowed_outcome.status in _SOFT_EMPTY_STATUSES
    )


def _record_completeness(record: TaiwanMarginShortRecord | None) -> str:
    if record is None:
        return "discarded"
    has_margin = record.margin_balance_lots is not None
    has_borrowed = record.borrowed_short_balance_lots is not None
    if has_margin and has_borrowed:
        return "complete"
    if has_margin or has_borrowed:
        return "partial"
    return "discarded"


def _extract_twse_margin_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
) -> _SourceOutcome:
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
) -> _SourceOutcome:
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
) -> _SourceOutcome:
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
) -> _SourceOutcome:
    return _extract_tpex_borrowed_short_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
    )


def _extract_twse_table_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
    parse_row: Callable[..., object | None],
) -> _SourceOutcome:
    if not isinstance(payload, dict):
        return _SourceOutcome(status="schema_error", reason="payload_not_dict")
    rows = payload.get("data")
    fields = payload.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return _SourceOutcome(status="schema_error", reason="missing_data_or_fields")

    record_date = normalize_taiwan_date(payload.get("date")) or current_date.isoformat()
    saw_matching_symbol = False
    for row in rows:
        if not isinstance(row, list):
            continue
        row_symbol = normalize_taiwan_symbol(_row_at(row, 0))
        if row_symbol != symbol:
            continue
        saw_matching_symbol = True
        point = parse_row(row, record_date=record_date)
        if point is not None and getattr(point, "symbol", None) == symbol:
            return _SourceOutcome(
                status="found",
                point=point,
                row_count=len(rows),
                field_count=len(fields),
            )
    if saw_matching_symbol:
        return _SourceOutcome(
            status="parse_error",
            row_count=len(rows),
            field_count=len(fields),
            reason="symbol_row_unparseable",
        )
    return _SourceOutcome(
        status="symbol_absent",
        row_count=len(rows),
        field_count=len(fields),
        reason="symbol_not_in_table",
    )


def _extract_tpex_table_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
    parse_row: Callable[..., object | None],
) -> _SourceOutcome:
    payload_dict = payload if isinstance(payload, dict) else None
    table = _first_tpex_table(payload)
    if table is None:
        return _SourceOutcome(status="schema_error", reason="missing_tpex_table")

    rows = table.get("data")
    fields = table.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return _SourceOutcome(status="schema_error", reason="missing_data_or_fields")

    record_date = (
        normalize_taiwan_date(table.get("date"))
        or normalize_taiwan_date(payload_dict.get("date") if payload_dict is not None else None)
        or current_date.isoformat()
    )
    saw_matching_symbol = False
    for row in rows:
        if not isinstance(row, list):
            continue
        row_symbol = normalize_taiwan_symbol(
            taiwan_row_value(row, fields, "代號", "股票代號", "證券代號")
        )
        if row_symbol != symbol:
            continue
        saw_matching_symbol = True
        point = parse_row(row, fields, record_date=record_date)
        if point is not None and getattr(point, "symbol", None) == symbol:
            return _SourceOutcome(
                status="found",
                point=point,
                row_count=len(rows),
                field_count=len(fields),
            )
    if saw_matching_symbol:
        return _SourceOutcome(
            status="parse_error",
            row_count=len(rows),
            field_count=len(fields),
            reason="symbol_row_unparseable",
        )
    return _SourceOutcome(
        status="symbol_absent",
        row_count=len(rows),
        field_count=len(fields),
        reason="symbol_not_in_table",
    )


def _extract_tpex_borrowed_short_table_point(
    payload: object | None,
    *,
    symbol: str,
    current_date: date,
) -> _SourceOutcome:
    payload_dict = payload if isinstance(payload, dict) else None
    table = _first_tpex_table(payload)
    if table is None:
        return _SourceOutcome(status="schema_error", reason="missing_tpex_table")

    rows = table.get("data")
    fields = table.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return _SourceOutcome(status="schema_error", reason="missing_data_or_fields")

    columns = resolve_tpex_borrowed_short_columns(fields)
    if columns is None:
        return _SourceOutcome(
            status="schema_error",
            reason="unresolved_borrowed_short_columns",
            row_count=len(rows),
            field_count=len(fields),
        )

    record_date = (
        normalize_taiwan_date(table.get("date"))
        or normalize_taiwan_date(payload_dict.get("date") if payload_dict is not None else None)
        or current_date.isoformat()
    )
    saw_matching_symbol = False
    for row in rows:
        if not isinstance(row, list):
            continue
        row_symbol = normalize_taiwan_symbol(
            taiwan_row_value_at(row, columns.symbol_index)
        )
        if row_symbol != symbol:
            continue
        saw_matching_symbol = True
        point = _parse_tpex_borrowed_short_row(
            row,
            fields,
            record_date=record_date,
            columns=columns,
        )
        if point is not None and point.symbol == symbol:
            return _SourceOutcome(
                status="found",
                point=point,
                row_count=len(rows),
                field_count=len(fields),
                reason="index_fallback" if columns.used_index_fallback else None,
            )
    if saw_matching_symbol:
        return _SourceOutcome(
            status="parse_error",
            row_count=len(rows),
            field_count=len(fields),
            reason="symbol_row_unparseable",
        )
    return _SourceOutcome(
        status="symbol_absent",
        row_count=len(rows),
        field_count=len(fields),
        reason="symbol_not_in_table",
    )


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

    outcome = _extract_twse_table_point(
        payload,
        symbol=symbol,
        current_date=current_date,
        parse_row=lambda row, record_date: _parse_close_supplement_row(
            row,
            fields,
            record_date=record_date,
        ),
    )
    point = outcome.point if isinstance(outcome.point, _ClosePoint) else None
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
    return _parse_borrowed_short_row(
        row,
        record_date=record_date,
        symbol_index=0,
        name_index=1,
        previous_balance_index=_TPEX_BORROWED_FALLBACK_PREV_INDEX,
        current_balance_index=_TPEX_BORROWED_FALLBACK_CURR_INDEX,
    )


def resolve_tpex_borrowed_short_columns(
    fields: Sequence[object],
) -> _TpexBorrowedShortColumns | None:
    """Resolve TPEx borrowed-short columns from official field metadata.

    Live TPEx SBL tables often repeat short labels such as ``前日餘額`` /
    ``當日餘額`` for both the margin and borrowed-short sections. Prefer unique
    ``借券賣出*`` labels when present; otherwise choose the occurrence that sits
    in the borrowed-short section (identified by markers such as ``當日還券``).
    Fall back to TWSE-aligned indexes 8/12 only when labels cannot be resolved.
    """
    symbol_index = taiwan_field_index(fields, *_TPEX_BORROWED_SYMBOL_LABELS)
    name_index = taiwan_field_index(fields, *_TPEX_BORROWED_NAME_LABELS)
    if symbol_index is None:
        return None
    if name_index is None:
        name_index = 1

    previous_index = taiwan_field_index(fields, *_TPEX_BORROWED_PREV_UNIQUE_LABELS)
    current_index = taiwan_field_index(fields, *_TPEX_BORROWED_CURR_UNIQUE_LABELS)

    if previous_index is None or current_index is None:
        previous_candidates = taiwan_field_indices(fields, *_TPEX_BORROWED_PREV_SHORT_LABELS)
        current_candidates = taiwan_field_indices(fields, *_TPEX_BORROWED_CURR_SHORT_LABELS)
        marker_indices = taiwan_field_indices(fields, *_TPEX_BORROWED_SECTION_MARKERS)

        marked = _select_borrowed_short_columns_with_markers(
            previous_candidates,
            current_candidates,
            marker_indices,
        )
        if marked is not None:
            previous_index, current_index = marked
        elif len(previous_candidates) >= 2 and len(current_candidates) >= 2:
            # Live TPEx tables put the borrowed-short block last.
            previous_index = previous_candidates[-1]
            current_index = current_candidates[-1]
        elif len(previous_candidates) == 1 and len(current_candidates) == 1:
            previous_index = previous_candidates[0]
            current_index = current_candidates[0]

    if (
        previous_index is not None
        and current_index is not None
        and previous_index != current_index
    ):
        return _TpexBorrowedShortColumns(
            symbol_index=symbol_index,
            name_index=name_index,
            previous_balance_index=previous_index,
            current_balance_index=current_index,
            used_index_fallback=False,
        )

    if len(fields) > _TPEX_BORROWED_FALLBACK_CURR_INDEX:
        return _TpexBorrowedShortColumns(
            symbol_index=symbol_index,
            name_index=name_index,
            previous_balance_index=_TPEX_BORROWED_FALLBACK_PREV_INDEX,
            current_balance_index=_TPEX_BORROWED_FALLBACK_CURR_INDEX,
            used_index_fallback=True,
        )
    return None


def _select_borrowed_short_columns_with_markers(
    previous_candidates: Sequence[int],
    current_candidates: Sequence[int],
    marker_indices: Sequence[int],
) -> tuple[int, int] | None:
    """Pick the prev/curr pair that best wraps borrowed-short section markers."""
    if not marker_indices or not previous_candidates or not current_candidates:
        return None

    best: tuple[tuple[int, int, int], int, int] | None = None
    for previous_index in previous_candidates:
        for current_index in current_candidates:
            if previous_index >= current_index:
                continue
            markers_between = sum(
                1 for marker in marker_indices if previous_index < marker < current_index
            )
            if markers_between <= 0:
                continue
            # Prefer the tightest pair that still wraps section markers, then the
            # later block (borrowed-short is usually after the margin section).
            span = current_index - previous_index
            score = (-span, previous_index, current_index)
            if best is None or score > best[0]:
                best = (score, previous_index, current_index)
    if best is None:
        return None
    return best[1], best[2]


def _parse_tpex_borrowed_short_row(
    row: Sequence[object],
    fields: Sequence[object],
    *,
    record_date: str,
    columns: _TpexBorrowedShortColumns | None = None,
) -> _BorrowedShortPoint | None:
    resolved = columns or resolve_tpex_borrowed_short_columns(fields)
    if resolved is None:
        return None
    return _parse_borrowed_short_row(
        row,
        record_date=record_date,
        symbol_index=resolved.symbol_index,
        name_index=resolved.name_index,
        previous_balance_index=resolved.previous_balance_index,
        current_balance_index=resolved.current_balance_index,
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
    if len(row) <= max(symbol_index, name_index, previous_balance_index, current_balance_index):
        return None
    symbol = normalize_taiwan_symbol(taiwan_row_value_at(row, symbol_index))
    if symbol is None:
        return None
    name = str(taiwan_row_value_at(row, name_index) or "").strip()
    current_balance_shares = _parse_share_int(taiwan_row_value_at(row, current_balance_index))
    previous_balance_shares = _parse_share_int(taiwan_row_value_at(row, previous_balance_index))
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
    if margin_point is None and borrowed_point is None:
        return None

    if margin_point is not None and borrowed_point is not None:
        if margin_point.symbol != borrowed_point.symbol:
            return None
        if margin_point.date != borrowed_point.date:
            return None

    record_date = margin_point.date if margin_point is not None else borrowed_point.date  # type: ignore[union-attr]
    symbol = margin_point.symbol if margin_point is not None else borrowed_point.symbol  # type: ignore[union-attr]
    name = ""
    if margin_point is not None and margin_point.name:
        name = margin_point.name
    elif borrowed_point is not None:
        name = borrowed_point.name

    close_price = (
        close_point.close_price
        if close_point is not None and close_point.date == record_date
        else None
    )

    borrowed_short_balance_lots: float | None = None
    borrowed_short_change_lots: float | None = None
    borrowed_short_change_amount_10k: int | None = None
    if borrowed_point is not None:
        borrowed_short_balance_lots = _lots_from_shares(
            borrowed_point.borrowed_short_balance_shares,
            borrowed_point.symbol,
        )
        if borrowed_short_balance_lots is None:
            # Keep margin-only data when borrowed shares cannot be converted.
            borrowed_point = None
        else:
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

    if margin_point is None and borrowed_point is None:
        return None

    return TaiwanMarginShortRecord(
        date=record_date,
        symbol=symbol,
        name=name,
        close_price=close_price,
        margin_balance_lots=(
            margin_point.margin_balance_lots if margin_point is not None else None
        ),
        short_margin_balance_lots=(
            margin_point.short_margin_balance_lots if margin_point is not None else None
        ),
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


def parse_all_margin_short_records(
    *,
    market: TWBoard,
    current_date: date,
    margin_payload: object | None,
    borrowed_payload: object | None,
    close_payload: object | None,
) -> list[TaiwanMarginShortRecord]:
    """Merge full-market margin + borrowed-short (+ optional close) payloads for one day."""
    if market == "TPEx":
        margin_points = _extract_all_tpex_table_points(
            margin_payload,
            current_date=current_date,
            parse_row=_parse_tpex_margin_row,
        )
        borrowed_points = _extract_all_tpex_borrowed_short_points(
            borrowed_payload,
            current_date=current_date,
        )
    else:
        margin_points = _extract_all_twse_table_points(
            margin_payload,
            current_date=current_date,
            parse_row=_parse_twse_margin_row,
        )
        borrowed_points = _extract_all_twse_table_points(
            borrowed_payload,
            current_date=current_date,
            parse_row=_parse_twse_borrowed_short_row,
        )

    close_by_symbol = {
        point.symbol: point
        for point in _extract_all_close_supplement_points(
            close_payload,
            market=market,
            current_date=current_date,
        )
    }
    margin_by_symbol = {point.symbol: point for point in margin_points}
    borrowed_by_symbol = {point.symbol: point for point in borrowed_points}
    symbols = sorted(set(margin_by_symbol) | set(borrowed_by_symbol))
    records: list[TaiwanMarginShortRecord] = []
    for symbol in symbols:
        record = _merge_daily_points(
            margin_point=margin_by_symbol.get(symbol),
            borrowed_point=borrowed_by_symbol.get(symbol),
            close_point=close_by_symbol.get(symbol),
        )
        if record is not None:
            records.append(record)
    return records


def _extract_all_tpex_borrowed_short_points(
    payload: object | None,
    *,
    current_date: date,
) -> list[_BorrowedShortPoint]:
    payload_dict = payload if isinstance(payload, dict) else None
    table = _first_tpex_table(payload)
    if table is None:
        return []
    rows = table.get("data")
    fields = table.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return []
    columns = resolve_tpex_borrowed_short_columns(fields)
    if columns is None:
        return []
    record_date = (
        normalize_taiwan_date(table.get("date"))
        or normalize_taiwan_date(payload_dict.get("date") if payload_dict is not None else None)
        or current_date.isoformat()
    )
    points: list[_BorrowedShortPoint] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        point = _parse_tpex_borrowed_short_row(
            row,
            fields,
            record_date=record_date,
            columns=columns,
        )
        if point is not None:
            points.append(point)
    return points


def _extract_all_twse_table_points(
    payload: object | None,
    *,
    current_date: date,
    parse_row: Callable[..., object | None],
) -> list:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    fields = payload.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return []
    record_date = normalize_taiwan_date(payload.get("date")) or current_date.isoformat()
    points = []
    for row in rows:
        if not isinstance(row, list):
            continue
        point = parse_row(row, record_date=record_date)
        if point is not None:
            points.append(point)
    return points


def _extract_all_tpex_table_points(
    payload: object | None,
    *,
    current_date: date,
    parse_row: Callable[..., object | None],
) -> list:
    payload_dict = payload if isinstance(payload, dict) else None
    table = _first_tpex_table(payload)
    if table is None:
        return []
    rows = table.get("data")
    fields = table.get("fields")
    if not isinstance(rows, list) or not isinstance(fields, list):
        return []
    record_date = (
        normalize_taiwan_date(table.get("date"))
        or normalize_taiwan_date(payload_dict.get("date") if payload_dict is not None else None)
        or current_date.isoformat()
    )
    points = []
    for row in rows:
        if not isinstance(row, list):
            continue
        point = parse_row(row, fields, record_date=record_date)
        if point is not None:
            points.append(point)
    return points


def _extract_all_close_supplement_points(
    payload: object | None,
    *,
    market: TWBoard,
    current_date: date,
) -> list[_ClosePoint]:
    if not isinstance(payload, dict):
        return []
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return []
    points = _extract_all_twse_table_points(
        payload,
        current_date=current_date,
        parse_row=lambda row, record_date: _parse_close_supplement_row(
            row,
            fields,
            record_date=record_date,
        ),
    )
    return [point for point in points if point.market == market]


def margin_short_source_for_market(market: TaiwanMarket) -> str:
    return _source_for_market(market)


def _source_for_market(market: TaiwanMarket) -> str:
    return _TPEX_SOURCE if market == "TPEx" else _TWSE_SOURCE


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_quote_source() -> QuoteService:
    from app.services.quote_service import quote_service

    return quote_service


tw_margin_short_service = TaiwanMarginShortService()
