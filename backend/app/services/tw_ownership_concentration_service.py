"""Taiwan ownership concentration (籌碼集中度) from TDCC + foreign ownership %.

TDCC open-data ``id=1-5`` is a weekly overwrite snapshot only. Multi-week
``400張以上`` history comes from the official TDCC portal qryStock pages
(about one year of Fridays), not from WantGoo.

Foreign ownership uses TWSE ``MI_QFIIS`` (``selectType=ALL`` so equities and
ETFs are included; ``ALLBUT0999`` would drop the ETF/受益憑證 bucket) or TPEx
``insti/qfii`` by board. When a TDCC Friday has no foreign table (holiday),
probes look back within ``_FOREIGN_LOOKBACK_DAYS`` and attach the resolved
value to the TDCC date.

Official no-data payloads return ``None`` from parsers; malformed schemas raise
``ForeignOwnershipSchemaError`` so callers can mark ``temporarily_unavailable``
instead of treating a parse break as “not published.”
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import httpx

from app.models.quote import CandleRecord
from app.services.http_client import (
    HttpFetchError,
    capped_retry_sleep_seconds,
    fetch_text,
    parse_retry_after_seconds,
)
from app.services.quote_service import QuoteService, quote_service
from app.services.taiwan_history_scan import candidate_start_date
from app.services.taiwan_market_data import (
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
from app.services.taiwan_signals_redis_cache import (
    get_cached_json,
    ownership_cache_key,
    parse_cached_at,
    set_cached_json,
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
OwnershipComponentStatus = Literal[
    "available",
    "not_published",
    "unsupported",
    "temporarily_unavailable",
    "no_data_for_period",
]

logger = logging.getLogger(__name__)

_RESULT_CACHE_TTL = timedelta(hours=12)
_INCOMPLETE_RESULT_CACHE_TTL = timedelta(minutes=20)
_STALE_REFRESH_BACKOFF = timedelta(minutes=15)
# Hard Redis TTL keeps a stale copy available after soft TTL for failover.
_REDIS_HARD_TTL = timedelta(hours=24)
_INCOMPLETE_REDIS_HARD_TTL = timedelta(minutes=45)
# Per TDCC date: exact day + lookback window. Shared probe-date cache avoids refetch.
_FOREIGN_LOOKBACK_DAYS = 7
# Bounded unique upstream foreign table fetches per compute.
_MAX_FOREIGN_PROBES = 40
_MAX_TDCC_WEEKS = 55
# Nearest-prior weekly close window (calendar days).
_CLOSE_LOOKBACK_DAYS = 10
_TDCC_OPEN_DATA_URL = "https://smart.tdcc.com.tw/opendata/getOD.ashx"
_TDCC_PORTAL_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
_TWSE_MI_QFIIS_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
_TPEX_QFII_URL = "https://www.tpex.org.tw/www/zh-tw/insti/qfii"
# Include equities + ETFs (ALLBUT0999 excludes the 0999 ETF/受益憑證 bucket).
_TWSE_MI_QFIIS_SELECT_TYPE = "ALL"
_QUOTE_HISTORY_SOURCE = "quote_history"
_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan ownership concentration is only available for TW stocks"
_UNKNOWN_TW_MARKET_ERROR = "Taiwan ownership market is unavailable for this stock"
_UPSTREAM_UNAVAILABLE_ERROR = "Taiwan ownership concentration upstream temporarily unavailable"
_FOREIGN_SOURCE_BY_BOARD: dict[TaiwanBoard, str] = {
    "TWSE": "twse_mi_qfiis",
    "TPEx": "tpex_qfii",
}
_PORTAL_TIMEOUT_SECONDS = 30.0
_PORTAL_RETRIES = 2
_PORTAL_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_PORTAL_HEADERS = {
    "User-Agent": "stock-ticker-tw-ownership/1",
    "Referer": _TDCC_PORTAL_URL,
}


class ForeignOwnershipSchemaError(ValueError):
    """Upstream foreign-ownership JSON is present but columns/layout are unusable."""


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

# Sentinel: probe raised; distinct from no_data (None) and empty/parsed map (dict).
_PROBE_ERROR = object()


@dataclass(frozen=True)
class OwnershipComponentState:
    status: OwnershipComponentStatus
    reason: str | None = None


@dataclass(frozen=True)
class OwnershipDataStatus:
    tdcc: OwnershipComponentState
    foreign_ownership: OwnershipComponentState
    weekly_close: OwnershipComponentState


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
    data_status: OwnershipDataStatus
    currency: str | None = "TWD"


@dataclass
class _OwnershipCacheEntry:
    cached_at: datetime
    result: TaiwanOwnershipConcentrationResult
    last_failure_at: datetime | None = None


@dataclass(frozen=True)
class _CloseHistoryLoad:
    closes: dict[str, float]
    failed: bool
    reason: str | None = None


@dataclass(frozen=True)
class _ForeignCollectionResult:
    by_tdcc_date: dict[str, float]
    status: OwnershipComponentStatus
    reason: str | None


TdccHistoryFetcher = Callable[[str, date, date], list[TdccConcentrationPoint]]
# (probe_date, board) -> symbol -> ownership %; None means official no_data.
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
        incomplete_result_cache_ttl: timedelta = _INCOMPLETE_RESULT_CACHE_TTL,
        stale_refresh_backoff: timedelta = _STALE_REFRESH_BACKOFF,
        max_foreign_probes: int = _MAX_FOREIGN_PROBES,
        max_tdcc_weeks: int = _MAX_TDCC_WEEKS,
        foreign_lookback_days: int = _FOREIGN_LOOKBACK_DAYS,
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
        self._incomplete_result_cache_ttl = incomplete_result_cache_ttl
        self._stale_refresh_backoff = stale_refresh_backoff
        self._max_foreign_probes = max(1, max_foreign_probes)
        self._max_tdcc_weeks = max(1, max_tdcc_weeks)
        self._foreign_lookback_days = max(0, foreign_lookback_days)
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
        if cached is not None and now - cached.cached_at < _soft_ttl_for(
            cached.result,
            complete_ttl=self._result_cache_ttl,
            incomplete_ttl=self._incomplete_result_cache_ttl,
        ):
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

        redis_payload = get_cached_json(ownership_cache_key(stock_id, requested_range))
        redis_cached_at = parse_cached_at(redis_payload) if redis_payload else None
        redis_result = (
            _result_from_redis_payload(redis_payload)
            if redis_payload is not None
            else None
        )
        if (
            redis_result is not None
            and redis_cached_at is not None
            and now - redis_cached_at
            < _soft_ttl_for(
                redis_result,
                complete_ttl=self._result_cache_ttl,
                incomplete_ttl=self._incomplete_result_cache_ttl,
            )
        ):
            self._result_cache[cache_key] = _OwnershipCacheEntry(
                cached_at=redis_cached_at,
                result=redis_result,
            )
            logger.info(
                "tw_ownership_concentration redis_hit stock_id=%s range=%s",
                stock_id,
                requested_range,
            )
            return redis_result

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
            if redis_result is not None:
                logger.warning(
                    "tw_ownership_concentration redis_stale_fallback stock_id=%s range=%s",
                    stock_id,
                    requested_range,
                )
                stale_result = _as_stale(redis_result)
                self._result_cache[cache_key] = _OwnershipCacheEntry(
                    cached_at=redis_cached_at or now,
                    result=stale_result,
                    last_failure_at=now,
                )
                return stale_result
            raise

        if _is_incomplete(result):
            complete_cached = None
            if cached is not None and _is_complete(cached.result):
                complete_cached = cached.result
            elif redis_result is not None and _is_complete(redis_result):
                complete_cached = redis_result
            if complete_cached is not None:
                logger.info(
                    "tw_ownership_concentration stale_complete_fallback stock_id=%s range=%s",
                    stock_id,
                    requested_range,
                )
                stale_complete = _as_stale(complete_cached)
                # Keep the complete entry; do not overwrite with incomplete.
                self._result_cache[cache_key] = _OwnershipCacheEntry(
                    cached_at=cached.cached_at if cached is not None else (redis_cached_at or now),
                    result=stale_complete,
                    last_failure_at=now,
                )
                return stale_complete

        redis_ttl = (
            _INCOMPLETE_REDIS_HARD_TTL if _is_incomplete(result) else _REDIS_HARD_TTL
        )
        logger.info(
            "tw_ownership_concentration cache_store stock_id=%s range=%s points=%s incomplete=%s",
            stock_id,
            requested_range,
            len(result.points),
            _is_incomplete(result),
        )
        self._result_cache[cache_key] = _OwnershipCacheEntry(cached_at=now, result=result)
        set_cached_json(
            ownership_cache_key(stock_id, requested_range),
            ttl_seconds=int(redis_ttl.total_seconds()),
            payload=_result_to_redis_payload(result, cached_at=now),
        )
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
        normalized_symbol = normalize_ownership_symbol(symbol) or symbol
        currency = (stock.currency or "TWD").strip().upper() or "TWD"

        as_of_date = candidate_start_date(now)
        start_date = as_of_date - timedelta(days=_RANGE_DAYS[requested_range] - 1)

        try:
            if self._tdcc_csv_fetcher is not None:
                tdcc_points = parse_tdcc_shareholding_csv(
                    self._tdcc_csv_fetcher(), symbol=normalized_symbol
                )
            else:
                tdcc_points = self._tdcc_history_fetcher(
                    normalized_symbol, start_date, as_of_date
                )
        except (OSError, ValueError, RuntimeError) as exc:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc

        holders_by_date = {
            point.date: point.holders_400_lots_or_more_percent
            for point in tdcc_points
            if _date_in_range(point.date, start_date=start_date, end_date=as_of_date)
        }

        tdcc_dates_newest_first = sorted(holders_by_date, reverse=True)
        foreign_result = self._collect_foreign_ownership(
            stock_id=stock.id,
            symbol=normalized_symbol,
            board=board,
            tdcc_dates=tdcc_dates_newest_first,
        )

        close_load = self._safe_closes(stock.id, requested_range)

        if not holders_by_date:
            data_status = OwnershipDataStatus(
                tdcc=OwnershipComponentState(
                    status="no_data_for_period",
                    reason="tdcc_empty",
                ),
                foreign_ownership=OwnershipComponentState(
                    status=foreign_result.status,
                    reason=foreign_result.reason,
                ),
                weekly_close=_weekly_close_state(close_load, has_any_close=False),
            )
            return TaiwanOwnershipConcentrationResult(
                stock_id=stock.id,
                range=requested_range,
                as_of=None,
                source=_build_source(
                    board,
                    foreign_status=foreign_result.status,
                    weekly_close_status=_weekly_close_state(
                        close_load, has_any_close=False
                    ).status,
                ),
                stale=False,
                points=(),
                data_status=data_status,
                currency=currency,
            )

        dates = sorted(holders_by_date)
        points = tuple(
            TaiwanOwnershipConcentrationPoint(
                date=point_date,
                holders_400_lots_or_more_percent=holders_by_date.get(point_date),
                foreign_ownership_percent=foreign_result.by_tdcc_date.get(point_date),
                # Official TDCC / MI_QFIIS / TPEx QFII do not publish these series.
                investment_trust_ownership_percent=None,
                dealer_ownership_percent=None,
                director_ownership_percent=None,
                weekly_close=_lookup_close(close_load.closes, point_date),
            )
            for point_date in dates
        )
        has_any_close = any(point.weekly_close is not None for point in points)
        data_status = OwnershipDataStatus(
            tdcc=OwnershipComponentState(status="available"),
            foreign_ownership=OwnershipComponentState(
                status=foreign_result.status,
                reason=foreign_result.reason,
            ),
            weekly_close=_weekly_close_state(close_load, has_any_close=has_any_close),
        )
        return TaiwanOwnershipConcentrationResult(
            stock_id=stock.id,
            range=requested_range,
            as_of=dates[-1] if dates else None,
            source=_build_source(
                board,
                foreign_status=foreign_result.status,
                weekly_close_status=data_status.weekly_close.status,
            ),
            stale=False,
            points=points,
            data_status=data_status,
            currency=currency,
        )

    def _default_tdcc_history_fetcher(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[TdccConcentrationPoint]:
        """Fetch per-Friday portal history, merging the latest open-data snapshot."""
        span_weeks = max(1, (end_date - start_date).days // 7 + 2)
        max_weeks = min(self._max_tdcc_weeks, span_weeks)
        portal_points = _fetch_tdcc_portal_history(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            max_weeks=max_weeks,
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
        stock_id: str,
        symbol: str,
        board: TaiwanBoard,
        tdcc_dates: list[str],
    ) -> _ForeignCollectionResult:
        """Resolve foreign % per TDCC date with lookback; soft-fail per probe."""
        results: dict[str, float] = {}
        if not tdcc_dates:
            return _ForeignCollectionResult(
                by_tdcc_date={},
                status="no_data_for_period",
                reason="no_tdcc_dates",
            )

        # Cache unique probe-date payloads within this compute.
        probe_cache: dict[date, object] = {}
        probes_used = 0
        saw_error = False
        saw_schema_error = False
        saw_loaded_table = False
        saw_symbol_absent = False
        saw_no_data = False

        for tdcc_iso in tdcc_dates:
            try:
                tdcc_date = date.fromisoformat(tdcc_iso)
            except ValueError:
                continue

            resolved_percent: float | None = None
            resolved_date: date | None = None
            null_reason: str | None = None

            for offset in range(self._foreign_lookback_days + 1):
                if probes_used >= self._max_foreign_probes and (
                    (tdcc_date - timedelta(days=offset)) not in probe_cache
                ):
                    null_reason = "probe_budget_exhausted"
                    break

                probe_date = tdcc_date - timedelta(days=offset)
                if probe_date not in probe_cache:
                    if probes_used >= self._max_foreign_probes:
                        null_reason = "probe_budget_exhausted"
                        break
                    probes_used += 1
                    try:
                        payload_map = self._foreign_ownership_fetcher(probe_date, board)
                        probe_cache[probe_date] = payload_map
                    except ForeignOwnershipSchemaError as exc:
                        saw_error = True
                        saw_schema_error = True
                        probe_cache[probe_date] = _PROBE_ERROR
                        logger.warning(
                            "tw_ownership_concentration foreign_probe "
                            "stock_id=%s symbol=%s board=%s requested_date=%s "
                            "resolved_date=%s result=schema_error null_reason=%s",
                            stock_id,
                            symbol,
                            board,
                            tdcc_iso,
                            probe_date.isoformat(),
                            str(exc) or type(exc).__name__,
                        )
                        continue
                    except (OSError, ValueError, RuntimeError) as exc:
                        saw_error = True
                        probe_cache[probe_date] = _PROBE_ERROR
                        logger.warning(
                            "tw_ownership_concentration foreign_probe "
                            "stock_id=%s symbol=%s board=%s requested_date=%s "
                            "resolved_date=%s result=error null_reason=%s",
                            stock_id,
                            symbol,
                            board,
                            tdcc_iso,
                            probe_date.isoformat(),
                            type(exc).__name__,
                        )
                        continue

                cached_payload = probe_cache[probe_date]
                if cached_payload is _PROBE_ERROR:
                    saw_error = True
                    null_reason = "upstream_error"
                    continue
                if cached_payload is None:
                    saw_no_data = True
                    null_reason = "no_data"
                    logger.info(
                        "tw_ownership_concentration foreign_probe "
                        "stock_id=%s symbol=%s board=%s requested_date=%s "
                        "resolved_date=%s result=no_data",
                        stock_id,
                        symbol,
                        board,
                        tdcc_iso,
                        probe_date.isoformat(),
                    )
                    continue

                saw_loaded_table = True
                assert isinstance(cached_payload, dict)
                percent = lookup_foreign_percent(cached_payload, symbol)
                if percent is None:
                    saw_symbol_absent = True
                    null_reason = "symbol_absent"
                    logger.info(
                        "tw_ownership_concentration foreign_probe "
                        "stock_id=%s symbol=%s board=%s requested_date=%s "
                        "resolved_date=%s result=miss null_reason=symbol_absent",
                        stock_id,
                        symbol,
                        board,
                        tdcc_iso,
                        probe_date.isoformat(),
                    )
                    continue

                resolved_percent = percent
                resolved_date = probe_date
                logger.info(
                    "tw_ownership_concentration foreign_probe "
                    "stock_id=%s symbol=%s board=%s requested_date=%s "
                    "resolved_date=%s result=hit",
                    stock_id,
                    symbol,
                    board,
                    tdcc_iso,
                    probe_date.isoformat(),
                )
                break

            if resolved_percent is not None and resolved_date is not None:
                results[tdcc_iso] = resolved_percent
            elif null_reason is not None:
                logger.info(
                    "tw_ownership_concentration foreign_attach "
                    "stock_id=%s symbol=%s board=%s requested_date=%s "
                    "resolved_date= null result=miss null_reason=%s",
                    stock_id,
                    symbol,
                    board,
                    tdcc_iso,
                    null_reason,
                )

        if results:
            return _ForeignCollectionResult(
                by_tdcc_date=results,
                status="available",
                reason=None,
            )
        if saw_error and not saw_loaded_table:
            return _ForeignCollectionResult(
                by_tdcc_date={},
                status="temporarily_unavailable",
                reason="schema_error" if saw_schema_error else "upstream_timeout",
            )
        if saw_loaded_table and saw_symbol_absent:
            # Inclusive MI_QFIIS ALL / TPEx QFII still omit some instruments.
            return _ForeignCollectionResult(
                by_tdcc_date={},
                status="not_published",
                reason="symbol_absent",
            )
        if saw_no_data and not saw_loaded_table:
            return _ForeignCollectionResult(
                by_tdcc_date={},
                status="no_data_for_period",
                reason="upstream_no_data",
            )
        if saw_error:
            return _ForeignCollectionResult(
                by_tdcc_date={},
                status="temporarily_unavailable",
                reason="upstream_error",
            )
        return _ForeignCollectionResult(
            by_tdcc_date={},
            status="no_data_for_period",
            reason="foreign_empty",
        )

    def _safe_closes(self, stock_id: str, requested_range: OwnershipRange) -> _CloseHistoryLoad:
        try:
            closes = self._close_history_fetcher(stock_id, requested_range)
            return _CloseHistoryLoad(closes=closes, failed=False)
        except Exception as exc:  # noqa: BLE001 - weekly close is best-effort
            logger.warning(
                "tw_ownership_concentration close_history_failed stock_id=%s reason=%s",
                stock_id,
                type(exc).__name__,
            )
            return _CloseHistoryLoad(
                closes={},
                failed=True,
                reason="history_unavailable",
            )

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


def normalize_ownership_symbol(value: object) -> str | None:
    """Normalize TW symbols for MI_QFIIS / TPEx QFII matching (strip TW: / .TW / .TWO)."""
    return normalize_taiwan_symbol(value)


def ownership_symbol_lookup_keys(symbol: str) -> tuple[str, ...]:
    """Candidate keys covering leading-zero variants used by upstream tables."""
    base = normalize_ownership_symbol(symbol) or symbol.strip().upper()
    keys: list[str] = [base]
    stripped = base.lstrip("0") or "0"
    if stripped not in keys:
        keys.append(stripped)
    if stripped.isdigit():
        for width in (4, 5, 6):
            padded = stripped.zfill(width)
            if padded not in keys:
                keys.append(padded)
    return tuple(keys)


def lookup_foreign_percent(payload_map: dict[str, float], symbol: str) -> float | None:
    for key in ownership_symbol_lookup_keys(symbol):
        if key in payload_map:
            return payload_map[key]
    # Match map keys that may themselves carry prefixes or leading zeros.
    normalized_wanted = set(ownership_symbol_lookup_keys(symbol))
    for map_key, percent in payload_map.items():
        map_normalized = normalize_ownership_symbol(map_key)
        if map_normalized is None:
            continue
        if set(ownership_symbol_lookup_keys(map_normalized)) & normalized_wanted:
            return percent
    return None


def _build_source(
    board: TaiwanBoard,
    *,
    foreign_status: OwnershipComponentStatus,
    weekly_close_status: OwnershipComponentStatus,
) -> str:
    parts = ["tdcc_portal"]
    if foreign_status == "available":
        parts.append(_FOREIGN_SOURCE_BY_BOARD[board])
    if weekly_close_status == "available":
        parts.append(_QUOTE_HISTORY_SOURCE)
    return "+".join(parts)


def _weekly_close_state(
    close_load: _CloseHistoryLoad,
    *,
    has_any_close: bool,
) -> OwnershipComponentState:
    if has_any_close:
        return OwnershipComponentState(status="available")
    if close_load.failed:
        return OwnershipComponentState(
            status="temporarily_unavailable",
            reason=close_load.reason or "history_unavailable",
        )
    return OwnershipComponentState(
        status="no_data_for_period",
        reason="history_empty",
    )


def _is_complete(result: TaiwanOwnershipConcentrationResult) -> bool:
    status = result.data_status
    if status.tdcc.status != "available":
        return False
    if status.weekly_close.status != "available":
        return False
    if status.foreign_ownership.status == "available":
        return True
    # Intentional absence after inclusive MI_QFIIS ALL / TPEx fetch still counts
    # as complete (do not thrash short TTL for symbols that are simply unpublished).
    return status.foreign_ownership.status in {"not_published", "unsupported"}


def _is_incomplete(result: TaiwanOwnershipConcentrationResult) -> bool:
    status = result.data_status
    return any(
        component.status == "temporarily_unavailable"
        for component in (status.tdcc, status.foreign_ownership, status.weekly_close)
    )


def _soft_ttl_for(
    result: TaiwanOwnershipConcentrationResult,
    *,
    complete_ttl: timedelta,
    incomplete_ttl: timedelta,
) -> timedelta:
    return incomplete_ttl if _is_incomplete(result) else complete_ttl


def _select_portal_dates(
    available: list[str],
    *,
    start_date: date,
    end_date: date,
    max_weeks: int,
) -> list[str]:
    """Return in-range portal Fridays newest-first, capped at ``max_weeks``."""
    in_range = [
        point_date
        for point_date in available
        if _date_in_range(point_date, start_date=start_date, end_date=end_date)
    ]
    return sorted(set(in_range), reverse=True)[: max(1, max_weeks)]


def _as_stale(result: TaiwanOwnershipConcentrationResult) -> TaiwanOwnershipConcentrationResult:
    return TaiwanOwnershipConcentrationResult(
        stock_id=result.stock_id,
        range=result.range,
        as_of=result.as_of,
        source=result.source,
        stale=True,
        points=result.points,
        data_status=result.data_status,
        currency=result.currency,
    )


def _component_to_redis(state: OwnershipComponentState) -> dict:
    return {"status": state.status, "reason": state.reason}


def _component_from_redis(payload: object) -> OwnershipComponentState:
    if not isinstance(payload, dict):
        return OwnershipComponentState(status="no_data_for_period", reason="missing_cache_status")
    status = payload.get("status")
    reason = payload.get("reason")
    if status not in {
        "available",
        "not_published",
        "unsupported",
        "temporarily_unavailable",
        "no_data_for_period",
    }:
        return OwnershipComponentState(status="no_data_for_period", reason="invalid_cache_status")
    return OwnershipComponentState(
        status=status,  # type: ignore[arg-type]
        reason=reason if isinstance(reason, str) else None,
    )


def _result_to_redis_payload(
    result: TaiwanOwnershipConcentrationResult,
    *,
    cached_at: datetime,
) -> dict:
    return {
        "cachedAt": cached_at.isoformat().replace("+00:00", "Z"),
        "stockId": result.stock_id,
        "range": result.range,
        "asOf": result.as_of,
        "source": result.source,
        "stale": False,
        "currency": result.currency,
        "dataStatus": {
            "tdcc": _component_to_redis(result.data_status.tdcc),
            "foreignOwnership": _component_to_redis(result.data_status.foreign_ownership),
            "weeklyClose": _component_to_redis(result.data_status.weekly_close),
        },
        "points": [
            {
                "date": point.date,
                "holders400LotsOrMorePercent": point.holders_400_lots_or_more_percent,
                "foreignOwnershipPercent": point.foreign_ownership_percent,
                "investmentTrustOwnershipPercent": point.investment_trust_ownership_percent,
                "dealerOwnershipPercent": point.dealer_ownership_percent,
                "directorOwnershipPercent": point.director_ownership_percent,
                "weeklyClose": point.weekly_close,
            }
            for point in result.points
        ],
    }


def _result_from_redis_payload(payload: dict) -> TaiwanOwnershipConcentrationResult | None:
    stock_id = payload.get("stockId")
    range_name = payload.get("range")
    if not isinstance(stock_id, str) or range_name not in _RANGE_DAYS:
        return None
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        return None
    points: list[TaiwanOwnershipConcentrationPoint] = []
    for item in raw_points:
        if not isinstance(item, dict) or not isinstance(item.get("date"), str):
            continue
        points.append(
            TaiwanOwnershipConcentrationPoint(
                date=item["date"],
                holders_400_lots_or_more_percent=_optional_float(
                    item.get("holders400LotsOrMorePercent")
                ),
                foreign_ownership_percent=_optional_float(item.get("foreignOwnershipPercent")),
                investment_trust_ownership_percent=_optional_float(
                    item.get("investmentTrustOwnershipPercent")
                ),
                dealer_ownership_percent=_optional_float(item.get("dealerOwnershipPercent")),
                director_ownership_percent=_optional_float(item.get("directorOwnershipPercent")),
                weekly_close=_optional_float(item.get("weeklyClose")),
            )
        )
    as_of = payload.get("asOf")
    source = payload.get("source")
    currency = payload.get("currency")
    raw_status = payload.get("dataStatus")
    if isinstance(raw_status, dict):
        data_status = OwnershipDataStatus(
            tdcc=_component_from_redis(raw_status.get("tdcc")),
            foreign_ownership=_component_from_redis(raw_status.get("foreignOwnership")),
            weekly_close=_component_from_redis(raw_status.get("weeklyClose")),
        )
    else:
        # Legacy cache entries without dataStatus: infer conservatively.
        has_holders = any(p.holders_400_lots_or_more_percent is not None for p in points)
        has_foreign = any(p.foreign_ownership_percent is not None for p in points)
        has_close = any(p.weekly_close is not None for p in points)
        data_status = OwnershipDataStatus(
            tdcc=OwnershipComponentState(
                status="available" if has_holders else "no_data_for_period"
            ),
            foreign_ownership=OwnershipComponentState(
                status="available" if has_foreign else "no_data_for_period"
            ),
            weekly_close=OwnershipComponentState(
                status="available" if has_close else "no_data_for_period"
            ),
        )
    return TaiwanOwnershipConcentrationResult(
        stock_id=stock_id,
        range=range_name,  # type: ignore[arg-type]
        as_of=as_of if isinstance(as_of, str) else None,
        source=source if isinstance(source, str) else "tdcc_portal",
        stale=False,
        points=tuple(points),
        data_status=data_status,
        currency=currency if isinstance(currency, str) else "TWD",
    )


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fetch_tdcc_portal_history(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    max_weeks: int,
) -> list[TdccConcentrationPoint]:
    """Walk official portal Fridays newest-first with a cookie+CSRF session.

    Uses a persistent ``httpx.Client`` so SYNCHRONIZER cookies survive across
    GET/POST. Each exchange applies the same bounded exponential backoff +
    Retry-After policy as ``http_client`` (stateless ``fetch_text`` cannot keep
    the CSRF session).
    """
    timeout = httpx.Timeout(_PORTAL_TIMEOUT_SECONDS)
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            verify=True,
            headers=_PORTAL_HEADERS,
        ) as client:
            return _portal_history_with_client(
                client,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                max_weeks=max_weeks,
            )
    except HttpFetchError as exc:
        raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc


def _portal_request_with_retries(
    client: httpx.Client,
    *,
    method: Literal["GET", "POST"],
    url: str,
    data: dict[str, str] | None = None,
    resource_id: str | None = None,
) -> httpx.Response:
    """Retry transient portal failures while preserving the cookie jar."""
    attempts = max(_PORTAL_RETRIES + 1, 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            if method == "GET":
                response = client.get(url)
            else:
                response = client.post(url, data=data)
            if response.status_code in _PORTAL_RETRYABLE_STATUS_CODES:
                retry_after = parse_retry_after_seconds(response.headers.get("retry-after"))
                raise HttpFetchError(
                    f"Retryable HTTP {response.status_code} from {url}",
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                    classification="rate_limited" if response.status_code == 429 else "http_error",
                    retry_after_seconds=retry_after,
                )
            response.raise_for_status()
            return response
        except (httpx.HTTPError, HttpFetchError) as exc:
            last_error = exc
            status_code = None
            retry_after = None
            if isinstance(exc, HttpFetchError):
                status_code = exc.status_code
                retry_after = exc.retry_after_seconds
            elif isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                retry_after = parse_retry_after_seconds(exc.response.headers.get("retry-after"))
            can_retry = attempt < attempts - 1 and (
                status_code is None or status_code in _PORTAL_RETRYABLE_STATUS_CODES
            )
            if not can_retry:
                break
            sleep_seconds = capped_retry_sleep_seconds(retry_after, attempt=attempt)
            logger.warning(
                "tw_ownership_concentration portal_retry method=%s resource_id=%s "
                "status_code=%s attempt=%s sleep_seconds=%s",
                method,
                resource_id,
                status_code,
                attempt + 1,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)
    assert last_error is not None
    raise last_error


def _portal_history_with_client(
    client: httpx.Client,
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    max_weeks: int,
) -> list[TdccConcentrationPoint]:
    portal_response = _portal_request_with_retries(
        client,
        method="GET",
        url=_TDCC_PORTAL_URL,
        resource_id="portal_get",
    )
    portal_html = portal_response.text
    token = extract_tdcc_synchronizer_token(portal_html)
    if token is None:
        raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)

    available = extract_tdcc_available_dates(portal_html)
    if not available:
        available = [current.isoformat() for current in _iter_friday_candidates(start_date, end_date)]
    # Portal HTML option order is not a contract — always take newest first.
    selected = _select_portal_dates(
        available,
        start_date=start_date,
        end_date=end_date,
        max_weeks=max_weeks,
    )
    fir_date = selected[0].replace("-", "") if selected else end_date.strftime("%Y%m%d")

    points: list[TdccConcentrationPoint] = []
    for point_date in selected:
        if token is None:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)
        sca = point_date.replace("-", "")
        result_response = _portal_request_with_retries(
            client,
            method="POST",
            url=_TDCC_PORTAL_URL,
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
            resource_id=point_date,
        )
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
            "selectType": _TWSE_MI_QFIIS_SELECT_TYPE,
        },
        operation_name="tw_ownership_mi_qfiis",
        resource_id=current_date.isoformat(),
    )
    return parse_mi_qfiis_foreign_ownership(payload)


def parse_mi_qfiis_foreign_ownership(payload: object) -> dict[str, float] | None:
    """Extract symbol -> 全體外資及陸資持股比率 from a MI_QFIIS JSON payload.

    Returns ``None`` for official no-data. Raises ``ForeignOwnershipSchemaError``
    when the payload is present but columns cannot be resolved.
    """
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
        raise ForeignOwnershipSchemaError("tpex_qfii_payload_not_object")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        return None
    table = tables[0]
    if not isinstance(table, dict):
        raise ForeignOwnershipSchemaError("tpex_qfii_table_not_object")
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
        raise ForeignOwnershipSchemaError("foreign_ownership_payload_not_object")

    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ForeignOwnershipSchemaError("foreign_ownership_missing_fields_or_data")
    if not fields:
        # Official empty field list with no_data-style body already handled above;
        # empty fields with a non-no_data envelope is a schema break.
        raise ForeignOwnershipSchemaError("foreign_ownership_empty_fields")

    symbol_idx = taiwan_field_index(fields, *symbol_labels)
    percent_idx = taiwan_field_index(fields, *percent_labels)
    if symbol_idx is None or percent_idx is None:
        raise ForeignOwnershipSchemaError("foreign_ownership_unknown_columns")

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
    symbol = normalize_ownership_symbol(symbol_value)
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
    for offset in range(1, _CLOSE_LOOKBACK_DAYS + 1):
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
