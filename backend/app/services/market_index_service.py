from __future__ import annotations

import json
import logging
import ssl
from dataclasses import dataclass, replace
from datetime import datetime
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.services.market_clock import is_market_open

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 8

# urllib may raise RemoteDisconnected (not a URLError) when the peer resets TLS.
_FETCH_ERRORS: tuple[type[BaseException], ...] = (
    HTTPError,
    URLError,
    RemoteDisconnected,
    ConnectionError,
    TimeoutError,
    ssl.SSLError,
    ValueError,
)
_STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
_TWSE_INDEX_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TW_YAHOO_SYMBOL = "^TWII"
_TZ_TAIPEI = "Asia/Taipei"
_TZ_NEW_YORK = "America/New_York"
_TZ_UTC = "UTC"

IndexStatus = Literal["ok", "unavailable"]
IndexFetcher = Callable[[], "MarketIndexRecord | None"]


@dataclass(frozen=True)
class MarketIndexRecord:
    id: str
    market: str
    label: str
    value: float | None
    change: float | None
    change_percent: float | None
    timestamp: str
    is_market_open: bool
    status: IndexStatus = "ok"
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class _IndexSlot:
    id: str
    market: str
    label: str


def _resolve_catalog_path() -> Path:
    """Prefer the Docker-bundled copy, then the shared-contracts source in a repo checkout."""
    bundled = Path(__file__).resolve().parents[1] / "data" / "market_index_catalog.json"
    if bundled.is_file():
        return bundled
    shared = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "shared-contracts"
        / "marketIndexCatalog.json"
    )
    if shared.is_file():
        return shared
    raise FileNotFoundError(
        "market index catalog not found (expected app/data or packages/shared-contracts)",
    )


def _load_market_index_catalog() -> tuple[
    tuple[_IndexSlot, ...],
    dict[str, str],
    dict[str, str | None],
    dict[str, str],
]:
    payload = json.loads(_resolve_catalog_path().read_text(encoding="utf-8"))
    slots = tuple(
        _IndexSlot(id=entry["id"], market=entry["market"], label=entry["label"])
        for entry in payload["indices"]
    )
    yahoo_symbols = {str(key): str(value) for key, value in payload["usYahooSymbols"].items()}
    stooq_symbols = {
        str(key): (None if value is None else str(value))
        for key, value in payload["usStooqSymbols"].items()
    }
    crypto_yahoo_symbols = {
        str(key): str(value) for key, value in payload["cryptoYahooSymbols"].items()
    }
    return slots, yahoo_symbols, stooq_symbols, crypto_yahoo_symbols


(
    _INDEX_CATALOG,
    _US_YAHOO_SYMBOLS,
    _US_STOOQ_SYMBOLS,
    _CRYPTO_YAHOO_SYMBOLS,
) = _load_market_index_catalog()
_TW_SLOT = next(slot for slot in _INDEX_CATALOG if slot.market == "TW")
_TW_INDEX_ID = _TW_SLOT.id
_TW_INDEX_LABEL = _TW_SLOT.label


class MarketIndexService:
    def __init__(
        self,
        *,
        twse_fetcher: IndexFetcher | None = None,
        yahoo_taiwan_fetcher: IndexFetcher | None = None,
        stooq_fetcher: Callable[[str, str], MarketIndexRecord | None] | None = None,
        yahoo_us_fetcher: Callable[[str, str, str], MarketIndexRecord | None] | None = None,
        yahoo_crypto_fetcher: Callable[[str, str, str], MarketIndexRecord | None] | None = None,
    ) -> None:
        self._twse_fetcher = twse_fetcher or _fetch_twse_index
        self._yahoo_taiwan_fetcher = yahoo_taiwan_fetcher or (
            lambda: _fetch_yahoo_index(
                _TW_YAHOO_SYMBOL,
                _TW_INDEX_ID,
                _TW_INDEX_LABEL,
                market="TW",
                timezone_name=_TZ_TAIPEI,
            )
        )
        self._stooq_fetcher = stooq_fetcher or _fetch_stooq_index
        self._yahoo_us_fetcher = yahoo_us_fetcher or (
            lambda yahoo_symbol, record_id, label: _fetch_yahoo_index(
                yahoo_symbol,
                record_id,
                label,
                market="US",
                timezone_name=_TZ_NEW_YORK,
            )
        )
        if yahoo_crypto_fetcher is not None:
            self._yahoo_crypto_fetcher = yahoo_crypto_fetcher
        elif yahoo_us_fetcher is not None:
            # Preserve dependency injection for callers/tests that replace Yahoo.
            def injected_crypto_fetcher(
                yahoo_symbol: str,
                record_id: str,
                label: str,
            ) -> MarketIndexRecord | None:
                record = yahoo_us_fetcher(yahoo_symbol, record_id, label)
                if record is None:
                    return None
                return replace(record, market="CRYPTO", is_market_open=True)

            self._yahoo_crypto_fetcher = injected_crypto_fetcher
        else:
            self._yahoo_crypto_fetcher = (
                lambda yahoo_symbol, record_id, label: _fetch_yahoo_index(
                    yahoo_symbol,
                    record_id,
                    label,
                    market="CRYPTO",
                    timezone_name=_TZ_UTC,
                )
            )

    def get_latest(self) -> list[MarketIndexRecord]:
        """Always return the fixed dashboard catalog (never omit a market slot)."""
        records: list[MarketIndexRecord] = []
        for slot in _INDEX_CATALOG:
            if slot.market == "TW":
                records.append(self._resolve_taiwan(slot))
            elif slot.market == "US":
                records.append(self._resolve_us(slot))
            elif slot.market == "CRYPTO":
                records.append(self._resolve_crypto(slot))
            else:
                records.append(_unavailable_index(slot, reason="unsupported_market"))
        return records

    def _resolve_taiwan(self, slot: _IndexSlot) -> MarketIndexRecord:
        try:
            record = self._twse_fetcher()
        except Exception as exc:  # noqa: BLE001 - never drop the dashboard slot
            logger.warning(
                "Taiwan index provider raised; trying fallback",
                extra={
                    "event": "market_index.twse.error",
                    "provider": "twse_mis",
                    "index_id": slot.id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            record = None
        if record is not None:
            return record

        try:
            fallback = self._yahoo_taiwan_fetcher()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Taiwan Yahoo fallback raised",
                extra={
                    "event": "market_index.yahoo_tw.error",
                    "provider": "yahoo_finance",
                    "index_id": slot.id,
                    "yahoo_symbol": _TW_YAHOO_SYMBOL,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            fallback = None
        if fallback is not None:
            logger.warning(
                "Taiwan index using Yahoo fallback after TWSE failure",
                extra={
                    "event": "market_index.twse.fallback_used",
                    "provider": "yahoo_finance",
                    "index_id": slot.id,
                    "yahoo_symbol": _TW_YAHOO_SYMBOL,
                },
            )
            return fallback

        logger.warning(
            "Taiwan index unavailable after primary and fallback providers failed",
            extra={
                "event": "market_index.twse.unavailable",
                "provider": "twse_mis",
                "fallback_provider": "yahoo_finance",
                "index_id": slot.id,
                "request_url": _TWSE_INDEX_URL,
            },
        )
        return _unavailable_index(
            slot,
            reason="taiwan_provider_unavailable",
        )

    def _resolve_us(self, slot: _IndexSlot) -> MarketIndexRecord:
        stooq_symbol = _US_STOOQ_SYMBOLS.get(slot.id)
        record = None
        if stooq_symbol is not None:
            try:
                record = self._stooq_fetcher(stooq_symbol, slot.label)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "US Stooq index fetch raised; trying Yahoo fallback",
                    extra={
                        "event": "market_index.stooq.error",
                        "provider": "stooq",
                        "index_id": slot.id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                record = None
            if record is not None:
                # Always expose the catalog id (Stooq symbol may differ in future).
                if record.id != slot.id:
                    return replace(record, id=slot.id, label=slot.label)
                return record

        yahoo_symbol = _US_YAHOO_SYMBOLS[slot.id]
        try:
            fallback = self._yahoo_us_fetcher(yahoo_symbol, slot.id, slot.label)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "US Yahoo index fallback raised",
                extra={
                    "event": "market_index.yahoo_us.error",
                    "provider": "yahoo_finance",
                    "index_id": slot.id,
                    "yahoo_symbol": yahoo_symbol,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            fallback = None
        if fallback is not None:
            if stooq_symbol is not None:
                logger.warning(
                    "US index using Yahoo fallback after Stooq failure",
                    extra={
                        "event": "market_index.stooq.fallback_used",
                        "provider": "yahoo_finance",
                        "index_id": slot.id,
                        "yahoo_symbol": yahoo_symbol,
                    },
                )
            return fallback

        logger.warning(
            "US index unavailable after providers failed",
            extra={
                "event": "market_index.us.unavailable",
                "provider": "stooq" if stooq_symbol is not None else "yahoo_finance",
                "fallback_provider": "yahoo_finance" if stooq_symbol is not None else None,
                "index_id": slot.id,
                "request_url": _STOOQ_QUOTE_URL if stooq_symbol is not None else _YAHOO_CHART_URL,
            },
        )
        return _unavailable_index(slot, reason="us_provider_unavailable")

    def _resolve_crypto(self, slot: _IndexSlot) -> MarketIndexRecord:
        yahoo_symbol = _CRYPTO_YAHOO_SYMBOLS[slot.id]
        try:
            record = self._yahoo_crypto_fetcher(yahoo_symbol, slot.id, slot.label)
        except Exception as exc:  # noqa: BLE001 - never drop the dashboard slot
            logger.warning(
                "Crypto Yahoo quote fetch raised",
                extra={
                    "event": "market_index.yahoo_crypto.error",
                    "provider": "yahoo_finance",
                    "index_id": slot.id,
                    "yahoo_symbol": yahoo_symbol,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            record = None
        if record is not None:
            return record

        logger.warning(
            "Crypto quote unavailable",
            extra={
                "event": "market_index.crypto.unavailable",
                "provider": "yahoo_finance",
                "index_id": slot.id,
                "yahoo_symbol": yahoo_symbol,
                "request_url": _YAHOO_CHART_URL,
            },
        )
        return _unavailable_index(slot, reason="crypto_provider_unavailable")


def _unavailable_index(slot: _IndexSlot, *, reason: str) -> MarketIndexRecord:
    timezone_name = {
        "TW": _TZ_TAIPEI,
        "US": _TZ_NEW_YORK,
        "CRYPTO": _TZ_UTC,
    }.get(slot.market, _TZ_UTC)
    now = datetime.now(ZoneInfo(timezone_name))
    return MarketIndexRecord(
        id=slot.id,
        market=slot.market,
        label=slot.label,
        value=None,
        change=None,
        change_percent=None,
        timestamp=now.isoformat(),
        is_market_open=slot.market == "CRYPTO" or is_market_open(slot.market, now),
        status="unavailable",
        unavailable_reason=reason,
    )


def _fetch_stooq_index(stooq_symbol: str, label: str) -> MarketIndexRecord | None:
    try:
        payload = _load_text(
            _STOOQ_QUOTE_URL,
            {"s": stooq_symbol.lower(), "i": "d"},
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/csv,text/plain,*/*",
            },
        )
    except _FETCH_ERRORS as exc:
        _log_fetch_failure(
            event="market_index.stooq.fetch_failed",
            provider="stooq",
            index_id=stooq_symbol,
            request_url=_STOOQ_QUOTE_URL,
            exc=exc,
        )
        return None

    rows = payload.strip().splitlines()
    if not rows:
        _log_parse_failure(
            event="market_index.stooq.empty_payload",
            provider="stooq",
            index_id=stooq_symbol,
            request_url=_STOOQ_QUOTE_URL,
            detail="empty_csv",
        )
        return None

    parts = [part.strip() for part in rows[0].split(",")]
    if len(parts) < 8:
        _log_parse_failure(
            event="market_index.stooq.malformed_payload",
            provider="stooq",
            index_id=stooq_symbol,
            request_url=_STOOQ_QUOTE_URL,
            detail="csv_columns_lt_8",
        )
        return None

    date_text, time_text = parts[1], parts[2]
    open_value = _parse_float(parts[3])
    close_value = _parse_float(parts[6])
    if close_value is None:
        _log_parse_failure(
            event="market_index.stooq.malformed_payload",
            provider="stooq",
            index_id=stooq_symbol,
            request_url=_STOOQ_QUOTE_URL,
            detail="missing_close",
        )
        return None

    previous_close = open_value if open_value is not None else close_value
    change = close_value - previous_close
    change_percent = (change / previous_close * 100) if previous_close else 0.0
    quote_time = _parse_stooq_timestamp(date_text, time_text, _TZ_NEW_YORK)
    market_now = datetime.now(ZoneInfo(_TZ_NEW_YORK))

    return MarketIndexRecord(
        id=stooq_symbol,
        market="US",
        label=label,
        value=round(close_value, 2),
        change=round(change, 2),
        change_percent=round(change_percent, 2),
        timestamp=quote_time.isoformat(),
        is_market_open=is_market_open("US", market_now),
    )


def _fetch_twse_index() -> MarketIndexRecord | None:
    try:
        payload = _load_json(
            _TWSE_INDEX_URL,
            {"ex_ch": "tse_t00.tw", "json": "1", "delay": "0"},
            {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://mis.twse.com.tw/stock/index.jsp",
            },
        )
    except _FETCH_ERRORS as exc:
        _log_fetch_failure(
            event="market_index.twse.fetch_failed",
            provider="twse_mis",
            index_id=_TW_INDEX_ID,
            request_url=_TWSE_INDEX_URL,
            exc=exc,
        )
        return None

    msg_array = payload.get("msgArray")
    if not isinstance(msg_array, list) or not msg_array:
        _log_parse_failure(
            event="market_index.twse.empty_payload",
            provider="twse_mis",
            index_id=_TW_INDEX_ID,
            request_url=_TWSE_INDEX_URL,
            detail="missing_or_empty_msgArray",
        )
        return None
    first = msg_array[0]
    if not isinstance(first, dict):
        _log_parse_failure(
            event="market_index.twse.malformed_payload",
            provider="twse_mis",
            index_id=_TW_INDEX_ID,
            request_url=_TWSE_INDEX_URL,
            detail="msgArray_item_not_object",
        )
        return None

    value = _parse_float(str(first.get("z") or ""))
    previous_close = _parse_float(str(first.get("y") or ""))
    raw_date = str(first.get("d") or "")
    raw_time = str(first.get("t") or "")
    if value is None or previous_close in {None, 0}:
        _log_parse_failure(
            event="market_index.twse.malformed_payload",
            provider="twse_mis",
            index_id=_TW_INDEX_ID,
            request_url=_TWSE_INDEX_URL,
            detail="missing_price_fields",
        )
        return None

    change = value - previous_close
    change_percent = change / previous_close * 100
    quote_time = _parse_twse_timestamp(raw_date, raw_time, _TZ_TAIPEI)
    market_now = datetime.now(ZoneInfo(_TZ_TAIPEI))

    return MarketIndexRecord(
        id=_TW_INDEX_ID,
        market="TW",
        label=_TW_INDEX_LABEL,
        value=round(value, 2),
        change=round(change, 2),
        change_percent=round(change_percent, 2),
        timestamp=quote_time.isoformat(),
        is_market_open=is_market_open("TW", market_now),
    )


def _fetch_yahoo_index(
    yahoo_symbol: str,
    record_id: str,
    label: str,
    *,
    market: str,
    timezone_name: str,
) -> MarketIndexRecord | None:
    path_symbol = quote(yahoo_symbol, safe="^-.")
    url = f"{_YAHOO_CHART_URL}/{path_symbol}?interval=1d&range=5d"
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    try:
        payload = _load_json_get(url, headers)
    except _FETCH_ERRORS as exc:
        _log_fetch_failure(
            event="market_index.yahoo.fetch_failed",
            provider="yahoo_finance",
            index_id=record_id,
            request_url=url,
            exc=exc,
        )
        return None

    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error"):
        _log_parse_failure(
            event="market_index.yahoo.malformed_payload",
            provider="yahoo_finance",
            index_id=record_id,
            request_url=url,
            detail="chart_error_or_missing",
        )
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        _log_parse_failure(
            event="market_index.yahoo.empty_payload",
            provider="yahoo_finance",
            index_id=record_id,
            request_url=url,
            detail="empty_result",
        )
        return None
    block = results[0]
    if not isinstance(block, dict):
        return None
    meta = block.get("meta")
    if not isinstance(meta, dict):
        return None

    last_price = meta.get("regularMarketPrice")
    prev_close = meta.get("chartPreviousClose")
    if not isinstance(last_price, (int, float)) or not isinstance(prev_close, (int, float)):
        _log_parse_failure(
            event="market_index.yahoo.malformed_payload",
            provider="yahoo_finance",
            index_id=record_id,
            request_url=url,
            detail="missing_price_fields",
        )
        return None
    if prev_close == 0:
        return None

    market_time = meta.get("regularMarketTime")
    tz = ZoneInfo(timezone_name)
    quote_time = (
        datetime.fromtimestamp(market_time, tz=tz)
        if isinstance(market_time, (int, float))
        else datetime.now(tz)
    )
    change = float(last_price) - float(prev_close)
    change_percent = change / float(prev_close) * 100
    market_now = datetime.now(tz)

    return MarketIndexRecord(
        id=record_id,
        market=market,
        label=label,
        value=round(float(last_price), 2),
        change=round(change, 2),
        change_percent=round(change_percent, 2),
        timestamp=quote_time.isoformat(),
        is_market_open=market == "CRYPTO" or is_market_open(market, market_now),
    )


def _log_fetch_failure(
    *,
    event: str,
    provider: str,
    index_id: str,
    request_url: str,
    exc: BaseException,
) -> None:
    status_code = getattr(exc, "code", None)
    logger.warning(
        "Market index provider fetch failed",
        extra={
            "event": event,
            "provider": provider,
            "index_id": index_id,
            "request_url": request_url,
            "http_status": status_code,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )


def _log_parse_failure(
    *,
    event: str,
    provider: str,
    index_id: str,
    request_url: str,
    detail: str,
) -> None:
    logger.warning(
        "Market index provider payload unusable",
        extra={
            "event": event,
            "provider": provider,
            "index_id": index_id,
            "request_url": request_url,
            "detail": detail,
        },
    )


def _load_json(url: str, params: dict[str, str], headers: dict[str, str]) -> dict:
    request = Request(f"{url}?{urlencode(params)}")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except URLError as exc:
        if not _should_retry_insecure(url, exc):
            raise
        insecure_context = ssl._create_unverified_context()  # noqa: SLF001
        with urlopen(
            request,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            context=insecure_context,
        ) as response:
            return json.load(response)


def _load_text(url: str, params: dict[str, str], headers: dict[str, str]) -> str:
    request = Request(f"{url}?{urlencode(params)}")
    for key, value in headers.items():
        request.add_header(key, value)
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="ignore")


def _load_json_get(url: str, headers: dict[str, str]) -> dict:
    request = Request(url)
    for key, value in headers.items():
        request.add_header(key, value)
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _parse_float(raw_value: str) -> float | None:
    value = raw_value.strip().replace(",", "")
    if not value or value in {"N/D", "-", "--"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_stooq_timestamp(raw_date: str, raw_time: str, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if (
        len(raw_date) == 8
        and len(raw_time) == 6
        and raw_date.isdigit()
        and raw_time.isdigit()
    ):
        try:
            return datetime.strptime(f"{raw_date}{raw_time}", "%Y%m%d%H%M%S").replace(
                tzinfo=tz
            )
        except ValueError:
            return datetime.now(tz)
    return datetime.now(tz)


def _parse_twse_timestamp(raw_date: str, raw_time: str, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    if len(raw_date) == 8 and raw_date.isdigit():
        try:
            if raw_time:
                return datetime.strptime(f"{raw_date}{raw_time}", "%Y%m%d%H:%M:%S").replace(
                    tzinfo=tz
                )
            return datetime.strptime(raw_date, "%Y%m%d").replace(
                hour=13,
                minute=30,
                tzinfo=tz,
            )
        except ValueError:
            return datetime.now(tz)
    return datetime.now(tz)


def _should_retry_insecure(url: str, exc: URLError) -> bool:
    if "twse.com.tw" not in url:
        return False
    return isinstance(exc.reason, ssl.SSLCertVerificationError)


market_index_service = MarketIndexService()
