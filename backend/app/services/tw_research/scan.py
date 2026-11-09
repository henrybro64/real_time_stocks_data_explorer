"""Bounded configurable Taiwan research batch scan (#312).

Stage A: daily close + completed-day MACD/KD pass for each symbol in the batch.
Stage B: for day-pass symbols only, completed-month indicators, SMAs, 52-week
position. Strict eligibility never changes to fill target_count.

Checkpoint contract key:
  (trading_day, universe_version, calculation_version, symbol)

Checkpoints and scan ledgers are **process-local / ephemeral**. They do not
survive a process restart. Restart-safe resume requires a Postgres store and is
intentionally not claimed here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from threading import Lock
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.services.daily_price_history_service import daily_price_history_service
from app.services.moving_average import completed_daily_candles
from app.services.quote_service import quote_service
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    DEFAULT_MAX_UPSTREAM_MONTH_FETCHES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    DEFAULT_TARGET_COUNT,
    PREFERRED_DAILY_HISTORY_LIMIT,
    SCAN_BATCH_SIZE,
)
from app.services.tw_research.indicators import (
    MacdKdSnapshot,
    daily_macd_kd_snapshot,
    fifty_two_week_position,
    latest_sma,
    monthly_macd_kd_snapshot,
)
from app.services.tw_research.universe import (
    UniverseSelector,
    resolve_universe,
)

TAIPEI = ZoneInfo("Asia/Taipei")
ScanStatus = Literal["running", "completed", "incomplete", "budget_exhausted"]


class HistoryLoader(Protocol):
    def __call__(
        self,
        stock: StockRecord,
        limit: int,
        *,
        max_month_fetches: int,
    ) -> tuple[list[CandleRecord], int]:
        """Return ``(candles, upstream_month_fetches_used)`` within the budget."""


StockLoader = Callable[[str], StockRecord | None]


@dataclass(frozen=True)
class CheckpointKey:
    trading_day: str
    universe_version: str
    calculation_version: str
    symbol: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.trading_day,
            self.universe_version,
            self.calculation_version,
            self.symbol,
        )


@dataclass
class SymbolScanRecord:
    stock_id: str
    stage: Literal["A", "B", "rejected"]
    daily: dict[str, Any] | None = None
    monthly: dict[str, Any] | None = None
    sma20: float | None = None
    sma60: float | None = None
    sma120: float | None = None
    sma240: float | None = None
    week52_position: float | None = None
    rejection_reason: str | None = None
    checkpoint: tuple[str, str, str, str] | None = None


@dataclass
class ScanState:
    scan_id: str
    trading_day: str
    universe_version: str
    calculation_version: str
    selector: UniverseSelector
    stock_ids: tuple[str, ...]
    cursor: int = 0
    target_count: int = DEFAULT_TARGET_COUNT
    status: ScanStatus = "running"
    records: dict[str, SymbolScanRecord] = field(default_factory=dict)
    day_pass_ids: list[str] = field(default_factory=list)
    upstream_month_fetches: int = 0
    max_upstream_month_fetches: int = DEFAULT_MAX_UPSTREAM_MONTH_FETCHES
    created_at: str = ""
    updated_at: str = ""
    notes: list[str] = field(default_factory=list)
    run_lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "scanId": self.scan_id,
            "tradingDay": self.trading_day,
            "universeVersion": self.universe_version,
            "calculationVersion": self.calculation_version,
            "status": self.status,
            "cursor": self.cursor,
            "universeSize": len(self.stock_ids),
            "processedCount": len(self.records),
            "remainingCount": max(0, len(self.stock_ids) - self.cursor),
            "targetCount": self.target_count,
            "dayPassCount": len(self.day_pass_ids),
            "dayPassIds": list(self.day_pass_ids),
            "upstreamMonthFetches": self.upstream_month_fetches,
            "maxUpstreamMonthFetches": self.max_upstream_month_fetches,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "notes": list(self.notes),
            "ledger": [asdict(record) for record in self.records.values()],
            "checkpointsEphemeral": True,
            "checkpointContract": {
                "key": [
                    "trading_day",
                    "universe_version",
                    "calculation_version",
                    "symbol",
                ],
                "durable": False,
                "note": (
                    "In-memory only. Restart clears checkpoints; do not claim "
                    "restart-safe resume without Postgres persistence."
                ),
            },
        }


class TwResearchScanService:
    def __init__(
        self,
        *,
        history_loader: HistoryLoader | None = None,
        stock_loader: StockLoader | None = None,
    ) -> None:
        self._history_loader = history_loader or _default_history_loader
        self._stock_loader = stock_loader or quote_service.get_catalog_stock
        self._lock = Lock()
        self._scans: dict[str, ScanState] = {}
        self._checkpoints: set[tuple[str, str, str, str]] = set()

    def create_scan(
        self,
        selector: UniverseSelector,
        *,
        target_count: int = DEFAULT_TARGET_COUNT,
        trading_day: str | None = None,
        max_upstream_month_fetches: int = DEFAULT_MAX_UPSTREAM_MONTH_FETCHES,
        now: datetime | None = None,
    ) -> ScanState:
        if target_count not in {10, 20}:
            # Output target only; never changes pass rules.
            raise ValueError("target_count must be 10 or 20")
        resolved = resolve_universe(selector, stock_resolver=self._stock_loader)
        current = now or datetime.now(TAIPEI)
        day = trading_day or current.date().isoformat()
        stamp = current.isoformat()
        state = ScanState(
            scan_id=str(uuid4()),
            trading_day=day,
            universe_version=resolved.universe_version,
            calculation_version=CALCULATION_VERSION,
            selector=selector,
            stock_ids=resolved.stock_ids,
            target_count=target_count,
            max_upstream_month_fetches=max_upstream_month_fetches,
            created_at=stamp,
            updated_at=stamp,
            notes=list(resolved.notes),
        )
        for item in resolved.rejected:
            state.records[item.stock_id] = SymbolScanRecord(
                stock_id=item.stock_id,
                stage="rejected",
                rejection_reason=item.reason,
            )
        with self._lock:
            self._scans[state.scan_id] = state
        return state

    def get_scan(self, scan_id: str) -> ScanState | None:
        with self._lock:
            return self._scans.get(scan_id)

    def run_batch(
        self,
        scan_id: str,
        *,
        batch_size: int = SCAN_BATCH_SIZE,
        now: datetime | None = None,
        timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    ) -> ScanState:
        """Process up to ``batch_size`` not-yet-scanned symbols (Stage A, then B)."""
        if batch_size <= 0 or batch_size > SCAN_BATCH_SIZE:
            raise ValueError(f"batch_size must be 1..{SCAN_BATCH_SIZE}")
        started = datetime.now(TAIPEI)

        with self._lock:
            state = self._scans.get(scan_id)
            if state is None:
                raise KeyError(f"Unknown scan_id: {scan_id}")

        # Serialize overlapping batch POSTs for the same scan.
        with state.run_lock:
            with self._lock:
                if state.status in {"completed", "budget_exhausted"} and state.cursor >= len(
                    state.stock_ids
                ):
                    return state
                cursor = state.cursor
                batch_ids = list(state.stock_ids[cursor : cursor + batch_size])
                # Reserve the slice so a concurrent waiter sees an advanced cursor.
                state.cursor = cursor + len(batch_ids)

            calculation_now = _calculation_now(state.trading_day, override=now)
            for stock_id in batch_ids:
                if (datetime.now(TAIPEI) - started).total_seconds() > timeout_seconds:
                    with self._lock:
                        # Rewind unprocessed reserved ids so a later batch can retry.
                        remaining = batch_ids[batch_ids.index(stock_id) :]
                        state.cursor = max(0, state.cursor - len(remaining))
                        state.status = "incomplete"
                        state.notes.append("Batch stopped: run timeout.")
                        state.updated_at = datetime.now(TAIPEI).isoformat()
                    return state
                try:
                    self._process_symbol(state, stock_id, now=calculation_now)
                except Exception as exc:  # noqa: BLE001 — isolate per symbol
                    record = SymbolScanRecord(
                        stock_id=stock_id,
                        stage="A",
                        rejection_reason=f"symbol_error:{type(exc).__name__}",
                        checkpoint=CheckpointKey(
                            trading_day=state.trading_day,
                            universe_version=state.universe_version,
                            calculation_version=state.calculation_version,
                            symbol=stock_id,
                        ).as_tuple(),
                    )
                    with self._lock:
                        state.records[stock_id] = record
                        self._checkpoints.add(record.checkpoint)  # type: ignore[arg-type]

            with self._lock:
                if state.cursor >= len(state.stock_ids):
                    state.status = "completed"
                elif state.upstream_month_fetches >= state.max_upstream_month_fetches:
                    state.status = "budget_exhausted"
                else:
                    state.status = "running"
                state.updated_at = datetime.now(TAIPEI).isoformat()
                state.notes.append(
                    "target_count is an output target only and does not relax pass rules."
                )
                return state

    def _process_symbol(self, state: ScanState, stock_id: str, *, now: datetime) -> None:
        checkpoint = CheckpointKey(
            trading_day=state.trading_day,
            universe_version=state.universe_version,
            calculation_version=state.calculation_version,
            symbol=stock_id,
        )
        with self._lock:
            if checkpoint.as_tuple() in self._checkpoints and stock_id in state.records:
                return
            # Reserve checkpoint before any upstream work so concurrent batches skip.
            self._checkpoints.add(checkpoint.as_tuple())

        stock = self._stock_loader(stock_id)
        if stock is None:
            record = SymbolScanRecord(
                stock_id=stock_id,
                stage="rejected",
                rejection_reason="not_in_catalog",
                checkpoint=checkpoint.as_tuple(),
            )
            with self._lock:
                state.records[stock_id] = record
            return

        with self._lock:
            remaining_budget = max(
                0, state.max_upstream_month_fetches - state.upstream_month_fetches
            )

        candles, months_used = self._history_loader(
            stock,
            PREFERRED_DAILY_HISTORY_LIMIT,
            max_month_fetches=remaining_budget,
        )
        with self._lock:
            state.upstream_month_fetches += months_used
            remaining_after = max(
                0, state.max_upstream_month_fetches - state.upstream_month_fetches
            )

        daily = daily_macd_kd_snapshot(candles, market="TW", now=now)
        record = SymbolScanRecord(
            stock_id=stock_id,
            stage="A",
            daily=_snapshot_dict(daily),
            checkpoint=checkpoint.as_tuple(),
        )

        if daily.pass_status == "pass":
            # Stage B is local computation over already-loaded candles. History
            # budget was enforced before the fetch above.
            if not candles and remaining_after <= 0 and months_used == 0:
                record.rejection_reason = "history_budget_exhausted"
            else:
                monthly = monthly_macd_kd_snapshot(candles, market="TW", now=now)
                completed = completed_daily_candles(candles, "TW", now=now)
                record.stage = "B"
                record.monthly = _snapshot_dict(monthly)
                record.sma20 = latest_sma(completed, 20)
                record.sma60 = latest_sma(completed, 60)
                record.sma120 = latest_sma(completed, 120)
                record.sma240 = latest_sma(completed, 240)
                record.week52_position = fifty_two_week_position(completed)
                with self._lock:
                    state.day_pass_ids.append(stock_id)
        elif months_used == 0 and remaining_budget <= 0 and not candles:
            record.rejection_reason = "history_budget_exhausted"

        with self._lock:
            state.records[stock_id] = record


def _calculation_now(trading_day: str, *, override: datetime | None) -> datetime:
    """Anchor indicators to the scan trading day (after TW cash close).

    An explicit ``override`` is honored only when its calendar date matches
    ``trading_day`` (unit tests). Cross-day overrides are ignored so resumed
    batches cannot mix sessions under one trading-day key.
    """
    day = date.fromisoformat(trading_day)
    if override is not None and override.astimezone(TAIPEI).date() == day:
        return override.astimezone(TAIPEI)
    return datetime.combine(day, time(14, 0), tzinfo=TAIPEI)


def _snapshot_dict(snapshot: MacdKdSnapshot) -> dict[str, Any]:
    return {
        "asOf": snapshot.as_of,
        "dif": snapshot.dif,
        "signal": snapshot.signal,
        "osc": snapshot.osc,
        "k": snapshot.k,
        "d": snapshot.d,
        "passStatus": snapshot.pass_status,
        "quality": snapshot.quality,
        "priceBasis": snapshot.price_basis,
        "calculationVersion": snapshot.calculation_version,
        "barCount": snapshot.bar_count,
        "timeframe": snapshot.timeframe,
    }


def _default_history_loader(
    stock: StockRecord,
    limit: int,
    *,
    max_month_fetches: int,
) -> tuple[list[CandleRecord], int]:
    from app.services.providers.tw_provider import TWProvider

    provider = quote_service.providers.get("TW") or TWProvider()
    return daily_price_history_service.get_daily_history_within_budget(
        stock,
        "MAX",
        limit=limit,
        provider=provider,
        max_month_fetches=max_month_fetches,
    )


tw_research_scan_service = TwResearchScanService()
