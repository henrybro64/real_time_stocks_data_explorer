from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import math
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError, HttpMethod, fetch_json, fetch_text

if TYPE_CHECKING:
    from app.services.quote_service import QuoteService

TaiwanMarket = Literal["TWSE", "TPEx", "UNKNOWN"]

_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_RETRIES = 1
_DEFAULT_CACHE_TTL = timedelta(hours=12)
# Empty/no-data responses are often temporary (pre-publish / holiday probes).
# Never pin them for the full dataset TTL.
_NO_DATA_CACHE_TTL = timedelta(minutes=2)
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}
_TWSE_HOSTS = {"www.twse.com.tw", "openapi.twse.com.tw", "mis.twse.com.tw", "isin.twse.com.tw"}
_TPEX_HOSTS = {"www.tpex.org.tw"}
_EMPTY_MARKERS = {"", "-", "--", "---", "----", "n/a", "na", "null", "none"}
_NO_DATA_STAT_MARKERS = (
    "sorry",
    "no data",
    "nodata",
    "empty",
    "沒有符合",
    "查無",
    "無資料",
)


@dataclass(frozen=True)
class _CacheEntry:
    cached_at: datetime
    payload: object


class TaiwanMarketDataClient:
    """Official TWSE/TPEx fetch helpers with host allowlisting and in-memory caching."""

    def __init__(self) -> None:
        self._cache: dict[
            tuple[
                str,
                str,
                str,
                str,
                str | None,
                tuple[tuple[str, str], ...],
                tuple[tuple[str, str], ...],
                tuple[tuple[str, str], ...],
            ],
            _CacheEntry,
        ] = {}

    def fetch_twse_data(
        self,
        url: str,
        *,
        method: HttpMethod = "GET",
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retries: int = _DEFAULT_RETRIES,
        cache_ttl: timedelta = _DEFAULT_CACHE_TTL,
        operation_name: str = "taiwan_market_data_twse",
        resource_id: str | None = None,
    ) -> object:
        self._validate_host(url, allowed_hosts=_TWSE_HOSTS)
        return self._fetch(
            url,
            provider_name="twse",
            response_kind="json",
            method=method,
            params=params,
            data=data,
            headers=headers,
            timeout_seconds=timeout_seconds,
            retries=retries,
            cache_ttl=cache_ttl,
            operation_name=operation_name,
            resource_id=resource_id,
        )

    def fetch_tpex_data(
        self,
        url: str,
        *,
        method: HttpMethod = "GET",
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retries: int = _DEFAULT_RETRIES,
        cache_ttl: timedelta = _DEFAULT_CACHE_TTL,
        operation_name: str = "taiwan_market_data_tpex",
        resource_id: str | None = None,
    ) -> object:
        self._validate_host(url, allowed_hosts=_TPEX_HOSTS)
        return self._fetch(
            url,
            provider_name="tpex",
            response_kind="json",
            method=method,
            params=params,
            data=data,
            headers=headers,
            timeout_seconds=timeout_seconds,
            retries=retries,
            cache_ttl=cache_ttl,
            operation_name=operation_name,
            resource_id=resource_id,
        )

    def fetch_twse_text(
        self,
        url: str,
        *,
        method: HttpMethod = "GET",
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retries: int = _DEFAULT_RETRIES,
        cache_ttl: timedelta = _DEFAULT_CACHE_TTL,
        encoding: str | None = None,
        operation_name: str = "taiwan_market_data_twse_text",
        resource_id: str | None = None,
    ) -> str:
        self._validate_host(url, allowed_hosts=_TWSE_HOSTS)
        payload = self._fetch(
            url,
            provider_name="twse",
            response_kind="text",
            method=method,
            params=params,
            data=data,
            headers=headers,
            timeout_seconds=timeout_seconds,
            retries=retries,
            cache_ttl=cache_ttl,
            operation_name=operation_name,
            resource_id=resource_id,
            encoding=encoding,
        )
        return str(payload)

    def fetch_tpex_text(
        self,
        url: str,
        *,
        method: HttpMethod = "GET",
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retries: int = _DEFAULT_RETRIES,
        cache_ttl: timedelta = _DEFAULT_CACHE_TTL,
        encoding: str | None = None,
        operation_name: str = "taiwan_market_data_tpex_text",
        resource_id: str | None = None,
    ) -> str:
        self._validate_host(url, allowed_hosts=_TPEX_HOSTS)
        payload = self._fetch(
            url,
            provider_name="tpex",
            response_kind="text",
            method=method,
            params=params,
            data=data,
            headers=headers,
            timeout_seconds=timeout_seconds,
            retries=retries,
            cache_ttl=cache_ttl,
            operation_name=operation_name,
            resource_id=resource_id,
            encoding=encoding,
        )
        return str(payload)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _fetch(
        self,
        url: str,
        *,
        provider_name: str,
        response_kind: Literal["json", "text"],
        method: HttpMethod,
        params: dict[str, str] | None,
        data: dict[str, str] | None,
        headers: dict[str, str] | None,
        timeout_seconds: float,
        retries: int,
        cache_ttl: timedelta,
        operation_name: str,
        resource_id: str | None,
        encoding: str | None = None,
    ) -> object:
        merged_headers = dict(_DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)

        cache_key = (
            provider_name,
            response_kind,
            method,
            url,
            encoding,
            tuple(sorted((params or {}).items())),
            tuple(sorted((data or {}).items())),
            tuple(sorted(merged_headers.items())),
        )
        now = datetime.now(UTC)
        cached = self._cache.get(cache_key)
        effective_ttl = cache_ttl
        if cached is not None:
            if is_taiwan_no_data_payload(cached.payload):
                effective_ttl = min(cache_ttl, _NO_DATA_CACHE_TTL)
            if now - cached.cached_at < effective_ttl:
                return cached.payload

        try:
            if response_kind == "text":
                payload: object = fetch_text(
                    url,
                    method=method,
                    params=params,
                    data=data,
                    headers=merged_headers,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                    encoding=encoding,
                    provider_name=provider_name,
                    operation_name=operation_name,
                    resource_id=resource_id,
                )
            else:
                payload = fetch_json(
                    url,
                    method=method,
                    params=params,
                    data=data,
                    headers=merged_headers,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                    provider_name=provider_name,
                    operation_name=operation_name,
                    resource_id=resource_id,
                )
        except HttpFetchError:
            # Propagate structured upstream failures so callers can return empty/error
            # responses without crashing the process.
            raise

        # Empty/no-data payloads are cached with a short TTL on read so pre-publish
        # probes do not block later fetches after the official file is available.
        self._cache[cache_key] = _CacheEntry(cached_at=now, payload=payload)
        return payload

    @staticmethod
    def _validate_host(url: str, *, allowed_hosts: set[str]) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host not in allowed_hosts:
            raise ValueError(f"Unsupported Taiwan market data host: {host or url}")


taiwan_market_data_client = TaiwanMarketDataClient()


def fetch_twse_data(
    url: str,
    *,
    method: HttpMethod = "GET",
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = _DEFAULT_RETRIES,
    cache_ttl: timedelta = _DEFAULT_CACHE_TTL,
    operation_name: str = "taiwan_market_data_twse",
    resource_id: str | None = None,
) -> object:
    return taiwan_market_data_client.fetch_twse_data(
        url,
        method=method,
        params=params,
        data=data,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        cache_ttl=cache_ttl,
        operation_name=operation_name,
        resource_id=resource_id,
    )


def fetch_tpex_data(
    url: str,
    *,
    method: HttpMethod = "GET",
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = _DEFAULT_RETRIES,
    cache_ttl: timedelta = _DEFAULT_CACHE_TTL,
    operation_name: str = "taiwan_market_data_tpex",
    resource_id: str | None = None,
) -> object:
    return taiwan_market_data_client.fetch_tpex_data(
        url,
        method=method,
        params=params,
        data=data,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        cache_ttl=cache_ttl,
        operation_name=operation_name,
        resource_id=resource_id,
    )


def require_taiwan_stock(
    stock_id: str,
    *,
    quote_source: QuoteService,
    not_found_error: str,
    non_taiwan_error: str,
) -> StockRecord:
    stock = quote_source.get_stock(stock_id)
    if stock is None:
        raise ValueError(not_found_error)
    if stock.market != "TW":
        raise ValueError(non_taiwan_error)
    return stock


def require_twse_or_tpex_symbol(
    stock: StockRecord,
    *,
    quote_source: QuoteService,
    unknown_market_error: str,
) -> tuple[Literal["TWSE", "TPEx"], str]:
    """Resolve a TW stock to an official board plus normalized symbol, or raise."""
    market = resolve_taiwan_market(
        stock.id,
        quote_source=quote_source,
        known_stocks=[stock],
    )
    if market != "TWSE" and market != "TPEx":
        raise ValueError(unknown_market_error)

    symbol = normalize_taiwan_symbol(stock.symbol) or normalize_taiwan_symbol(stock.id)
    if symbol is None:
        raise ValueError(unknown_market_error)
    return market, symbol


def resolve_taiwan_market(
    symbol_or_stock_id: str,
    *,
    quote_source: QuoteService | None = None,
    known_stocks: Iterable[StockRecord] | None = None,
) -> TaiwanMarket:
    """Resolve TWSE vs TPEx from existing metadata only. Never guess from code patterns."""
    if not isinstance(symbol_or_stock_id, str) or not symbol_or_stock_id.strip():
        return "UNKNOWN"

    raw = symbol_or_stock_id.strip()
    if ":" in raw:
        market_prefix = raw.split(":", 1)[0].strip().upper()
        if market_prefix and market_prefix != "TW":
            return "UNKNOWN"

    stock_id = _as_tw_stock_id(raw)
    exact_matches: list[StockRecord] = []

    if known_stocks is not None:
        exact_matches.extend(
            stock
            for stock in known_stocks
            if stock.market == "TW"
            and (
                stock.id == stock_id
                or normalize_taiwan_symbol(stock.symbol) == normalize_taiwan_symbol(raw)
            )
        )
        # Prefer already-provided metadata and avoid cold-cache remote catalog lookups.
        if exact_matches:
            return _resolve_from_matches(exact_matches)

    quote_lookup = quote_source or _default_quote_source()
    stock = quote_lookup.get_stock(stock_id)
    if stock is not None and stock.market == "TW":
        exact_matches.append(stock)

    if exact_matches:
        return _resolve_from_matches(exact_matches)

    symbol = normalize_taiwan_symbol(raw)
    if symbol is None:
        return "UNKNOWN"

    catalog_matches = [
        stock
        for stock in quote_lookup.list_stocks()
        if stock.market == "TW" and normalize_taiwan_symbol(stock.symbol) == symbol
    ]
    if not catalog_matches:
        return "UNKNOWN"
    return _resolve_from_matches(catalog_matches)


def normalize_taiwan_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped or stripped.lower() in _EMPTY_MARKERS:
        return None

    parts = [segment for segment in stripped.replace("-", "/").split("/") if segment]
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        try:
            first = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
            year = first + 1911 if first < 1911 else first
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    digits = "".join(ch for ch in stripped if ch.isdigit())
    if len(digits) == 8:
        try:
            return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8])).isoformat()
        except ValueError:
            return None
    if len(digits) == 7:
        try:
            return date(1911 + int(digits[0:3]), int(digits[3:5]), int(digits[5:7])).isoformat()
        except ValueError:
            return None
    return None


def parse_taiwan_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if not isinstance(value, str):
        return None

    cleaned = value.strip().replace(",", "").replace("，", "")
    if cleaned.lower() in _EMPTY_MARKERS:
        return None

    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"

    try:
        numeric = float(cleaned)
    except ValueError:
        return None
    return numeric if math.isfinite(numeric) else None


def shares_to_lots(value: object, *, symbol: str | None = None) -> float | None:
    """Convert share/beneficiary-unit counts to 張 using ``taiwan_shares_per_lot``.

    Callers must not hardcode ``/1000``; pass the Taiwan symbol so foreign-currency
    ETF lot sizes are applied. Returns ``None`` when ``value`` cannot be parsed.
    """
    shares = parse_taiwan_number(value)
    if shares is None:
        return None
    return shares / float(taiwan_shares_per_lot(symbol))


def net_shares_to_lots(value: object, *, symbol: str | None = None) -> float | None:
    """``shares_to_lots`` rounded for display/payload use. ``None`` stays ``None``."""
    lots = shares_to_lots(value, symbol=symbol)
    if lots is None:
        return None
    return round(lots, 3)


def taiwan_shares_per_lot(symbol: str | None = None) -> int:
    """Return shares (受益權單位) per 張 for Taiwan instruments.

    Rules (aligned with common TWSE/TPEx dual-currency / 加掛 ETF symbol conventions):

    - Default equities and most instruments: ``1000``
    - Foreign-currency (加掛) ETF symbols whose **sixth** character is ``K``
      (equity ETF) or ``C`` (bond ETF): ``100``

    This is a **heuristic**, not a product registry. Limitations:

    - Issuers may select another multiple of 100 for some 加掛 ETFs; when the
      exact product lot size is unknown we still use ``100`` for ``K``/``C``
      symbols (the common dual-ETF case) rather than guessing further.
    - Other FX ETF suffix letters (for example leverage ``M`` / inverse ``S``)
      keep the default ``1000`` until product-specific rules are added.
    - Does **not** infer TWSE vs TPEx from digit patterns; only the symbol text
      (after normalizing prefixes/suffixes) is inspected.
    - Detail API responses also expose ``sharesPerLot`` so clients can see which
      divisor was applied without reconstructing the rule.
    """
    normalized = normalize_taiwan_symbol(symbol) if symbol is not None else None
    if normalized is not None and len(normalized) >= 6 and normalized[5] in {"K", "C"}:
        return 100
    return 1000


def normalize_taiwan_symbol(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    if not cleaned:
        return None
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[1]
    if cleaned.endswith(".TWO"):
        cleaned = cleaned[:-4]
    elif cleaned.endswith(".TW"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    return cleaned or None


def taiwan_field_index(fields: Sequence[object], *labels: str) -> int | None:
    normalized_labels = {label.strip() for label in labels if label.strip()}
    if not normalized_labels:
        return None
    for index, field in enumerate(fields):
        if str(field).strip() in normalized_labels:
            return index
    return None


def taiwan_field_indices(fields: Sequence[object], *labels: str) -> list[int]:
    """Return every matching field index (useful when official tables repeat labels)."""
    normalized_labels = {label.strip() for label in labels if label.strip()}
    if not normalized_labels:
        return []
    return [
        index
        for index, field in enumerate(fields)
        if str(field).strip() in normalized_labels
    ]


def taiwan_row_value(
    row: Sequence[object],
    fields: Sequence[object],
    *labels: str,
) -> object | None:
    index = taiwan_field_index(fields, *labels)
    if index is None or index >= len(row):
        return None
    return row[index]


def taiwan_row_value_at(
    row: Sequence[object],
    index: int | None,
) -> object | None:
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


def is_taiwan_no_data_payload(payload: object) -> bool:
    """Detect holiday / closed-day / empty official payloads without raising."""
    if payload is None:
        return True
    if isinstance(payload, str):
        return _is_no_data_text(payload)
    if not isinstance(payload, dict):
        return False
    if _dict_has_no_data_stat_marker(payload):
        return True
    return _dict_has_empty_data_or_tables(payload)


def _is_no_data_text(payload: str) -> bool:
    stripped = payload.strip()
    lowered = stripped.lower()
    return not stripped or any(marker in lowered for marker in _NO_DATA_STAT_MARKERS)


def _dict_has_no_data_stat_marker(payload: dict) -> bool:
    for key in ("stat", "status", "message", "msg"):
        raw = payload.get(key)
        if _text_has_no_data_marker(raw):
            return True
    return False


def _text_has_no_data_marker(raw: object) -> bool:
    if not isinstance(raw, str):
        return False
    lowered = raw.strip().lower()
    return any(marker in lowered for marker in _NO_DATA_STAT_MARKERS)


def _dict_has_empty_data_or_tables(payload: dict) -> bool:
    data = payload.get("data")
    tables = payload.get("tables")
    if _is_empty_list(data) and tables is None:
        return True
    if _is_empty_list(tables) and data is None:
        return True
    return data is None and _tables_are_nested_empty(tables)


def _is_empty_list(value: object) -> bool:
    return isinstance(value, list) and len(value) == 0


def _tables_are_nested_empty(tables: object) -> bool:
    if not isinstance(tables, list) or not tables:
        return False
    return all(_table_has_empty_data(table) for table in tables)


def _table_has_empty_data(table: object) -> bool:
    if not isinstance(table, dict):
        return False
    rows = table.get("data")
    return isinstance(rows, list) and len(rows) == 0


def _resolve_from_matches(matches: Iterable[StockRecord]) -> TaiwanMarket:
    resolved = {
        _stock_exchange_to_market(stock.exchange)
        for stock in matches
        if _stock_exchange_to_market(stock.exchange) != "UNKNOWN"
    }
    if len(resolved) == 1:
        return resolved.pop()
    return "UNKNOWN"


def _stock_exchange_to_market(exchange: str | None) -> TaiwanMarket:
    if not exchange:
        return "UNKNOWN"
    normalized = exchange.strip().upper()
    if normalized in {"TWSE", "上市"}:
        return "TWSE"
    if normalized in {"TPEX", "OTC", "上櫃"}:
        return "TPEx"
    return "UNKNOWN"


def _as_tw_stock_id(symbol_or_stock_id: str) -> str:
    raw = symbol_or_stock_id.strip()
    if raw.upper().startswith("TW:"):
        symbol = normalize_taiwan_symbol(raw)
        return f"TW:{symbol}" if symbol is not None else raw.upper()
    symbol = normalize_taiwan_symbol(raw)
    return f"TW:{symbol}" if symbol is not None else raw.upper()


def _default_quote_source() -> QuoteService:
    from app.services.quote_service import quote_service

    return quote_service
