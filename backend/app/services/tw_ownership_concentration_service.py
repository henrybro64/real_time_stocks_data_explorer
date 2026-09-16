"""Taiwan ownership concentration (籌碼集中度) from TDCC + foreign ownership %.

TDCC open-data ``id=1-5`` is a weekly overwrite snapshot only. Multi-week
``400張以上`` history comes from the official TDCC portal qryStock pages
(about one year of Fridays), not from WantGoo.

Foreign ownership uses TWSE ``MI_QFIIS`` or TPEx ``insti/qfii`` by board.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from app.models.quote import CandleRecord
from app.services.http_client import fetch_text
from app.services.quote_service import QuoteService, quote_service
from app.services.taiwan_history_scan import candidate_start_date
from app.services.taiwan_market_data import (
    fetch_tpex_data,
    fetch_twse_data,
    is_taiwan_no_data_payload,
    normalize_taiwan_date,
    parse_taiwan_number,
    require_taiwan_stock,
    require_twse_or_tpex_symbol,
    taiwan_field_index,
    taiwan_row_value,
)
from app.services.tdcc_shareholding import (
    TdccConcentrationPoint,
    extract_tdcc_available_dates,
    extract_tdcc_synchronizer_token,
    parse_tdcc_qry_stock_html,
    parse_tdcc_shareholding_csv,
)

OwnershipRange = Literal["3M", "6M", "1Y"]
TaiwanBoard = Literal["TWSE", "TPEx"]

logger = logging.getLogger(__name__)

_RESULT_CACHE_TTL = timedelta(hours=12)
_STALE_REFRESH_BACKOFF = timedelta(minutes=15)
# Fridays only; ~26 covers 6M weekly or 1Y biweekly-ish. Prefer newest TDCC dates.
_MAX_FOREIGN_PROBES = 26
_MAX_TDCC_WEEKS = 55
_TDCC_OPEN_DATA_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx"
_TDCC_PORTAL_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
_TWSE_MI_QFIIS_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
_TPEX_QFII_URL = "https://www.tpex.org.tw/www/zh-tw/insti/qfii"
_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan ownership concentration is only available for TW stocks"
_UNKNOWN_TW_MARKET_ERROR = "Taiwan ownership market is unavailable for this stock"
_UPSTREAM_UNAVAILABLE_ERROR = "Taiwan ownership concentration upstream temporarily unavailable"
_SOURCE_BY_BOARD: dict[TaiwanBoard, str] = {
    "TWSE": "tdcc_portal+twse_mi_qfiis",
    "TPEx": "tdcc_portal+tpex_qfii",
}
_RANGE_DAYS: dict[OwnershipRange, int] = {
    "3M": 93,
    "6M": 186,
    "1Y": 372,
}
_HISTORY_RANGE_BY_OWNERSHIP: dict[OwnershipRange, str] = {
    "3M": "3M",
    "6M": "6M",
    "1Y": "1Y",
}


@dataclass(frozen=True)
class TaiwanOwnershipConcentrationPoint:
    date: str
    holders_400_lots_or_more_percent: float | None
    foreign_ownership_percent: float | None
    investment_trust_ownership_percent: float | None
    dealer_ownership_percent: float | None
    director_ownership_percent: float | None
    weekly_close: float | None


@dataclass(frozen=True)
class TaiwanOwnershipConcentrationResult:
    stock_id: str
    range: OwnershipRange
    as_of: str | None
    source: str
    stale: bool
    points: tuple[TaiwanOwnershipConcentrationPoint, ...]


@dataclass
class _OwnershipCacheEntry:
    cached_at: datetime
    result: TaiwanOwnershipConcentrationResult
    last_failure_at: datetime | None = None


TdccHistoryFetcher = Callable[[str, date, date], list[TdccConcentrationPoint]]
# (probe_date, board) -> symbol -> ownership %
ForeignOwnershipFetcher = Callable[[date, TaiwanBoard], dict[str, float] | None]
CloseHistoryFetcher = Callable[[str, OwnershipRange], dict[str, float]]


class TwOwnershipConcentrationService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        tdcc_history_fetcher: TdccHistoryFetcher | None = None,
        # Backward-compatible test seam: returns open-data CSV for one snapshot.
        tdcc_csv_fetcher: Callable[[], str] | None = None,
        foreign_ownership_fetcher: ForeignOwnershipFetcher | None = None,
        close_history_fetcher: CloseHistoryFetcher | None = None,
        now_provider: Callable[[], datetime] | None = None,
        result_cache_ttl: timedelta = _RESULT_CACHE_TTL,
        stale_refresh_backoff: timedelta = _STALE_REFRESH_BACKOFF,
        max_foreign_probes: int = _MAX_FOREIGN_PROBES,
        max_tdcc_weeks: int = _MAX_TDCC_WEEKS,
    ) -> None:
        self._quote_source = quote_source or quote_service
        self._tdcc_csv_fetcher = tdcc_csv_fetcher
        self._tdcc_history_fetcher = tdcc_history_fetcher or self._default_tdcc_history_fetcher
        self._foreign_ownership_fetcher = (
            foreign_ownership_fetcher or _default_foreign_ownership_fetcher
        )
        self._close_history_fetcher = close_history_fetcher or self._default_close_history_fetcher
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._result_cache_ttl = result_cache_ttl
        self._stale_refresh_backoff = stale_refresh_backoff
        self._max_foreign_probes = max(1, max_foreign_probes)
        self._max_tdcc_weeks = max(1, max_tdcc_weeks)
        self._result_cache: dict[tuple[str, OwnershipRange], _OwnershipCacheEntry] = {}

    def clear_cache(self) -> None:
        self._result_cache.clear()

    def get_ownership_concentration(
        self,
        stock_id: str,
        *,
        range_name: OwnershipRange = "1Y",
    ) -> TaiwanOwnershipConcentrationResult:
        requested_range = _normalize_range(range_name)
        now = self._now_provider()
        cache_key = (stock_id, requested_range)
        cached = self._result_cache.get(cache_key)
        if cached is not None and now - cached.cached_at < self._result_cache_ttl:
            logger.info(
                "tw_ownership_concentration cache_hit stock_id=%s range=%s",
                stock_id,
                requested_range,
            )
            return cached.result

        if (
            cached is not None
            and cached.last_failure_at is not None
            and now - cached.last_failure_at < self._stale_refresh_backoff
        ):
            logger.info(
                "tw_ownership_concentration stale_backoff stock_id=%s range=%s",
                stock_id,
                requested_range,
            )
            return _as_stale(cached.result)

        try:
            result = self._compute(stock_id, requested_range=requested_range, now=now)
        except Exception:
            if cached is not None:
                logger.warning(
                    "tw_ownership_concentration stale_fallback stock_id=%s range=%s",
                    stock_id,
                    requested_range,
                )
                stale_result = _as_stale(cached.result)
                self._result_cache[cache_key] = _OwnershipCacheEntry(
                    cached_at=cached.cached_at,
                    result=stale_result,
                    last_failure_at=now,
                )
                return stale_result
            raise

        logger.info(
            "tw_ownership_concentration cache_store stock_id=%s range=%s points=%s",
            stock_id,
            requested_range,
            len(result.points),
        )
        self._result_cache[cache_key] = _OwnershipCacheEntry(cached_at=now, result=result)
        return result

    def _compute(
        self,
        stock_id: str,
        *,
        requested_range: OwnershipRange,
        now: datetime,
    ) -> TaiwanOwnershipConcentrationResult:
        stock = require_taiwan_stock(
            stock_id,
            quote_source=self._quote_source,
            not_found_error=_STOCK_NOT_FOUND_ERROR,
            non_taiwan_error=_NON_TW_STOCK_ERROR,
        )
        board, symbol = require_twse_or_tpex_symbol(
            stock,
            quote_source=self._quote_source,
            unknown_market_error=_UNKNOWN_TW_MARKET_ERROR,
        )
        source = _SOURCE_BY_BOARD[board]

        as_of_date = candidate_start_date(now)
        start_date = as_of_date - timedelta(days=_RANGE_DAYS[requested_range] - 1)

        try:
            if self._tdcc_csv_fetcher is not None:
                tdcc_points = parse_tdcc_shareholding_csv(self._tdcc_csv_fetcher(), symbol=symbol)
            else:
                tdcc_points = self._tdcc_history_fetcher(symbol, start_date, as_of_date)
        except (OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc

        holders_by_date = {
            point.date: point.holders_400_lots_or_more_percent
            for point in tdcc_points
            if _date_in_range(point.date, start_date=start_date, end_date=as_of_date)
        }

        try:
            # Newest-first so the probe budget covers the summary card first.
            foreign_by_date = self._collect_foreign_ownership(
                symbol=symbol,
                board=board,
                start_date=start_date,
                end_date=as_of_date,
                prefer_dates=sorted(holders_by_date, reverse=True),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            if not holders_by_date:
                raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc
            foreign_by_date = {}

        if not holders_by_date and not foreign_by_date:
            return TaiwanOwnershipConcentrationResult(
                stock_id=stock.id,
                range=requested_range,
                as_of=None,
                source=source,
                stale=False,
                points=(),
            )

        closes_by_date = self._safe_closes(stock.id, requested_range)
        dates = sorted(set(holders_by_date) | set(foreign_by_date))
        points = tuple(
            TaiwanOwnershipConcentrationPoint(
                date=point_date,
                holders_400_lots_or_more_percent=holders_by_date.get(point_date),
                foreign_ownership_percent=foreign_by_date.get(point_date),
                # Official TDCC / MI_QFIIS / TPEx QFII do not publish these series.
                investment_trust_ownership_percent=None,
                dealer_ownership_percent=None,
                director_ownership_percent=None,
                weekly_close=_lookup_close(closes_by_date, point_date),
            )
            for point_date in dates
        )
        as_of = dates[-1] if dates else None
        return TaiwanOwnershipConcentrationResult(
            stock_id=stock.id,
            range=requested_range,
            as_of=as_of,
            source=source,
            stale=False,
            points=points,
        )

    def _default_tdcc_history_fetcher(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[TdccConcentrationPoint]:
        """Fetch per-Friday portal history, merging the latest open-data snapshot."""
        portal_points = _fetch_tdcc_portal_history(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            max_weeks=self._max_tdcc_weeks,
        )
        by_date = {point.date: point for point in portal_points}

        try:
            open_data_csv = fetch_text(
                _TDCC_OPEN_DATA_URL,
                params={"id": "1-5"},
                timeout_seconds=60.0,
                retries=1,
                encoding="utf-8-sig",
                provider_name="tdcc",
                operation_name="tdcc_shareholding_open_data",
                resource_id="1-5",
            )
            for point in parse_tdcc_shareholding_csv(open_data_csv, symbol=symbol):
                if _date_in_range(point.date, start_date=start_date, end_date=end_date):
                    by_date[point.date] = point
        except (OSError, ValueError, RuntimeError):
            # Portal history alone is enough when open-data is down.
            pass

        return sorted(by_date.values(), key=lambda item: item.date)

    def _collect_foreign_ownership(
        self,
        *,
        symbol: str,
        board: TaiwanBoard,
        start_date: date,
        end_date: date,
        prefer_dates: list[str],
    ) -> dict[str, float]:
        """Probe Fridays (prefer newest TDCC dates), bounded; soft-fail per date."""
        results: dict[str, float] = {}
        preferred: list[date] = []
        for raw in prefer_dates:
            try:
                preferred.append(date.fromisoformat(raw))
            except ValueError:
                continue
        friday_fallback = list(_iter_friday_candidates(start_date, end_date))
        candidates: list[date] = []
        seen: set[date] = set()
        for current in preferred + friday_fallback:
            if current in seen or current < start_date or current > end_date:
                continue
            seen.add(current)
            candidates.append(current)

        for probes, current in enumerate(candidates):
            if probes >= self._max_foreign_probes:
                break
            try:
                payload_map = self._foreign_ownership_fetcher(current, board)
            except (OSError, ValueError, RuntimeError):
                logger.warning(
                    "tw_ownership_concentration foreign_probe_failed board=%s date=%s",
                    board,
                    current.isoformat(),
                )
                continue
            if not payload_map:
                continue
            percent = payload_map.get(symbol)
            if percent is None:
                continue
            results[current.isoformat()] = percent
        return results

    def _safe_closes(self, stock_id: str, requested_range: OwnershipRange) -> dict[str, float]:
        try:
            return self._close_history_fetcher(stock_id, requested_range)
        except Exception:  # noqa: BLE001 - weekly close is best-effort
            return {}

    def _default_close_history_fetcher(
        self,
        stock_id: str,
        requested_range: OwnershipRange,
    ) -> dict[str, float]:
        history_range = _HISTORY_RANGE_BY_OWNERSHIP[requested_range]
        loaded = self._quote_source.get_history(stock_id, history_range)
        if loaded is None:
            return {}
        _stock, candles = loaded
        return _closes_from_candles(candles)


def _as_stale(result: TaiwanOwnershipConcentrationResult) -> TaiwanOwnershipConcentrationResult:
    return TaiwanOwnershipConcentrationResult(
        stock_id=result.stock_id,
        range=result.range,
        as_of=result.as_of,
        source=result.source,
        stale=True,
        points=result.points,
    )


def _fetch_tdcc_portal_history(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    max_weeks: int,
) -> list[TdccConcentrationPoint]:
    """Walk official portal Fridays newest-first with a cookie+CSRF session."""
    import httpx

    timeout = httpx.Timeout(30.0)
    headers = {
        "User-Agent": "stock-ticker-tw-ownership/1",
        "Referer": _TDCC_PORTAL_URL,
    }
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            verify=True,
            headers=headers,
        ) as client:
            return _portal_history_with_client(
                client,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                max_weeks=max_weeks,
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc


def _portal_history_with_client(
    client: object,
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    max_weeks: int,
) -> list[TdccConcentrationPoint]:
    portal_response = client.get(_TDCC_PORTAL_URL)  # type: ignore[attr-defined]
    portal_response.raise_for_status()
    portal_html = portal_response.text
    token = extract_tdcc_synchronizer_token(portal_html)
    if token is None:
        raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)

    available = extract_tdcc_available_dates(portal_html)
    if not available:
        available = [current.isoformat() for current in _iter_friday_candidates(start_date, end_date)]
    fir_date = available[0].replace("-", "") if available else end_date.strftime("%Y%m%d")
    selected = [
        point_date
        for point_date in available
        if _date_in_range(point_date, start_date=start_date, end_date=end_date)
    ][:max_weeks]

    points: list[TdccConcentrationPoint] = []
    for point_date in selected:
        if token is None:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)
        sca = point_date.replace("-", "")
        result_response = client.post(  # type: ignore[attr-defined]
            _TDCC_PORTAL_URL,
            data={
                "SYNCHRONIZER_TOKEN": token,
                "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                "method": "submit",
                "firDate": fir_date,
                "scaDate": sca,
                "sqlMethod": "StockNo",
                "stockNo": symbol,
                "stockName": "",
            },
        )
        result_response.raise_for_status()
        result_html = result_response.text
        token = extract_tdcc_synchronizer_token(result_html) or token
        point = parse_tdcc_qry_stock_html(
            result_html,
            symbol=symbol,
            point_date=point_date,
        )
        if point is not None:
            points.append(point)
    return points


def _iter_friday_candidates(start_date: date, end_date: date):
    """Yield Fridays newest-first within [start_date, end_date]."""
    current = end_date
    while current >= start_date:
        if current.weekday() == 4:
            yield current
        current -= timedelta(days=1)


def _default_foreign_ownership_fetcher(
    current_date: date,
    board: TaiwanBoard,
) -> dict[str, float] | None:
    if board == "TPEx":
        payload = fetch_tpex_data(
            _TPEX_QFII_URL,
            params={
                "date": current_date.strftime("%Y%m%d"),
                "id": "",
                "response": "json",
            },
            operation_name="tw_ownership_tpex_qfii",
            resource_id=current_date.isoformat(),
        )
        return parse_tpex_qfii_foreign_ownership(payload)

    payload = fetch_twse_data(
        _TWSE_MI_QFIIS_URL,
        params={
            "response": "json",
            "date": current_date.strftime("%Y%m%d"),
            "selectType": "ALLBUT0999",
        },
        operation_name="tw_ownership_mi_qfiis",
        resource_id=current_date.isoformat(),
    )
    return parse_mi_qfiis_foreign_ownership(payload)


def parse_mi_qfiis_foreign_ownership(payload: object) -> dict[str, float] | None:
    """Extract symbol -> 全體外資及陸資持股比率 from a MI_QFIIS JSON payload."""
    return _parse_foreign_ownership_table(
        payload,
        symbol_labels=("證券代號", "股票代號"),
        percent_labels=("全體外資及陸資持股比率", "外資及陸資持股比率", "持股比率"),
    )


def parse_tpex_qfii_foreign_ownership(payload: object) -> dict[str, float] | None:
    """Extract symbol -> 僑外資及陸資持股比率 from a TPEx QFII tables payload."""
    if is_taiwan_no_data_payload(payload):
        return None
    if not isinstance(payload, dict):
        return None
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return None
    table = tables[0]
    if not isinstance(table, dict):
        return None
    return _parse_foreign_ownership_table(
        table,
        symbol_labels=("代號", "證券代號", "股票代號"),
        percent_labels=("僑外資及陸資持股比率(E=C/A)", "僑外資及陸資持股比率", "持股比率"),
    )


def _parse_foreign_ownership_table(
    payload: object,
    *,
    symbol_labels: tuple[str, ...],
    percent_labels: tuple[str, ...],
) -> dict[str, float] | None:
    if is_taiwan_no_data_payload(payload):
        return None
    if not isinstance(payload, dict):
        return None

    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        return None

    symbol_idx = taiwan_field_index(fields, *symbol_labels)
    percent_idx = taiwan_field_index(fields, *percent_labels)
    if symbol_idx is None or percent_idx is None:
        return None

    parsed: dict[str, float] = {}
    for row in data:
        symbol, percent = _row_symbol_percent(
            row,
            fields=fields,
            symbol_idx=symbol_idx,
            percent_idx=percent_idx,
            percent_labels=percent_labels,
        )
        if symbol is None or percent is None:
            continue
        parsed[symbol] = percent
    return parsed


def _row_symbol_percent(
    row: object,
    *,
    fields: list[object],
    symbol_idx: int,
    percent_idx: int,
    percent_labels: tuple[str, ...],
) -> tuple[str | None, float | None]:
    if not isinstance(row, (list, tuple)):
        return None, None
    symbol_value = row[symbol_idx] if symbol_idx < len(row) else None
    symbol = str(symbol_value).strip().upper() if symbol_value is not None else ""
    if not symbol:
        return None, None
    raw_percent = row[percent_idx] if percent_idx < len(row) else None
    percent = _parse_ownership_percent(raw_percent)
    if percent is None:
        percent = _parse_ownership_percent(taiwan_row_value(row, fields, *percent_labels))
    if percent is None:
        return symbol, None
    return symbol, round(float(percent), 2)


def _parse_ownership_percent(value: object) -> float | None:
    if isinstance(value, str):
        value = value.strip().rstrip("%％")
    return parse_taiwan_number(value)


def _closes_from_candles(candles: list[CandleRecord]) -> dict[str, float]:
    closes: dict[str, float] = {}
    for candle in candles:
        point_date = normalize_taiwan_date(candle.time[:10] if len(candle.time) >= 10 else candle.time)
        if point_date is None:
            continue
        closes[point_date] = float(candle.close)
    return closes


def _lookup_close(closes_by_date: dict[str, float], point_date: str) -> float | None:
    if point_date in closes_by_date:
        return closes_by_date[point_date]
    try:
        current = date.fromisoformat(point_date)
    except ValueError:
        return None
    for offset in range(1, 11):
        candidate = (current - timedelta(days=offset)).isoformat()
        if candidate in closes_by_date:
            return closes_by_date[candidate]
    return None


def _date_in_range(value: str, *, start_date: date, end_date: date) -> bool:
    try:
        current = date.fromisoformat(value)
    except ValueError:
        return False
    return start_date <= current <= end_date


def _normalize_range(value: str) -> OwnershipRange:
    normalized = value.strip().upper()
    if normalized not in _RANGE_DAYS:
        raise ValueError(f"Unsupported ownership concentration range: {value}")
    return normalized  # type: ignore[return-value]


tw_ownership_concentration_service = TwOwnershipConcentrationService()
