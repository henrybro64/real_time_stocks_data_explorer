"""Taiwan ownership concentration (籌碼集中度) from TDCC + TWSE MI_QFIIS.

TDCC open-data ``id=1-5`` is a weekly overwrite snapshot only. Multi-week
``400張以上`` history comes from the official TDCC portal qryStock pages
(about one year of Fridays), not from WantGoo.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from app.models.quote import CandleRecord
from app.services.http_client import HttpFetchError, fetch_text
from app.services.quote_service import QuoteService, quote_service
from app.services.taiwan_history_scan import ResultCacheEntry, candidate_start_date
from app.services.taiwan_market_data import (
    fetch_twse_data,
    is_taiwan_no_data_payload,
    normalize_taiwan_date,
    parse_taiwan_number,
    require_taiwan_stock,
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

logger = logging.getLogger(__name__)

_RESULT_CACHE_TTL = timedelta(hours=12)
# Fridays only; ~26 covers 6M weekly or 1Y biweekly-ish. Prefer TDCC dates first.
_MAX_FOREIGN_PROBES = 26
_MAX_TDCC_WEEKS = 55
_TDCC_OPEN_DATA_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx"
_TDCC_PORTAL_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
_TWSE_MI_QFIIS_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan ownership concentration is only available for TW stocks"
_UPSTREAM_UNAVAILABLE_ERROR = "Taiwan ownership concentration upstream temporarily unavailable"
_SOURCE = "tdcc_portal+twse_mi_qfiis"
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


TdccHistoryFetcher = Callable[[str, date, date], list[TdccConcentrationPoint]]
ForeignOwnershipFetcher = Callable[[date], dict[str, float] | None]
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
        max_foreign_probes: int = _MAX_FOREIGN_PROBES,
        max_tdcc_weeks: int = _MAX_TDCC_WEEKS,
    ) -> None:
        self._quote_source = quote_source or quote_service
        self._tdcc_csv_fetcher = tdcc_csv_fetcher
        self._tdcc_history_fetcher = tdcc_history_fetcher or self._default_tdcc_history_fetcher
        self._foreign_ownership_fetcher = foreign_ownership_fetcher or _default_foreign_ownership_fetcher
        self._close_history_fetcher = close_history_fetcher or self._default_close_history_fetcher
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._result_cache_ttl = result_cache_ttl
        self._max_foreign_probes = max(1, max_foreign_probes)
        self._max_tdcc_weeks = max(1, max_tdcc_weeks)
        self._result_cache: dict[tuple[str, OwnershipRange], ResultCacheEntry[TaiwanOwnershipConcentrationResult]] = {}

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

        try:
            result = self._compute(stock_id, requested_range=requested_range, now=now)
        except Exception:
            if cached is not None:
                logger.warning(
                    "tw_ownership_concentration stale_fallback stock_id=%s range=%s",
                    stock_id,
                    requested_range,
                )
                return TaiwanOwnershipConcentrationResult(
                    stock_id=cached.result.stock_id,
                    range=cached.result.range,
                    as_of=cached.result.as_of,
                    source=cached.result.source,
                    stale=True,
                    points=cached.result.points,
                )
            raise

        logger.info(
            "tw_ownership_concentration cache_store stock_id=%s range=%s points=%s",
            stock_id,
            requested_range,
            len(result.points),
        )
        self._result_cache[cache_key] = ResultCacheEntry(cached_at=now, result=result)
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
        symbol = (stock.symbol or "").strip().upper() or stock.id.split(":", 1)[-1].strip().upper()

        as_of_date = candidate_start_date(now)
        start_date = as_of_date - timedelta(days=_RANGE_DAYS[requested_range] - 1)

        try:
            if self._tdcc_csv_fetcher is not None:
                # Test / single-snapshot seam.
                tdcc_points = parse_tdcc_shareholding_csv(self._tdcc_csv_fetcher(), symbol=symbol)
            else:
                tdcc_points = self._tdcc_history_fetcher(symbol, start_date, as_of_date)
        except (HttpFetchError, OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc

        holders_by_date = {
            point.date: point.holders_400_lots_or_more_percent
            for point in tdcc_points
            if _date_in_range(point.date, start_date=start_date, end_date=as_of_date)
        }

        try:
            foreign_by_date = self._collect_foreign_ownership(
                symbol=symbol,
                start_date=start_date,
                end_date=as_of_date,
                prefer_dates=sorted(holders_by_date),
            )
        except (HttpFetchError, OSError, ValueError, RuntimeError) as exc:
            if not holders_by_date:
                raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc
            foreign_by_date = {}

        if not holders_by_date and not foreign_by_date:
            return TaiwanOwnershipConcentrationResult(
                stock_id=stock.id,
                range=requested_range,
                as_of=None,
                source=_SOURCE,
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
                # Official TDCC / MI_QFIIS do not publish these ownership % series.
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
            source=_SOURCE,
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
        except (HttpFetchError, OSError, ValueError, RuntimeError):
            # Portal history alone is enough when open-data is down.
            pass

        return sorted(by_date.values(), key=lambda item: item.date)

    def _collect_foreign_ownership(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        prefer_dates: list[str],
    ) -> dict[str, float]:
        """Probe Fridays (prefer TDCC dates), bounded."""
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
            payload_map = self._foreign_ownership_fetcher(current)
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

    def _load_with_client(verify: bool) -> list[TdccConcentrationPoint]:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            verify=verify,
            headers=headers,
        ) as client:
            portal_response = client.get(_TDCC_PORTAL_URL)
            portal_response.raise_for_status()
            portal_html = portal_response.text
            token = extract_tdcc_synchronizer_token(portal_html)
            available = extract_tdcc_available_dates(portal_html)
            if not available:
                available = [
                    current.isoformat() for current in _iter_friday_candidates(start_date, end_date)
                ]
            fir_date = available[0].replace("-", "") if available else end_date.strftime("%Y%m%d")
            selected = [
                point_date
                for point_date in available
                if _date_in_range(point_date, start_date=start_date, end_date=end_date)
            ][:max_weeks]

            points: list[TdccConcentrationPoint] = []
            for point_date in selected:
                if token is None:
                    break
                sca = point_date.replace("-", "")
                result_response = client.post(
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

    try:
        return _load_with_client(verify=True)
    except httpx.HTTPError:
        try:
            logger.warning(
                "tdcc portal history retrying with insecure TLS fallback symbol=%s",
                symbol,
            )
            return _load_with_client(verify=False)
        except httpx.HTTPError as exc:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc


def _iter_friday_candidates(start_date: date, end_date: date):
    """Yield Fridays newest-first within [start_date, end_date]."""
    current = end_date
    while current >= start_date:
        if current.weekday() == 4:
            yield current
        current -= timedelta(days=1)


def _default_foreign_ownership_fetcher(current_date: date) -> dict[str, float] | None:
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
    if is_taiwan_no_data_payload(payload):
        return None
    if not isinstance(payload, dict):
        return None

    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        return None

    symbol_idx = taiwan_field_index(fields, "證券代號", "股票代號")
    percent_idx = taiwan_field_index(
        fields,
        "全體外資及陸資持股比率",
        "外資及陸資持股比率",
        "持股比率",
    )
    if symbol_idx is None or percent_idx is None:
        return None

    parsed: dict[str, float] = {}
    for row in data:
        if not isinstance(row, (list, tuple)):
            continue
        symbol_value = row[symbol_idx] if symbol_idx < len(row) else None
        symbol = str(symbol_value).strip().upper() if symbol_value is not None else ""
        if not symbol:
            continue
        percent = parse_taiwan_number(row[percent_idx] if percent_idx < len(row) else None)
        if percent is None:
            percent = parse_taiwan_number(
                taiwan_row_value(row, fields, "全體外資及陸資持股比率", "外資及陸資持股比率")
            )
        if percent is None:
            continue
        parsed[symbol] = round(float(percent), 2)
    return parsed


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
