"""Taiwan profitability margins (財報三率) from Yahoo quarterly income statements.

v1 prefers Yahoo Finance fundamentals via the shared HTTP client (not WantGoo).
Official MOPS/XBRL remains a follow-up when a stable open adapter fits this
architecture. Ratios are computed here. ``convert_ytd_to_quarterly`` exists for
cumulative official sources but is not auto-enabled for Yahoo single-quarter
columns.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

from app.core.diagnostics import elapsed_ms
from app.services.quote_service import QuoteService, quote_service
from app.services.taiwan_market_data import normalize_taiwan_date, require_taiwan_stock
from app.services.taiwan_signals_redis_cache import (
    get_cached_json,
    parse_cached_at,
    profitability_cache_key,
    set_cached_json,
)
from app.services.tw_profitability_yahoo import fetch_quarterly_income_statement

logger = logging.getLogger(__name__)

_RESULT_CACHE_TTL = timedelta(hours=24)
_STALE_REFRESH_BACKOFF = timedelta(minutes=15)
_REDIS_HARD_TTL = timedelta(hours=48)
_DEFAULT_QUARTERS = 12
_ALLOWED_QUARTERS = {8, 12, 20}
_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan profitability margins are only available for TW stocks"
_UPSTREAM_UNAVAILABLE_ERROR = "Taiwan profitability margins upstream temporarily unavailable"
_SOURCE = "yahoo_finance_quarterly_income"

_REVENUE_LABELS = (
    "Total Revenue",
    "Operating Revenue",
    "Revenue",
    "TotalRevenue",
)
_GROSS_LABELS = (
    "Gross Profit",
    "GrossProfit",
)
_OPERATING_LABELS = (
    "Operating Income",
    "Operating Income or Loss",
    "OperatingIncome",
    "EBIT",
)
_PRETAX_LABELS = (
    "Pretax Income",
    "Income Before Tax",
    "PretaxIncome",
    "Earnings Before Tax",
)
_NET_LABELS = (
    "Net Income",
    "Net Income Common Stockholders",
    "NetIncome",
    "Net Income Including Noncontrolling Interests",
)


@dataclass(frozen=True)
class TaiwanProfitabilityPoint:
    fiscal_quarter: str
    period_end: str
    gross_margin_percent: float | None
    operating_margin_percent: float | None
    pretax_margin_percent: float | None
    net_margin_percent: float | None
    quarter_end_close: float | None


@dataclass(frozen=True)
class TaiwanProfitabilityResult:
    stock_id: str
    quarters: int
    as_of: str | None
    source: str
    stale: bool
    points: tuple[TaiwanProfitabilityPoint, ...]


@dataclass
class _ProfitabilityCacheEntry:
    cached_at: datetime
    result: TaiwanProfitabilityResult
    last_failure_at: datetime | None = None


QuarterlyIncomeFetchResult = (
    Mapping[Any, Mapping[Any, Any]]
    | tuple[Mapping[Any, Mapping[Any, Any]], str]
    | None
)
QuarterlyIncomeFetcher = Callable[[str], QuarterlyIncomeFetchResult]


class TwProfitabilityService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        quarterly_income_fetcher: QuarterlyIncomeFetcher | None = None,
        now_provider: Callable[[], datetime] | None = None,
        result_cache_ttl: timedelta = _RESULT_CACHE_TTL,
        stale_refresh_backoff: timedelta = _STALE_REFRESH_BACKOFF,
    ) -> None:
        self._quote_source = quote_source or quote_service
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._result_cache_ttl = result_cache_ttl
        self._stale_refresh_backoff = stale_refresh_backoff
        self._result_cache: dict[tuple[str, int], _ProfitabilityCacheEntry] = {}
        self._quarterly_income_fetcher = (
            quarterly_income_fetcher or self._fetch_default_quarterly_income
        )

    def clear_cache(self) -> None:
        self._result_cache.clear()

    def _fetch_default_quarterly_income(self, symbol: str) -> QuarterlyIncomeFetchResult:
        return fetch_quarterly_income_statement(symbol)

    def get_profitability(
        self,
        stock_id: str,
        *,
        quarters: int = _DEFAULT_QUARTERS,
    ) -> TaiwanProfitabilityResult:
        requested_quarters = _normalize_quarters(quarters)
        now = self._now_provider()
        cache_key = (stock_id, requested_quarters)
        cached = self._result_cache.get(cache_key)
        if cached is not None and now - cached.cached_at < self._result_cache_ttl:
            logger.info(
                "tw_profitability cache_hit stock_id=%s quarters=%s points=%s",
                stock_id,
                requested_quarters,
                len(cached.result.points),
            )
            return cached.result

        if (
            cached is not None
            and cached.last_failure_at is not None
            and now - cached.last_failure_at < self._stale_refresh_backoff
        ):
            logger.info(
                "tw_profitability stale_backoff stock_id=%s quarters=%s",
                stock_id,
                requested_quarters,
            )
            return _as_stale(cached.result)

        redis_payload = get_cached_json(profitability_cache_key(stock_id, requested_quarters))
        redis_cached_at = parse_cached_at(redis_payload) if redis_payload else None
        redis_result = (
            _result_from_redis_payload(redis_payload) if redis_payload is not None else None
        )
        if (
            redis_result is not None
            and redis_cached_at is not None
            and now - redis_cached_at < self._result_cache_ttl
        ):
            self._result_cache[cache_key] = _ProfitabilityCacheEntry(
                cached_at=redis_cached_at,
                result=redis_result,
            )
            logger.info(
                "tw_profitability redis_hit stock_id=%s quarters=%s points=%s",
                stock_id,
                requested_quarters,
                len(redis_result.points),
            )
            return redis_result

        started_at = perf_counter()
        try:
            result = self._compute(stock_id, requested_quarters=requested_quarters)
        except Exception as exc:
            logger.warning(
                "tw_profitability upstream_failed stock_id=%s quarters=%s duration_ms=%s reason=%s",
                stock_id,
                requested_quarters,
                elapsed_ms(started_at),
                exc,
            )
            if cached is not None:
                logger.warning(
                    "tw_profitability stale_fallback stock_id=%s quarters=%s",
                    stock_id,
                    requested_quarters,
                )
                stale_result = _as_stale(cached.result)
                self._result_cache[cache_key] = _ProfitabilityCacheEntry(
                    cached_at=cached.cached_at,
                    result=stale_result,
                    last_failure_at=now,
                )
                return stale_result
            if redis_result is not None:
                logger.warning(
                    "tw_profitability redis_stale_fallback stock_id=%s quarters=%s",
                    stock_id,
                    requested_quarters,
                )
                stale_result = _as_stale(redis_result)
                self._result_cache[cache_key] = _ProfitabilityCacheEntry(
                    cached_at=redis_cached_at or now,
                    result=stale_result,
                    last_failure_at=now,
                )
                return stale_result
            raise

        logger.info(
            "tw_profitability cache_store stock_id=%s quarters=%s points=%s source=%s duration_ms=%s",
            stock_id,
            requested_quarters,
            len(result.points),
            result.source,
            elapsed_ms(started_at),
        )
        self._result_cache[cache_key] = _ProfitabilityCacheEntry(cached_at=now, result=result)
        set_cached_json(
            profitability_cache_key(stock_id, requested_quarters),
            ttl_seconds=int(_REDIS_HARD_TTL.total_seconds()),
            payload=_result_to_redis_payload(result, cached_at=now),
        )
        return result

    def _compute(self, stock_id: str, *, requested_quarters: int) -> TaiwanProfitabilityResult:
        stock = require_taiwan_stock(
            stock_id,
            quote_source=self._quote_source,
            not_found_error=_STOCK_NOT_FOUND_ERROR,
            non_taiwan_error=_NON_TW_STOCK_ERROR,
        )
        symbol = (stock.symbol or "").strip().upper() or stock.id.split(":", 1)[-1].strip().upper()

        try:
            fetched = self._quarterly_income_fetcher(symbol)
        except Exception as exc:  # noqa: BLE001 - upstream shapes vary
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc

        statement, source = _unwrap_quarterly_income_fetch(fetched)
        if statement is None:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)

        points = build_profitability_points(statement, quarters=requested_quarters)
        as_of = points[-1].period_end if points else None
        return TaiwanProfitabilityResult(
            stock_id=stock.id,
            quarters=requested_quarters,
            as_of=as_of,
            source=source,
            stale=False,
            points=tuple(points),
        )


def _unwrap_quarterly_income_fetch(
    fetched: QuarterlyIncomeFetchResult,
) -> tuple[Mapping[Any, Mapping[Any, Any]] | None, str]:
    """Keep provider source request-local (never mutate shared service state)."""
    if fetched is None:
        return None, _SOURCE
    if isinstance(fetched, tuple):
        statement, source = fetched
        if not isinstance(source, str) or not source:
            return statement, _SOURCE
        return statement, source
    return fetched, _SOURCE


def _as_stale(result: TaiwanProfitabilityResult) -> TaiwanProfitabilityResult:
    return TaiwanProfitabilityResult(
        stock_id=result.stock_id,
        quarters=result.quarters,
        as_of=result.as_of,
        source=result.source,
        stale=True,
        points=result.points,
    )


def _result_to_redis_payload(result: TaiwanProfitabilityResult, *, cached_at: datetime) -> dict:
    return {
        "cachedAt": cached_at.isoformat().replace("+00:00", "Z"),
        "stockId": result.stock_id,
        "quarters": result.quarters,
        "asOf": result.as_of,
        "source": result.source,
        "stale": False,
        "points": [
            {
                "fiscalQuarter": point.fiscal_quarter,
                "periodEnd": point.period_end,
                "grossMarginPercent": point.gross_margin_percent,
                "operatingMarginPercent": point.operating_margin_percent,
                "pretaxMarginPercent": point.pretax_margin_percent,
                "netMarginPercent": point.net_margin_percent,
                "quarterEndClose": point.quarter_end_close,
            }
            for point in result.points
        ],
    }


def _result_from_redis_payload(payload: dict) -> TaiwanProfitabilityResult | None:
    stock_id = payload.get("stockId")
    quarters = payload.get("quarters")
    if not isinstance(stock_id, str) or quarters not in _ALLOWED_QUARTERS:
        return None
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        return None
    points: list[TaiwanProfitabilityPoint] = []
    for item in raw_points:
        if not isinstance(item, dict):
            continue
        fiscal = item.get("fiscalQuarter")
        period_end = item.get("periodEnd")
        if not isinstance(fiscal, str) or not isinstance(period_end, str):
            continue
        points.append(
            TaiwanProfitabilityPoint(
                fiscal_quarter=fiscal,
                period_end=period_end,
                gross_margin_percent=_optional_float(item.get("grossMarginPercent")),
                operating_margin_percent=_optional_float(item.get("operatingMarginPercent")),
                pretax_margin_percent=_optional_float(item.get("pretaxMarginPercent")),
                net_margin_percent=_optional_float(item.get("netMarginPercent")),
                quarter_end_close=_optional_float(item.get("quarterEndClose")),
            )
        )
    as_of = payload.get("asOf")
    source = payload.get("source")
    return TaiwanProfitabilityResult(
        stock_id=stock_id,
        quarters=int(quarters),
        as_of=as_of if isinstance(as_of, str) else None,
        source=source if isinstance(source, str) else _SOURCE,
        stale=False,
        points=tuple(points),
    )


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def build_profitability_points(
    statement: Mapping[Any, Mapping[Any, Any]] | Sequence[Mapping[str, Any]],
    *,
    quarters: int = _DEFAULT_QUARTERS,
    treat_as_ytd: bool = False,
) -> list[TaiwanProfitabilityPoint]:
    """Build margin points from a quarterly income statement-like mapping.

    Accepts either:
    - yfinance-style ``{row_label: {period_end: value}}``
    - list of period dicts with revenue / income fields

    ``treat_as_ytd`` must be set explicitly for cumulative YTD columns.
    Auto-detecting YTD from non-decreasing revenue is unsafe — growing firms
    often post rising quarterly revenue without being cumulative.
    """
    columns = _normalize_statement_columns(statement)
    if not columns:
        return []

    if treat_as_ytd:
        columns = convert_ytd_to_quarterly(columns)

    points: list[TaiwanProfitabilityPoint] = []
    for period_end in sorted(columns):
        values = columns[period_end]
        revenue = values.get("revenue")
        gross = values.get("gross")
        operating = values.get("operating")
        pretax = values.get("pretax")
        net = values.get("net")
        points.append(
            TaiwanProfitabilityPoint(
                fiscal_quarter=fiscal_quarter_label(period_end),
                period_end=period_end,
                gross_margin_percent=margin_percent(gross, revenue),
                operating_margin_percent=margin_percent(operating, revenue),
                pretax_margin_percent=margin_percent(pretax, revenue),
                net_margin_percent=margin_percent(net, revenue),
                # Price alignment is optional; leave null unless a caller enriches later.
                quarter_end_close=None,
            )
        )

    points = _dedupe_fiscal_quarters(points)
    if quarters > 0:
        points = points[-quarters:]
    return points


def margin_percent(numerator: float | None, revenue: float | None) -> float | None:
    """Centralized margin formula: numerator / revenue * 100, rounded to 2 decimals."""
    if revenue is None or revenue == 0:
        return None
    if numerator is None:
        return None
    return round(float(numerator) / float(revenue) * 100.0, 2)


def _normalize_statement_columns(
    statement: Mapping[Any, Mapping[Any, Any]] | Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float | None]]:
    if isinstance(statement, Sequence) and not isinstance(statement, (str, bytes)):
        return _normalize_period_row_list(statement)
    if not isinstance(statement, Mapping):
        return {}
    return _normalize_yfinance_statement(statement)


def _normalize_period_row_list(
    statement: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float | None]]:
    columns: dict[str, dict[str, float | None]] = {}
    for row in statement:
        if not isinstance(row, Mapping):
            continue
        period_end = normalize_taiwan_date(row.get("periodEnd") or row.get("period_end") or row.get("date"))
        if period_end is None:
            continue
        columns[period_end] = {
            "revenue": _as_float(row.get("revenue") or row.get("Total Revenue")),
            "gross": _as_float(row.get("gross") or row.get("Gross Profit")),
            "operating": _as_float(row.get("operating") or row.get("Operating Income")),
            "pretax": _as_float(row.get("pretax") or row.get("Pretax Income")),
            "net": _as_float(row.get("net") or row.get("Net Income")),
        }
    return columns


def _normalize_yfinance_statement(
    statement: Mapping[Any, Mapping[Any, Any]],
) -> dict[str, dict[str, float | None]]:
    period_ends: set[str] = set()
    labeled_rows: dict[str, Mapping[Any, Any]] = {}
    for raw_label, series in statement.items():
        label = str(raw_label).strip()
        if not isinstance(series, Mapping):
            continue
        labeled_rows[label] = series
        for period in series:
            normalized = _period_end_key(period)
            if normalized is not None:
                period_ends.add(normalized)

    columns = {
        period: {
            "revenue": None,
            "gross": None,
            "operating": None,
            "pretax": None,
            "net": None,
        }
        for period in period_ends
    }

    # Prefer earlier aliases in each label tuple (e.g. Operating Income over EBIT).
    best_series: dict[str, tuple[int, Mapping[Any, Any]]] = {}
    for label, series in labeled_rows.items():
        mapped = _map_row_label_priority(label)
        if mapped is None:
            continue
        field, priority = mapped
        current = best_series.get(field)
        if current is None or priority < current[0]:
            best_series[field] = (priority, series)

    for field, (_priority, series) in best_series.items():
        for period, value in series.items():
            period_end = _period_end_key(period)
            if period_end is None or period_end not in columns:
                continue
            columns[period_end][field] = _as_float(value)
    return columns


def convert_ytd_to_quarterly(
    columns: dict[str, dict[str, float | None]],
) -> dict[str, dict[str, float | None]]:
    """Convert cumulative YTD columns within each fiscal year to quarterly increments.

    Within a year, Q1 stays as-is, Q2/Q3 differ from prior YTD, and Q4 uses
    annual (or Q4 YTD) minus Q3 YTD when present.
    """
    if len(columns) < 2:
        return columns

    by_year: dict[int, list[str]] = {}
    for period in sorted(columns):
        year = int(period[:4])
        by_year.setdefault(year, []).append(period)

    converted: dict[str, dict[str, float | None]] = {}
    for periods in by_year.values():
        converted.update(_ytd_year_to_quarterly(columns, periods))
    return converted


def _ytd_year_to_quarterly(
    columns: dict[str, dict[str, float | None]],
    periods: list[str],
) -> dict[str, dict[str, float | None]]:
    previous: dict[str, float | None] = {
        "revenue": 0.0,
        "gross": 0.0,
        "operating": 0.0,
        "pretax": 0.0,
        "net": 0.0,
    }
    converted: dict[str, dict[str, float | None]] = {}
    for period in periods:
        current = columns[period]
        quarter_values: dict[str, float | None] = {}
        for field in ("revenue", "gross", "operating", "pretax", "net"):
            quarter_values[field] = _ytd_field_delta(current.get(field), previous.get(field))
            if current.get(field) is not None:
                previous[field] = current.get(field)
        converted[period] = quarter_values
    return converted


def _ytd_field_delta(
    current_value: float | None,
    previous_value: float | None,
) -> float | None:
    if current_value is None:
        return None
    if previous_value is None:
        return current_value
    return current_value - previous_value


def _map_row_label_priority(label: str) -> tuple[str, int] | None:
    """Return ``(field, priority)`` where lower priority wins when aliases coexist."""
    normalized = " ".join(label.strip().split())
    compact = normalized.replace(" ", "").lower()
    checks = (
        ("revenue", _REVENUE_LABELS),
        ("gross", _GROSS_LABELS),
        ("operating", _OPERATING_LABELS),
        ("pretax", _PRETAX_LABELS),
        ("net", _NET_LABELS),
    )
    for field, labels in checks:
        for priority, candidate in enumerate(labels):
            if normalized.lower() == candidate.lower() or compact == candidate.replace(" ", "").lower():
                return field, priority
    return None


def _map_row_label(label: str) -> str | None:
    mapped = _map_row_label_priority(label)
    return None if mapped is None else mapped[0]


def _period_end_key(value: object) -> str | None:
    if hasattr(value, "date") and callable(getattr(value, "date")):
        try:
            return value.date().isoformat()  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            iso = value.isoformat()  # type: ignore[union-attr]
            return normalize_taiwan_date(str(iso)[:10])
        except Exception:  # noqa: BLE001
            pass
    return normalize_taiwan_date(str(value)[:10] if value is not None else None)


def fiscal_quarter_label(period_end: str) -> str:
    """Map a period-end date to a calendar fiscal-quarter label (``2025Q3``).

    Early-month dates on quarter boundaries (e.g. ``2025-04-01``) snap to the
    prior calendar quarter so filing/settlement slips do not shift the axis.
    """
    year = int(period_end[:4])
    month = int(period_end[5:7])
    day = int(period_end[8:10])
    if day <= 7 and month in {1, 4, 7, 10}:
        if month == 1:
            return f"{year - 1}Q4"
        prior_quarter_month = month - 1
        return f"{year}Q{(prior_quarter_month - 1) // 3 + 1}"
    quarter = (month - 1) // 3 + 1
    return f"{year}Q{quarter}"


def _dedupe_fiscal_quarters(
    points: list[TaiwanProfitabilityPoint],
) -> list[TaiwanProfitabilityPoint]:
    """Prefer the latest period_end when restatements share a fiscal quarter."""
    best: dict[str, TaiwanProfitabilityPoint] = {}
    for point in points:
        current = best.get(point.fiscal_quarter)
        if current is None or point.period_end >= current.period_end:
            best[point.fiscal_quarter] = point
    return sorted(best.values(), key=lambda item: item.period_end)


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    import math

    if math.isnan(numeric):
        return None
    return numeric


def _normalize_quarters(value: int) -> int:
    if value not in _ALLOWED_QUARTERS:
        raise ValueError(f"Unsupported profitability quarters: {value}")
    return value


tw_profitability_service = TwProfitabilityService()
