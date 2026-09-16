"""Taiwan profitability margins (財報三率) from yfinance quarterly income statements.

v1 uses labeled Yahoo Finance quarterly income statements (not WantGoo, not MOPS).
Ratios are computed in this service. ``convert_ytd_to_quarterly`` exists for
cumulative official sources but is not auto-enabled for yfinance (already
single-quarter columns).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.quote_service import QuoteService, quote_service
from app.services.taiwan_history_scan import ResultCacheEntry
from app.services.taiwan_market_data import normalize_taiwan_date, require_taiwan_stock

_RESULT_CACHE_TTL = timedelta(hours=24)
_DEFAULT_QUARTERS = 12
_ALLOWED_QUARTERS = {8, 12, 20}
_STOCK_NOT_FOUND_ERROR = "Stock not found"
_NON_TW_STOCK_ERROR = "Taiwan profitability margins are only available for TW stocks"
_UPSTREAM_UNAVAILABLE_ERROR = "Taiwan profitability margins upstream temporarily unavailable"
_SOURCE = "yfinance_quarterly_income"

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


QuarterlyIncomeFetcher = Callable[[str], Mapping[Any, Mapping[Any, Any]] | None]


class TwProfitabilityService:
    def __init__(
        self,
        *,
        quote_source: QuoteService | None = None,
        quarterly_income_fetcher: QuarterlyIncomeFetcher | None = None,
        now_provider: Callable[[], datetime] | None = None,
        result_cache_ttl: timedelta = _RESULT_CACHE_TTL,
    ) -> None:
        self._quote_source = quote_source or quote_service
        self._quarterly_income_fetcher = quarterly_income_fetcher or _default_quarterly_income_fetcher
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._result_cache_ttl = result_cache_ttl
        self._result_cache: dict[tuple[str, int], ResultCacheEntry[TaiwanProfitabilityResult]] = {}

    def clear_cache(self) -> None:
        self._result_cache.clear()

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
            return cached.result

        try:
            result = self._compute(stock_id, requested_quarters=requested_quarters)
        except Exception:
            if cached is not None:
                return TaiwanProfitabilityResult(
                    stock_id=cached.result.stock_id,
                    quarters=cached.result.quarters,
                    as_of=cached.result.as_of,
                    source=cached.result.source,
                    stale=True,
                    points=cached.result.points,
                )
            raise

        self._result_cache[cache_key] = ResultCacheEntry(cached_at=now, result=result)
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
            statement = self._quarterly_income_fetcher(symbol)
        except Exception as exc:  # noqa: BLE001 - upstream shapes vary
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc

        if statement is None:
            raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR)

        points = build_profitability_points(statement, quarters=requested_quarters)
        as_of = points[-1].period_end if points else None
        return TaiwanProfitabilityResult(
            stock_id=stock.id,
            quarters=requested_quarters,
            as_of=as_of,
            source=_SOURCE,
            stale=False,
            points=tuple(points),
        )


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
                fiscal_quarter=_fiscal_quarter_label(period_end),
                period_end=period_end,
                gross_margin_percent=margin_percent(gross, revenue),
                operating_margin_percent=margin_percent(operating, revenue),
                pretax_margin_percent=margin_percent(pretax, revenue),
                net_margin_percent=margin_percent(net, revenue),
                # Price alignment is optional; leave null unless a caller enriches later.
                quarter_end_close=None,
            )
        )

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

    if not isinstance(statement, Mapping):
        return {}

    # yfinance DataFrame-like: row label -> {Timestamp/str: value}
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

    for label, series in labeled_rows.items():
        field = _map_row_label(label)
        if field is None:
            continue
        for period, value in series.items():
            period_end = _period_end_key(period)
            if period_end is None or period_end not in columns:
                continue
            columns[period_end][field] = _as_float(value)
    return columns


def convert_ytd_to_quarterly(
    columns: dict[str, dict[str, float | None]],
) -> dict[str, dict[str, float | None]]:
    """Convert cumulative YTD columns within each fiscal year to quarterly increments."""
    if len(columns) < 2:
        return columns

    by_year: dict[int, list[str]] = {}
    for period in sorted(columns):
        year = int(period[:4])
        by_year.setdefault(year, []).append(period)

    converted: dict[str, dict[str, float | None]] = {}
    for _year, periods in by_year.items():
        previous: dict[str, float | None] = {
            "revenue": 0.0,
            "gross": 0.0,
            "operating": 0.0,
            "pretax": 0.0,
            "net": 0.0,
        }
        for period in periods:
            current = columns[period]
            quarter_values: dict[str, float | None] = {}
            for field in ("revenue", "gross", "operating", "pretax", "net"):
                current_value = current.get(field)
                previous_value = previous.get(field)
                if current_value is None:
                    quarter_values[field] = None
                elif previous_value is None:
                    quarter_values[field] = current_value
                else:
                    quarter_values[field] = current_value - previous_value
                if current_value is not None:
                    previous[field] = current_value
            converted[period] = quarter_values
    return converted


def _map_row_label(label: str) -> str | None:
    normalized = " ".join(label.strip().split())
    compact = normalized.replace(" ", "")
    checks = (
        ("revenue", _REVENUE_LABELS),
        ("gross", _GROSS_LABELS),
        ("operating", _OPERATING_LABELS),
        ("pretax", _PRETAX_LABELS),
        ("net", _NET_LABELS),
    )
    for field, labels in checks:
        for candidate in labels:
            if normalized.lower() == candidate.lower() or compact.lower() == candidate.replace(" ", "").lower():
                return field
    return None


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


def _fiscal_quarter_label(period_end: str) -> str:
    year = int(period_end[:4])
    month = int(period_end[5:7])
    quarter = (month - 1) // 3 + 1
    return f"{year}Q{quarter}"


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numeric != numeric:  # NaN
        return None
    return numeric


def _normalize_quarters(value: int) -> int:
    if value not in _ALLOWED_QUARTERS:
        raise ValueError(f"Unsupported profitability quarters: {value}")
    return value


def _default_quarterly_income_fetcher(symbol: str) -> Mapping[Any, Mapping[Any, Any]] | None:
    try:
        import yfinance as yf  # pylint: disable=import-outside-toplevel
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(_UPSTREAM_UNAVAILABLE_ERROR) from exc

    for suffix in (".TW", ".TWO"):
        ticker = yf.Ticker(f"{symbol}{suffix}")
        try:
            frame = ticker.quarterly_income_stmt
        except Exception:  # noqa: BLE001 - try next suffix
            continue
        if frame is None:
            continue
        try:
            if getattr(frame, "empty", False):
                continue
            # Convert DataFrame to nested mapping: index(row) -> columns(period) -> value
            return {
                str(index): {
                    column: (None if _is_na(value) else value)
                    for column, value in row.items()
                }
                for index, row in frame.to_dict(orient="index").items()
            }
        except Exception:  # noqa: BLE001
            continue
    return None


def _is_na(value: object) -> bool:
    try:
        import math

        if isinstance(value, float) and math.isnan(value):
            return True
    except Exception:  # noqa: BLE001
        return False
    try:
        import pandas as pd  # pylint: disable=import-outside-toplevel

        return bool(pd.isna(value))
    except Exception:  # noqa: BLE001
        return False


tw_profitability_service = TwProfitabilityService()
