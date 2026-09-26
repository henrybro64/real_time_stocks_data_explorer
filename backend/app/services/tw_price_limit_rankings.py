"""Market-wide Taiwan 漲停／跌停 rankings from TWSE MIS.

Each quote carries the session's reference price (``y``), limit-up price (``u``),
limit-down price (``w``), and last trade (``z``). Classification uses those
prices. Change percentage is stored for sorting and display only.

These rankings are live-only. They are not written to the rankings snapshot
table or the Azure SQL EOD archive. A failed MIS read returns an empty list
with an explicit warning; it never substitutes the hard-coded quote universe
or treats a mover as limit-up because the percentage looks like ±10%.

Cache keys are separate per ranking type and board. A shared classified-universe
entry avoids a second full MIS scan when the other list or board is requested.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from time import monotonic, sleep
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.schemas.rankings import RankingType
from app.services.http_client import HttpFetchError, fetch_json
from app.services.tw_price_limits import (
    PriceLimitStatus,
    classify_taiwan_price_limit,
    decimal_to_api_float,
    parse_price,
    parse_signed_decimal,
)

logger = logging.getLogger(__name__)

TwLimitBoard = Literal["all", "TWSE", "TPEx"]

TW_PRICE_LIMIT_SOURCE = "TWSE/TPEx MIS quotes with official daily limit prices."
TW_PRICE_LIMIT_FRESHNESS_OPEN = (
    "Market open; Taiwan limit-up and limit-down rankings use official MIS limit prices "
    "and are live-only."
)
TW_PRICE_LIMIT_FRESHNESS_CLOSED = (
    "Market closed; Taiwan limit-up and limit-down rankings use the latest MIS session "
    "limit prices and are live-only."
)
TW_PRICE_LIMIT_PARTIAL_WARNING = (
    "Some Taiwan quotes could not be classified. Those symbols are omitted instead of "
    "guessing from the change percentage."
)
TW_PRICE_LIMIT_UNAVAILABLE_WARNING = (
    "Taiwan limit-price quotes are temporarily unavailable. No symbols are listed, so a "
    "missing feed is not treated as a limit-up or limit-down ranking."
)
US_PRICE_LIMIT_UNSUPPORTED_REASON = (
    "U.S. markets do not use Taiwan daily price-limit rankings in this app."
)

_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_TWSE_SYMBOLS_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT84U"
_TPEX_SYMBOLS_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_BATCH_SIZE = 40
_MAX_WORKERS = 2
_MIS_REQUEST_GAP_SECONDS = 0.15
# Stay under the rankings request timeout so a cold MIS outage returns unavailable
# instead of draining every queued batch.
_SCAN_DEADLINE_SECONDS = 18.0
_SYMBOL_LIST_TTL = timedelta(hours=12)
_FAILURE_CACHE_SECONDS = 60
_CACHE_KEY_PREFIX = "rankings:tw-price-limit:v1:"
_CLASSIFIED_KEY = f"{_CACHE_KEY_PREFIX}classified"
_TAIPEI = ZoneInfo("Asia/Taipei")
_NO_TRADE_TOKENS = {"", "-", "--", "---", "----"}
_PRICE_LIMIT_TYPES = {RankingType.limit_up, RankingType.limit_down}


@dataclass(frozen=True)
class TwLimitRankingQuote:
    symbol: str
    name: str
    exchange: Literal["TWSE", "TPEx"]
    last_price: Decimal | None
    reference_price: Decimal | None
    limit_up_price: Decimal | None
    limit_down_price: Decimal | None
    change_percent: Decimal | None
    volume: int | None
    timestamp: str
    status: PriceLimitStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "exchange": self.exchange,
            "last_price": _decimal_text(self.last_price),
            "reference_price": _decimal_text(self.reference_price),
            "limit_up_price": _decimal_text(self.limit_up_price),
            "limit_down_price": _decimal_text(self.limit_down_price),
            "change_percent": _decimal_text(self.change_percent),
            "volume": self.volume,
            "timestamp": self.timestamp,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TwLimitRankingQuote:
        exchange = payload.get("exchange")
        status = payload.get("status")
        if exchange not in {"TWSE", "TPEx"} or status not in {
            "limit_up",
            "limit_down",
            "none",
            "unknown",
        }:
            raise ValueError("invalid cached Taiwan limit quote")
        volume = payload.get("volume")
        return cls(
            symbol=str(payload["symbol"]),
            name=str(payload.get("name") or payload["symbol"]),
            exchange=exchange,
            last_price=parse_price(payload.get("last_price")),
            reference_price=parse_price(payload.get("reference_price")),
            limit_up_price=parse_price(payload.get("limit_up_price")),
            limit_down_price=parse_price(payload.get("limit_down_price")),
            change_percent=parse_signed_decimal(payload.get("change_percent")),
            volume=int(volume) if isinstance(volume, int) else None,
            timestamp=str(payload.get("timestamp") or ""),
            status=status,
        )


@dataclass(frozen=True)
class TwPriceLimitFetchResult:
    quotes: list[TwLimitRankingQuote]
    partial: bool
    unavailable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "quotes": [quote.as_dict() for quote in self.quotes],
            "partial": self.partial,
            "unavailable": self.unavailable,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TwPriceLimitFetchResult:
        raw_quotes = payload.get("quotes")
        if not isinstance(raw_quotes, list):
            raise ValueError("invalid cached Taiwan limit ranking")
        return cls(
            quotes=[TwLimitRankingQuote.from_dict(item) for item in raw_quotes if isinstance(item, dict)],
            partial=bool(payload.get("partial")),
            unavailable=bool(payload.get("unavailable")),
        )


@dataclass(frozen=True)
class _ClassifiedUniverse:
    quotes: list[TwLimitRankingQuote]
    partial: bool
    unavailable: bool


@dataclass
class _MemoryEntry:
    fetched_at: float
    ttl_seconds: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class SymbolUniverse:
    symbols: dict[Literal["TWSE", "TPEx"], tuple[str, ...]]
    partial: bool = False


SymbolLoader = Callable[[], SymbolUniverse | dict[Literal["TWSE", "TPEx"], tuple[str, ...]]]
MisFetcher = Callable[[list[str]], list[dict[str, Any]]]


class TwPriceLimitRankingsSource:
    def __init__(
        self,
        *,
        symbol_loader: SymbolLoader | None = None,
        mis_fetcher: MisFetcher | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        self._symbol_loader = symbol_loader or _default_symbol_loader
        self._mis_fetcher = mis_fetcher or _default_mis_fetcher
        self._cache_ttl_seconds = max(
            1,
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else settings.rankings_external_cache_ttl_seconds,
        )
        self._memory: dict[str, _MemoryEntry] = {}
        self._symbol_cache: (
            tuple[float, tuple[dict[Literal["TWSE", "TPEx"], tuple[str, ...]], bool]] | None
        ) = None
        self._lock = threading.Lock()

    def fetch(
        self,
        ranking_type: RankingType,
        board: TwLimitBoard,
        now: datetime | None = None,
        *,
        force_refresh: bool = False,
    ) -> TwPriceLimitFetchResult:
        if ranking_type not in _PRICE_LIMIT_TYPES:
            raise ValueError(f"{ranking_type} is not a Taiwan price-limit ranking")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cache_key = _result_cache_key(ranking_type, board)
        if not force_refresh:
            cached = self._read(cache_key, current)
            if cached is not None:
                return TwPriceLimitFetchResult.from_dict(cached)

        classified = self._load_classified(current, force_refresh=force_refresh)
        result = _filter_classified(classified, ranking_type, board)
        ttl = (
            min(_FAILURE_CACHE_SECONDS, self._cache_ttl_seconds)
            if result.unavailable
            else self._cache_ttl_seconds
        )
        self._write(cache_key, result.as_dict(), current, ttl_seconds=ttl)
        return result

    def _load_classified(self, now: datetime, *, force_refresh: bool = False) -> _ClassifiedUniverse:
        if not force_refresh:
            cached = self._read(_CLASSIFIED_KEY, now)
            if cached is not None:
                restored = TwPriceLimitFetchResult.from_dict(cached)
                return _ClassifiedUniverse(
                    quotes=restored.quotes,
                    partial=restored.partial,
                    unavailable=restored.unavailable,
                )

        classified = self._fetch_classified(now)
        if not classified.unavailable:
            self._write(
                _CLASSIFIED_KEY,
                TwPriceLimitFetchResult(
                    quotes=classified.quotes,
                    partial=classified.partial,
                    unavailable=False,
                ).as_dict(),
                now,
                ttl_seconds=self._cache_ttl_seconds,
            )
        return classified

    def _fetch_classified(self, now: datetime) -> _ClassifiedUniverse:
        try:
            symbols, symbol_partial = self._symbols_by_board()
        except (HttpFetchError, TimeoutError, ValueError, OSError) as exc:
            logger.warning(
                "Taiwan price-limit symbol list was unavailable",
                extra={"event": "rankings.tw_price_limit.symbols_unavailable", "error": str(exc)},
            )
            return _ClassifiedUniverse(quotes=[], partial=False, unavailable=True)

        if not symbols["TWSE"] and not symbols["TPEx"]:
            return _ClassifiedUniverse(quotes=[], partial=False, unavailable=True)

        batches = _mis_batches(symbols)
        if not batches:
            return _ClassifiedUniverse(quotes=[], partial=False, unavailable=True)

        from app.services.providers.tw_provider import (  # pylint: disable=import-outside-toplevel
            mis_circuit_is_open,
        )

        if mis_circuit_is_open():
            logger.warning(
                "Taiwan price-limit scan skipped because the MIS circuit is open",
                extra={"event": "rankings.tw_price_limit.circuit_open"},
            )
            return _ClassifiedUniverse(quotes=[], partial=False, unavailable=True)

        quotes, failures, empty_successes, unclassified, timed_out = self._scan_batches(
            batches,
            now,
        )
        successful = len(batches) - failures
        # A 200 with no parsed quotes is a missing feed, not "nobody is at limit".
        # A deadline with nothing parsed is the same: do not publish an empty ranking.
        if (
            failures == len(batches)
            or (successful > 0 and empty_successes == successful and not quotes)
            or (timed_out and not quotes)
        ):
            return _ClassifiedUniverse(quotes=[], partial=False, unavailable=True)
        deduped = [
            quote
            for quote in _dedupe_quotes(quotes)
            if quote.status in {"limit_up", "limit_down"}
        ]
        return _ClassifiedUniverse(
            quotes=deduped,
            partial=(
                failures > 0
                or symbol_partial
                or unclassified > 0
                or timed_out
                or (empty_successes > 0 and len(quotes) > 0)
            ),
            unavailable=False,
        )

    def _scan_batches(
        self,
        batches: list[list[tuple[Literal["TWSE", "TPEx"], str]]],
        now: datetime,
    ) -> tuple[list[TwLimitRankingQuote], int, int, int, bool]:
        """Fetch MIS batches until the scan deadline. Do not wait out a stalled queue."""
        from app.services.providers.tw_provider import (  # pylint: disable=import-outside-toplevel
            mis_circuit_is_open,
        )

        quotes: list[TwLimitRankingQuote] = []
        failures = 0
        empty_successes = 0
        unclassified = 0
        timed_out = False
        pending = list(batches)
        inflight: dict[Future[tuple[list[TwLimitRankingQuote], int]], None] = {}
        workers = min(_MAX_WORKERS, len(batches))
        deadline = monotonic() + _SCAN_DEADLINE_SECONDS
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tw-limit")
        try:
            while pending or inflight:
                if mis_circuit_is_open() and pending:
                    timed_out = True
                    break
                while pending and len(inflight) < workers and monotonic() < deadline:
                    batch = pending.pop(0)
                    inflight[pool.submit(self._fetch_batch_paced, batch, now)] = None
                if not inflight:
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                done, _pending_futures = wait(
                    tuple(inflight),
                    timeout=remaining,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    timed_out = True
                    break
                for future in done:
                    del inflight[future]
                    try:
                        batch_quotes, batch_unclassified = future.result()
                    except (HttpFetchError, TimeoutError, ValueError, OSError):
                        failures += 1
                        continue
                    unclassified += batch_unclassified
                    if not batch_quotes and batch_unclassified == 0:
                        empty_successes += 1
                    quotes.extend(batch_quotes)
        finally:
            for future in inflight:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
        unfinished = len(pending) + len(inflight)
        if unfinished:
            failures += unfinished
            timed_out = True
        return quotes, failures, empty_successes, unclassified, timed_out

    def _fetch_batch_paced(
        self,
        batch: list[tuple[Literal["TWSE", "TPEx"], str]],
        now: datetime,
    ) -> tuple[list[TwLimitRankingQuote], int]:
        """Space MIS calls so a full-universe scan does not get disconnected."""
        with self._lock:
            previous = getattr(self, "_last_mis_at", 0.0)
            wait = _MIS_REQUEST_GAP_SECONDS - (monotonic() - previous)
            if wait > 0:
                sleep(wait)
            self._last_mis_at = monotonic()
        return self._fetch_batch(batch, now)

    def _fetch_batch(
        self,
        batch: list[tuple[Literal["TWSE", "TPEx"], str]],
        now: datetime,
    ) -> tuple[list[TwLimitRankingQuote], int]:
        requested = {symbol: exchange for exchange, symbol in batch}
        ex_chs = [
            f"{'otc' if exchange == 'TPEx' else 'tse'}_{symbol}.tw"
            for exchange, symbol in batch
        ]
        rows = self._mis_fetcher(ex_chs)
        quotes: list[TwLimitRankingQuote] = []
        seen: set[str] = set()
        unclassified = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = _quote_from_mis_row(row, requested, now)
            if parsed is None:
                continue
            seen.add(parsed.symbol)
            quotes.append(parsed)
            if parsed.status == "unknown":
                unclassified += 1
        if rows:
            unclassified += sum(1 for symbol in requested if symbol not in seen)
        return quotes, unclassified

    def _symbols_by_board(
        self,
    ) -> tuple[dict[Literal["TWSE", "TPEx"], tuple[str, ...]], bool]:
        now_mono = monotonic()
        with self._lock:
            cached = self._symbol_cache
            if cached is not None and now_mono - cached[0] < _SYMBOL_LIST_TTL.total_seconds():
                return cached[1]
        loaded = self._symbol_loader()
        partial = False
        if isinstance(loaded, SymbolUniverse):
            raw_symbols = loaded.symbols
            partial = loaded.partial
        else:
            raw_symbols = loaded
        normalized = {
            "TWSE": _listed_symbols(raw_symbols.get("TWSE", ())),
            "TPEx": _listed_symbols(raw_symbols.get("TPEx", ())),
        }
        with self._lock:
            self._symbol_cache = (monotonic(), (normalized, partial))
        return normalized, partial

    def _read(self, key: str, now: datetime) -> dict[str, Any] | None:
        now_epoch = now.timestamp()
        with self._lock:
            entry = self._memory.get(key)
            if entry is not None and now_epoch - entry.fetched_at < entry.ttl_seconds:
                return entry.payload
        raw = _redis_get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            fetched_at = float(payload["fetched_at"])
            ttl_seconds = int(payload["ttl_seconds"])
            body = payload["body"]
            if not isinstance(body, dict) or now_epoch - fetched_at >= ttl_seconds:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        with self._lock:
            self._memory[key] = _MemoryEntry(fetched_at, ttl_seconds, body)
        return body

    def _write(
        self,
        key: str,
        body: dict[str, Any],
        now: datetime,
        *,
        ttl_seconds: int,
    ) -> None:
        fetched_at = now.timestamp()
        with self._lock:
            self._memory[key] = _MemoryEntry(fetched_at, ttl_seconds, body)
        envelope = {"fetched_at": fetched_at, "ttl_seconds": ttl_seconds, "body": body}
        _redis_setex(key, ttl_seconds, json.dumps(envelope, ensure_ascii=False))


def _filter_classified(
    classified: _ClassifiedUniverse,
    ranking_type: RankingType,
    board: TwLimitBoard,
) -> TwPriceLimitFetchResult:
    if classified.unavailable:
        return TwPriceLimitFetchResult(quotes=[], partial=False, unavailable=True)
    wanted = ranking_type.value
    selected = [
        quote
        for quote in classified.quotes
        if quote.status == wanted and (board == "all" or quote.exchange == board)
    ]
    selected.sort(key=lambda quote: _sort_key(quote, ranking_type))
    return TwPriceLimitFetchResult(
        quotes=selected,
        partial=classified.partial,
        unavailable=False,
    )


def _sort_key(quote: TwLimitRankingQuote, ranking_type: RankingType) -> tuple[object, ...]:
    change = quote.change_percent
    if ranking_type == RankingType.limit_down:
        change_key: tuple[object, ...] = (
            change is None,
            change if change is not None else Decimal("1e18"),
        )
    else:
        change_key = (
            change is None,
            -(change if change is not None else Decimal("-1e18")),
        )
    return (*change_key, -(quote.volume if quote.volume is not None else -1), quote.symbol)


def _result_cache_key(ranking_type: RankingType, board: TwLimitBoard) -> str:
    return f"{_CACHE_KEY_PREFIX}{ranking_type.value}:{board}"


def _mis_batches(
    symbols: dict[Literal["TWSE", "TPEx"], tuple[str, ...]],
) -> list[list[tuple[Literal["TWSE", "TPEx"], str]]]:
    flat: list[tuple[Literal["TWSE", "TPEx"], str]] = []
    for exchange in ("TWSE", "TPEx"):
        for symbol in symbols[exchange]:
            flat.append((exchange, symbol))
    return [flat[index : index + _BATCH_SIZE] for index in range(0, len(flat), _BATCH_SIZE)]


def _listed_symbols(symbols: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Keep 4–6 digit codes. Tick fallback stays 4-digit; longer codes need MIS ``u``/``w``."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in symbols:
        code = str(raw).strip()
        if code.isdigit() and 4 <= len(code) <= 6 and code not in seen:
            seen.add(code)
            ordered.append(code)
    return tuple(ordered)


def _dedupe_quotes(quotes: list[TwLimitRankingQuote]) -> list[TwLimitRankingQuote]:
    by_symbol: dict[str, TwLimitRankingQuote] = {}
    for quote in quotes:
        by_symbol.setdefault(quote.symbol, quote)
    return list(by_symbol.values())


def _quote_from_mis_row(
    row: dict[str, Any],
    requested: dict[str, Literal["TWSE", "TPEx"]],
    now: datetime,
) -> TwLimitRankingQuote | None:
    symbol = str(row.get("c") or "").strip()
    if symbol not in requested:
        return None
    exchange = _exchange_from_row(row, requested[symbol])
    if exchange is None:
        return None
    traded = _is_traded_price(row.get("z"))
    last_price = parse_price(row.get("z")) if traded else None
    reference = parse_price(row.get("y"))
    limit_up = parse_price(row.get("u"))
    limit_down = parse_price(row.get("w"))
    status, reference, limit_up, limit_down = classify_taiwan_price_limit(
        last_price=last_price,
        reference_price=reference,
        limit_up_price_value=limit_up,
        limit_down_price_value=limit_down,
        traded=traded,
        symbol=symbol,
    )
    name = str(row.get("n") or symbol).strip() or symbol
    return TwLimitRankingQuote(
        symbol=symbol,
        name=name,
        exchange=exchange,
        last_price=last_price,
        reference_price=reference,
        limit_up_price=limit_up,
        limit_down_price=limit_down,
        change_percent=_change_percent(last_price, reference),
        volume=_volume_shares(row.get("v")),
        timestamp=_session_timestamp(row.get("d"), row.get("t"), now),
        status=status,
    )


def _exchange_from_row(
    row: dict[str, Any],
    requested: Literal["TWSE", "TPEx"] | None,
) -> Literal["TWSE", "TPEx"] | None:
    raw = str(row.get("ex") or "").strip().lower()
    if raw == "tse":
        return "TWSE"
    if raw == "otc":
        return "TPEx"
    return requested


def _is_traded_price(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if text.casefold() in _NO_TRADE_TOKENS:
        return False
    return parse_price(text) is not None


def _change_percent(last_price: Decimal | None, reference: Decimal | None) -> Decimal | None:
    if last_price is None or reference is None or reference == 0:
        return None
    return ((last_price - reference) / reference * Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _volume_shares(value: object) -> int | None:
    """MIS ``v`` is cumulative volume in lots. Match the existing quote provider."""
    lots = parse_price(value)
    if lots is None:
        return None
    return int(lots) * 1000


def _session_timestamp(raw_date: object, raw_time: object, now: datetime) -> str:
    date_text = "".join(ch for ch in str(raw_date or "") if ch.isdigit())
    time_text = str(raw_time or "").strip()
    if len(date_text) == 8 and len(time_text.split(":")) == 3:
        try:
            hour, minute, second = (int(part) for part in time_text.split(":"))
            stamp = datetime(
                int(date_text[0:4]),
                int(date_text[4:6]),
                int(date_text[6:8]),
                hour,
                minute,
                second,
                tzinfo=_TAIPEI,
            )
            return stamp.isoformat()
        except ValueError:
            pass
    return now.astimezone(_TAIPEI).isoformat()


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _default_symbol_loader() -> SymbolUniverse:
    twse_symbols, twse_failed = _load_symbol_side(
        _TWSE_SYMBOLS_URL,
        "Code",
        provider_name="twse_openapi",
        resource_id="TWT84U",
    )
    tpex_symbols, tpex_failed = _load_symbol_side(
        _TPEX_SYMBOLS_URL,
        "SecuritiesCompanyCode",
        provider_name="tpex",
        resource_id="tpex_companies",
    )
    if twse_failed and tpex_failed:
        raise ValueError("Taiwan price-limit symbol lists were unavailable")
    return SymbolUniverse(
        symbols={"TWSE": twse_symbols, "TPEx": tpex_symbols},
        partial=twse_failed or tpex_failed,
    )


def _load_symbol_side(
    url: str,
    field: str,
    *,
    provider_name: str,
    resource_id: str,
) -> tuple[tuple[str, ...], bool]:
    try:
        payload = fetch_json(
            url,
            headers={"User-Agent": _BROWSER_USER_AGENT, "Accept": "application/json"},
            timeout_seconds=8.0,
            retries=1,
            provider_name=provider_name,
            operation_name="tw_price_limit_symbols",
            resource_id=resource_id,
        )
        return _symbols_from_rows(payload, field), False
    except (HttpFetchError, TimeoutError, ValueError, OSError):
        logger.warning(
            "Taiwan price-limit symbol list failed for one board",
            extra={
                "event": "rankings.tw_price_limit.symbols_board_failed",
                "resource_id": resource_id,
            },
        )
        return (), True


def _symbols_from_rows(payload: object, field: str) -> tuple[str, ...]:
    if not isinstance(payload, list):
        raise ValueError("Taiwan symbol list was not a JSON array")
    symbols: list[str] = []
    for row in payload:
        if isinstance(row, dict):
            symbols.append(str(row.get(field) or ""))
    return tuple(symbols)


def _default_mis_fetcher(ex_chs: list[str]) -> list[dict[str, Any]]:
    from app.services.providers.tw_provider import (  # pylint: disable=import-outside-toplevel
        mis_circuit_is_open,
        note_mis_transport_failure,
        note_mis_transport_success,
    )

    if mis_circuit_is_open():
        raise TimeoutError("TWSE MIS circuit is open")
    try:
        payload = _fetch_mis_payload(ex_chs)
    except (HttpFetchError, TimeoutError, ValueError, OSError):
        note_mis_transport_failure("tw_price_limit")
        raise
    note_mis_transport_success()
    return payload


def _fetch_mis_payload(ex_chs: list[str]) -> list[dict[str, Any]]:
    payload = fetch_json(
        _MIS_URL,
        params={"ex_ch": "|".join(ex_chs), "json": "1", "delay": "0"},
        headers={
            "User-Agent": _BROWSER_USER_AGENT,
            "Accept": "application/json,text/javascript,*/*",
            "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        },
        timeout_seconds=8.0,
        retries=1,
        provider_name="twse",
        operation_name="tw_price_limit_mis",
        resource_id=ex_chs[0] if ex_chs else "empty",
    )
    if not isinstance(payload, dict):
        raise ValueError("MIS price-limit response was not an object")
    if str(payload.get("rtcode") or "") not in {"", "0000"}:
        raise ValueError("MIS price-limit response was not OK")
    rows = payload.get("msgArray")
    if not isinstance(rows, list):
        raise ValueError("MIS price-limit response is missing msgArray")
    return [row for row in rows if isinstance(row, dict)]


def _redis_get(key: str) -> str | None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    return run_redis(f"tw price limit get {key}", lambda client: client.get(key), kind="read")


def _redis_setex(key: str, ttl_seconds: int, value: str) -> None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    run_redis(
        f"tw price limit setex {key}",
        lambda client: client.setex(key, ttl_seconds, value),
        kind="write",
    )


def api_price(value: Decimal | None) -> float | None:
    return decimal_to_api_float(value)


tw_price_limit_rankings_source = TwPriceLimitRankingsSource()
