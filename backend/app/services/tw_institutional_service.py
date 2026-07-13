from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from operator import attrgetter
from typing import TYPE_CHECKING, Callable, Literal, Protocol

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
    taiwan_field_index,
    taiwan_row_value,
)

if TYPE_CHECKING:
    from app.db.models import TaiwanInstitutionalDaily
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

# Read-path policy (#105): serve from taiwan_institutional_daily for normal
# detail-page requests. When the store is empty/unavailable/stale, probe only the
# scheduler's recent weekday window live — never the historical multi-month walk.
# Freshness + live probe size both follow TAIWAN_MARKET_DATA_REFRESH_RECENT_DAYS.
_PERSISTED_SOURCE_PREFIX = "Persisted "

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


class InstitutionalDailyStore(Protocol):
    def is_available(self) -> bool: ...

    def list_history(
        self,
        *,
        market: str,
        symbol: str,
        limit: int,
    ) -> list[TaiwanInstitutionalDaily]: ...

    def upsert_records(
        self,
        *,
        market: str,
        source: str,
        records: list[TaiwanInstitutionalInvestorRecord],
    ) -> object: ...


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


class TaiwanInstitutionalInvestorsService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        twse_fetcher: PayloadFetcher | None = None,
        tpex_fetcher: PayloadFetcher | None = None,
        now_provider: NowProvider | None = None,
        store: InstitutionalDailyStore | None = None,
        use_default_store: bool = True,
    ) -> None:
        self._quote_source = quote_source or _default_quote_source()
        self._twse_fetcher = twse_fetcher or _fetch_twse_payload
        self._tpex_fetcher = tpex_fetcher or _fetch_tpex_payload
        self._now_provider = now_provider or _utc_now
        self._store = store
        self._use_default_store = use_default_store and store is None
        self._result_cache = {}

    def get_institutional_investors(
        self,
        stock_id: str,
        *,
        days: int = _DEFAULT_HISTORY_DAYS,
    ) -> TaiwanInstitutionalInvestorsResult:
        requested_days = max(days, 1)
        now = self._now_provider()

        def compute() -> TaiwanInstitutionalInvestorsResult:
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
            start_date = candidate_start_date(now)
            stored = self._load_stored_history(
                market=market,
                symbol=symbol,
                requested_days=requested_days,
            )
            live_history: list[TaiwanInstitutionalInvestorRecord] = []
            successful_fetches = 0
            served_from_store_only = False

            if _stored_history_is_fresh(stored, start_date=start_date):
                history = stored
                served_from_store_only = True
            else:
                live_history, successful_fetches = self._collect_live_fallback(
                    market=market,
                    symbol=symbol,
                    requested_days=requested_days,
                    start_date=start_date,
                )
                if live_history:
                    self._persist_live_fallback(market=market, records=live_history)
                history = _merge_history(stored, live_history, requested_days=requested_days)
                if not history and successful_fetches == 0 and not stored:
                    raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)
                # Official source only when live rows contributed; otherwise keep
                # persisted attribution for stale-store / empty-live responses.
                served_from_store_only = not live_history

            latest = history[-1] if history else None
            return TaiwanInstitutionalInvestorsResult(
                stock_id=stock.id,
                symbol=symbol,
                market=market,
                latest_date=latest.date if latest is not None else None,
                summary=latest,
                history=tuple(history),
                source=_response_source(market, served_from_store_only=served_from_store_only),
            )

        return get_cached_or_compute(
            self._result_cache,
            (stock_id, requested_days),
            now=now,
            ttl=_RESULT_CACHE_TTL,
            compute=compute,
        )

    def _resolve_store(self) -> InstitutionalDailyStore | None:
        if self._store is not None:
            return self._store
        if not self._use_default_store:
            return None
        from app.repositories.taiwan_market_daily_repository import (
            taiwan_institutional_daily_repository,
        )

        return taiwan_institutional_daily_repository

    def _load_stored_history(
        self,
        *,
        market: TWBoard,
        symbol: str,
        requested_days: int,
    ) -> list[TaiwanInstitutionalInvestorRecord]:
        store = self._resolve_store()
        if store is None or not store.is_available():
            return []
        try:
            rows = store.list_history(
                market=market,
                symbol=symbol,
                limit=requested_days,
            )
        except Exception:  # noqa: BLE001 - store outages fall back to bounded live
            # Store outages must not block the detail page; fall back to bounded live.
            return []
        # Repository returns newest-first; API history is chronological ascending.
        records = [_record_from_stored_row(row) for row in rows]
        records.reverse()
        return _dedupe_history_by_date(records)

    def _persist_live_fallback(
        self,
        *,
        market: TWBoard,
        records: Sequence[TaiwanInstitutionalInvestorRecord],
    ) -> None:
        """Best-effort heal of the store from bounded live rows (never fails the read)."""
        if not records:
            return
        store = self._resolve_store()
        if store is None or not store.is_available():
            return
        try:
            store.upsert_records(
                market=market,
                source=_source_for_market(market),
                records=list(records),
            )
        except Exception:  # noqa: BLE001 - store healing must not block detail reads
            return

    def _collect_live_fallback(
        self,
        *,
        market: TWBoard,
        symbol: str,
        requested_days: int,
        start_date: date,
    ) -> tuple[list[TaiwanInstitutionalInvestorRecord], int]:
        history, successful_fetches = collect_dated_history(
            requested_days=requested_days,
            start_date=start_date,
            fetch_day=lambda day: self._history_day_lookup(market, day, symbol),
            record_date=attrgetter("date"),
            window_days=_read_path_window_days(),
        )
        history.reverse()
        return history, successful_fetches

    def _history_day_lookup(
        self,
        market: TWBoard,
        day: date,
        symbol: str,
    ) -> tuple[TaiwanInstitutionalInvestorRecord | None, bool]:
        lookup = self._fetch_record_for_day(market, day, symbol)
        return lookup.record, lookup.request_succeeded

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


def parse_all_institutional_records(
    payload: object,
    *,
    market: TWBoard,
    current_date: date,
) -> list[TaiwanInstitutionalInvestorRecord]:
    """Parse every usable row from a full-market institutional daily payload."""
    if is_taiwan_no_data_payload(payload) or not isinstance(payload, dict):
        return []
    if market == "TWSE":
        return _parse_all_twse_institutional_records(payload, current_date=current_date)
    return _parse_all_tpex_institutional_records(payload, current_date=current_date)


def _parse_rows_with_fields(
    rows: object,
    fields: object,
    *,
    record_date: str,
    parse_row,
) -> list[TaiwanInstitutionalInvestorRecord]:
    if not isinstance(rows, list) or not isinstance(fields, list):
        return []
    parsed_rows: list[TaiwanInstitutionalInvestorRecord] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        record = parse_row(row, fields, record_date=record_date)
        if record is not None:
            parsed_rows.append(record)
    return parsed_rows


def _parse_all_twse_institutional_records(
    payload: dict,
    *,
    current_date: date,
) -> list[TaiwanInstitutionalInvestorRecord]:
    record_date = normalize_taiwan_date(payload.get("date")) or current_date.isoformat()
    return _parse_rows_with_fields(
        payload.get("data"),
        payload.get("fields"),
        record_date=record_date,
        parse_row=_parse_twse_row,
    )


def _parse_all_tpex_institutional_records(
    payload: dict,
    *,
    current_date: date,
) -> list[TaiwanInstitutionalInvestorRecord]:
    tables = payload.get("tables")
    if not isinstance(tables, list):
        return []
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
        return []
    record_date = (
        normalize_taiwan_date(table.get("date"))
        or normalize_taiwan_date(payload.get("date"))
        or current_date.isoformat()
    )
    return _parse_rows_with_fields(
        table.get("data"),
        table.get("fields"),
        record_date=record_date,
        parse_row=_parse_tpex_row,
    )


def fetch_institutional_market_payload(market: TWBoard, current_date: date) -> object:
    if market == "TPEx":
        return _fetch_tpex_payload(current_date)
    return _fetch_twse_payload(current_date)


def institutional_source_for_market(market: TWBoard) -> str:
    return _source_for_market(market)


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


def _response_source(market: TaiwanMarket, *, served_from_store_only: bool) -> str:
    base = _source_for_market(market)
    if served_from_store_only:
        return f"{_PERSISTED_SOURCE_PREFIX}{base}"
    return base


def _read_path_window_days() -> int:
    """Freshness + live probe window; aligned with the #97 recent refresh window."""
    from app.core.config import settings

    return max(settings.taiwan_market_data_refresh_recent_days, 1)


def _record_from_stored_row(row: TaiwanInstitutionalDaily) -> TaiwanInstitutionalInvestorRecord:
    return TaiwanInstitutionalInvestorRecord(
        date=row.date.isoformat(),
        symbol=row.symbol,
        name=row.name or "",
        foreign_net_shares=row.foreign_net_shares,
        investment_trust_net_shares=row.investment_trust_net_shares,
        dealer_self_net_shares=row.dealer_self_net_shares,
        dealer_hedge_net_shares=row.dealer_hedge_net_shares,
        dealer_net_shares=row.dealer_net_shares,
        total_net_shares=row.total_net_shares,
    )


def _stored_history_is_fresh(
    history: Sequence[TaiwanInstitutionalInvestorRecord],
    *,
    start_date: date,
    max_age_days: int | None = None,
) -> bool:
    if not history:
        return False
    try:
        latest_date = date.fromisoformat(history[-1].date)
    except ValueError:
        return False
    age_days = max_age_days if max_age_days is not None else _read_path_window_days()
    return latest_date >= start_date - timedelta(days=age_days)


def _dedupe_history_by_date(
    history: Sequence[TaiwanInstitutionalInvestorRecord],
) -> list[TaiwanInstitutionalInvestorRecord]:
    """Keep chronological order with unique trading dates (last write wins)."""
    by_date: dict[str, TaiwanInstitutionalInvestorRecord] = {}
    for record in history:
        by_date[record.date] = record
    return [by_date[key] for key in sorted(by_date)]


def _merge_history(
    stored: Sequence[TaiwanInstitutionalInvestorRecord],
    live: Sequence[TaiwanInstitutionalInvestorRecord],
    *,
    requested_days: int,
) -> list[TaiwanInstitutionalInvestorRecord]:
    """Merge store + bounded live rows; live wins on the same trading date."""
    by_date: dict[str, TaiwanInstitutionalInvestorRecord] = {
        record.date: record for record in stored
    }
    for record in live:
        by_date[record.date] = record
    merged = [by_date[key] for key in sorted(by_date)]
    if len(merged) <= requested_days:
        return merged
    return merged[-requested_days:]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_quote_source() -> QuoteService:
    from app.services.quote_service import quote_service

    return quote_service


tw_institutional_service = TaiwanInstitutionalInvestorsService()
