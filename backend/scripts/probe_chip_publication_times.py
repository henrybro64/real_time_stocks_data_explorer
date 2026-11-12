#!/usr/bin/env python3
"""Read-only probe for TW institutional/margin publication-ready times (#347).

Polls the same official TWSE/TPEx endpoints the collector uses (T86-equivalent
and margin balance) and records the first Asia/Taipei time each dataset becomes
available for a given trading day. Does **not** write to the DB, touch
first-pass/scan code, or change code defaults.

Cache bypass: each poll clears ``taiwan_market_data_client`` and fetches with
``cache_ttl=timedelta(0)`` so stale no-data cache entries cannot hide publication.

Usage (from backend/, or inside the backend Deployment)::

    python -m scripts.probe_chip_publication_times --date 2026-11-12
    python -m scripts.probe_chip_publication_times --start 14:30 --end 23:00 \\
        --interval-minutes 10 --stock TW:2330 --market TWSE

Hard caps: at most 60 requests per dataset per run. Non-trading days exit
non-zero. Results append one JSON line to ``scripts/out/chip_publication_times.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

# Allow `python scripts/probe_chip_publication_times.py` from backend/.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_TAIPEI = ZoneInfo("Asia/Taipei")
_MAX_REQUESTS_PER_DATASET = 60
_DEFAULT_OUT = _BACKEND_ROOT / "scripts" / "out" / "chip_publication_times.jsonl"
_DATASETS = ("institutional", "margin")

Fetcher = Callable[[date, str, str], tuple[bool, str | None]]
SleepFn = Callable[[float], None]
NowFn = Callable[[], datetime]
IsTradingDayFn = Callable[[str, datetime], bool]


@dataclass
class ProbeResult:
    trading_day: str
    market: str
    stock_id: str
    first_available: dict[str, str | None]
    polls: list[dict[str, Any]]
    request_counts: dict[str, int]


def _parse_hhmm(raw: str) -> dt_time:
    parts = str(raw).strip().split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {raw!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {raw!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise argparse.ArgumentTypeError(f"expected HH:MM, got {raw!r}")
    return dt_time(hour, minute)


def _symbol_from_stock_id(stock_id: str) -> str:
    text = str(stock_id).strip().upper()
    if ":" in text:
        return text.split(":", 1)[1]
    return text


def _live_institutional_available(
    trading_day: date, symbol: str, market: str
) -> tuple[bool, str | None]:
    """One uncached T86 / TPEx daily institutional request for ``symbol``."""
    from datetime import timedelta as _td

    from app.services.http_client import HttpFetchError
    from app.services.taiwan_market_data import (
        is_taiwan_no_data_payload,
        taiwan_market_data_client,
    )
    from app.services.tw_institutional_service import (
        _TPEX_DAILY_TRADE_SECT,
        _TPEX_DAILY_TRADE_URL,
        _TWSE_T86_SELECT_TYPE,
        _TWSE_T86_URL,
        TaiwanInstitutionalInvestorsService,
    )

    # Bypass the module client's in-memory cache (including short no-data TTL).
    taiwan_market_data_client.clear_cache()
    board = "TPEx" if market == "TPEx" else "TWSE"
    try:
        if board == "TPEx":
            payload = taiwan_market_data_client.fetch_tpex_data(
                _TPEX_DAILY_TRADE_URL,
                method="POST",
                data={
                    "response": "json",
                    "type": "Daily",
                    "sect": _TPEX_DAILY_TRADE_SECT,
                    "date": trading_day.strftime("%Y/%m/%d"),
                },
                cache_ttl=_td(0),
                operation_name="probe_chip_institutional_tpex",
                resource_id=trading_day.isoformat(),
            )
        else:
            payload = taiwan_market_data_client.fetch_twse_data(
                _TWSE_T86_URL,
                params={
                    "response": "json",
                    "date": trading_day.strftime("%Y%m%d"),
                    "selectType": _TWSE_T86_SELECT_TYPE,
                },
                cache_ttl=_td(0),
                operation_name="probe_chip_institutional_twse",
                resource_id=trading_day.isoformat(),
            )
    except HttpFetchError as exc:
        return False, f"http_error:{getattr(exc, 'classification', None) or 'fetch_failed'}"

    if is_taiwan_no_data_payload(payload):
        return False, "not_published"

    service = TaiwanInstitutionalInvestorsService(
        twse_fetcher=(lambda _d, _p=payload: _p) if board == "TWSE" else None,
        tpex_fetcher=(lambda _d, _p=payload: _p) if board == "TPEx" else None,
    )
    lookup = service._fetch_record_for_day(board, trading_day, symbol)  # noqa: SLF001
    if not lookup.request_succeeded:
        return False, "request_failed"
    if lookup.record is None:
        return False, "no_data_for_stock"
    return True, None


def _live_margin_available(
    trading_day: date, symbol: str, market: str
) -> tuple[bool, str | None]:
    """One uncached official margin-balance request for ``symbol`` (not borrowed-short)."""
    from datetime import timedelta as _td

    from app.services.http_client import HttpFetchError
    from app.services.taiwan_market_data import taiwan_market_data_client
    from app.services.tw_margin_short_service import (
        _TPEX_MARGIN_URL,
        _TWSE_MARGIN_URL,
        _extract_tpex_margin_point,
        _extract_twse_margin_point,
        assess_margin_short_core_payload,
    )

    taiwan_market_data_client.clear_cache()
    board = "TPEx" if market == "TPEx" else "TWSE"
    try:
        if board == "TPEx":
            payload = taiwan_market_data_client.fetch_tpex_data(
                _TPEX_MARGIN_URL,
                params={
                    "response": "json",
                    "date": trading_day.strftime("%Y/%m/%d"),
                },
                cache_ttl=_td(0),
                operation_name="probe_chip_margin_tpex",
                resource_id=trading_day.isoformat(),
            )
            status, usable = assess_margin_short_core_payload(
                payload, payload_kind="tpex"
            )
            if status != "found" or usable is None:
                return False, status
            outcome = _extract_tpex_margin_point(
                usable, symbol=symbol, current_date=trading_day
            )
        else:
            payload = taiwan_market_data_client.fetch_twse_data(
                _TWSE_MARGIN_URL,
                params={
                    "response": "json",
                    "date": trading_day.strftime("%Y%m%d"),
                    "selectType": "ALL",
                },
                cache_ttl=_td(0),
                operation_name="probe_chip_margin_twse",
                resource_id=trading_day.isoformat(),
            )
            status, usable = assess_margin_short_core_payload(
                payload, payload_kind="twse"
            )
            if status != "found" or usable is None:
                return False, status
            outcome = _extract_twse_margin_point(
                usable, symbol=symbol, current_date=trading_day
            )
    except HttpFetchError as exc:
        return False, f"http_error:{getattr(exc, 'classification', None) or 'fetch_failed'}"

    if outcome.status == "found":
        return True, None
    if outcome.status == "symbol_absent":
        return False, "no_data_for_stock"
    return False, outcome.status or "unavailable"


def default_fetchers() -> dict[str, Fetcher]:
    return {
        "institutional": _live_institutional_available,
        "margin": _live_margin_available,
    }


def run_probe(
    *,
    trading_day: date,
    market: str,
    stock_id: str,
    start: dt_time,
    end: dt_time,
    interval_minutes: int,
    fetchers: dict[str, Fetcher] | None = None,
    now_fn: NowFn | None = None,
    sleep_fn: SleepFn | None = None,
    is_trading_day_fn: IsTradingDayFn | None = None,
    max_requests_per_dataset: int = _MAX_REQUESTS_PER_DATASET,
    out_path: Path | None = None,
    write_jsonl: bool = True,
) -> ProbeResult:
    """Poll datasets until available or caps hit. Network only via ``fetchers``."""
    if interval_minutes < 5:
        raise ValueError("interval_minutes must be >= 5")
    if market not in {"TWSE", "TPEx"}:
        raise ValueError("market must be TWSE or TPEx")

    now_fn = now_fn or (lambda: datetime.now(_TAIPEI))
    sleep_fn = sleep_fn or time.sleep
    if is_trading_day_fn is None:
        from app.services.market_clock import is_trading_day as _is_trading_day

        def is_trading_day_fn(market_code: str, when: datetime) -> bool:
            return bool(_is_trading_day(market_code, when, allow_holiday_fetch=False))

    probe_now = now_fn()
    if probe_now.tzinfo is None:
        probe_now = probe_now.replace(tzinfo=_TAIPEI)
    else:
        probe_now = probe_now.astimezone(_TAIPEI)

    day_probe = datetime.combine(trading_day, dt_time(12, 0), tzinfo=_TAIPEI)
    if not is_trading_day_fn("TW", day_probe):
        raise SystemExit(
            f"non_trading_day: {trading_day.isoformat()} is not a TW trading day "
            "(refusing to probe; use market_clock / holiday calendar)."
        )

    fetchers = fetchers or default_fetchers()
    symbol = _symbol_from_stock_id(stock_id)
    pending = {name for name in _DATASETS if name in fetchers}
    first_available: dict[str, str | None] = {name: None for name in _DATASETS}
    request_counts: dict[str, int] = {name: 0 for name in _DATASETS}
    polls: list[dict[str, Any]] = []

    window_start = datetime.combine(trading_day, start, tzinfo=_TAIPEI)
    window_end = datetime.combine(trading_day, end, tzinfo=_TAIPEI)
    interval = timedelta(minutes=interval_minutes)

    # Align first poll to now if already inside the window; otherwise wait for start.
    cursor = max(probe_now, window_start)
    # Floor to interval grid from window_start.
    if cursor > window_start:
        elapsed = cursor - window_start
        steps = int(elapsed.total_seconds() // interval.total_seconds())
        cursor = window_start + steps * interval
        if cursor < probe_now:
            cursor = cursor + interval

    while pending and cursor <= window_end:
        now = now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=_TAIPEI)
        else:
            now = now.astimezone(_TAIPEI)
        if now < cursor:
            sleep_fn((cursor - now).total_seconds())
            now = now_fn()
            if now.tzinfo is None:
                now = now.replace(tzinfo=_TAIPEI)
            else:
                now = now.astimezone(_TAIPEI)

        for dataset in list(pending):
            if request_counts[dataset] >= max_requests_per_dataset:
                pending.discard(dataset)
                continue
            available, empty_reason = fetchers[dataset](trading_day, symbol, market)
            request_counts[dataset] += 1
            entry = {
                "now_taipei": now.isoformat(),
                "dataset": dataset,
                "available": bool(available),
                "empty_reason": empty_reason,
            }
            polls.append(entry)
            if available:
                first_available[dataset] = now.strftime("%H:%M")
                pending.discard(dataset)

        # Cap check after the round.
        for dataset in list(pending):
            if request_counts[dataset] >= max_requests_per_dataset:
                pending.discard(dataset)

        cursor = cursor + interval

    result = ProbeResult(
        trading_day=trading_day.isoformat(),
        market=market,
        stock_id=stock_id,
        first_available=first_available,
        polls=polls,
        request_counts=request_counts,
    )

    if write_jsonl:
        path = out_path or _DEFAULT_OUT
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "trading_day": result.trading_day,
            "market": result.market,
            "stock_id": result.stock_id,
            "institutional_first_available": first_available.get("institutional"),
            "margin_first_available": first_available.get("margin"),
            "request_counts": request_counts,
            "polled_at": now_fn().astimezone(_TAIPEI).isoformat(),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe TW chip publication-ready times (read-only)."
    )
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Trading day YYYY-MM-DD (default: today Asia/Taipei)",
    )
    parser.add_argument("--start", type=_parse_hhmm, default=dt_time(14, 30))
    parser.add_argument("--end", type=_parse_hhmm, default=dt_time(23, 0))
    parser.add_argument("--interval-minutes", type=int, default=10)
    parser.add_argument("--stock", default="TW:2330")
    parser.add_argument("--market", choices=("TWSE", "TPEx"), default="TWSE")
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT,
        help="JSONL append path (default: scripts/out/chip_publication_times.jsonl)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    trading_day = args.date or datetime.now(_TAIPEI).date()
    if args.interval_minutes < 5:
        print("error: --interval-minutes must be >= 5", file=sys.stderr)
        return 2
    try:
        result = run_probe(
            trading_day=trading_day,
            market=args.market,
            stock_id=args.stock,
            start=args.start,
            end=args.end,
            interval_minutes=args.interval_minutes,
            out_path=args.out,
            write_jsonl=True,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"trading_day={result.trading_day} market={result.market} stock={result.stock_id}")
    for dataset in _DATASETS:
        when = result.first_available.get(dataset)
        print(
            f"  {dataset}: first_available={when or 'not_observed'} "
            f"(requests={result.request_counts.get(dataset, 0)})"
        )
    print(f"jsonl_appended={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
