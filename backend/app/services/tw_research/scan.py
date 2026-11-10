"""Bounded configurable Taiwan research batch scan (#312/#318/#320).

Stage A: daily close + completed-day MACD/KD pass for each symbol in the batch.
Stage B: for day-pass symbols only, completed-month indicators, SMAs, 52-week
position. Strict eligibility never changes to fill target_count.

Checkpoint contract key:
  (trading_day, universe_version, calculation_version, symbol, scan_id)

Checkpoints and scan ledgers are persisted to PostgreSQL when DATABASE_URL is
configured (#318). Durable reads load committed rows and do not serve the
process-local cache. The cache is only the persist=False path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from inspect import Parameter, signature
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
    DEFAULT_MAX_ENRICHMENTS_PER_RUN,
    DEFAULT_MAX_UPSTREAM_MONTH_FETCHES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    DEFAULT_TARGET_COUNT,
    ENRICH_BATCH_SIZE,
    PREFERRED_DAILY_HISTORY_LIMIT,
    SCAN_BATCH_SIZE,
)
from app.services.tw_research.coverage import (
    derive_readiness,
    preflight_tw_trading_calendars,
    validate_ohlcv_series,
)
from app.services.tw_research.enrichment import (
    enrich_qualified_symbol,
    is_technically_qualified,
)
from app.services.tw_research.indicators import (
    MacdKdSnapshot,
    assemble_monthly_candles,
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
logger = logging.getLogger(__name__)
ScanStatus = Literal[
    "queued",
    "running",
    "partial",
    "budget_exhausted",
    "completed_processing",
    "verified_ready",
    "failed",
]
QualityStatus = Literal[
    "processed",
    "technically_qualified",
    "source_incomplete",
    "verified_ready",
]


class TwResearchDurableStoreError(RuntimeError):
    """Durable PostgreSQL claim/write failed; caller should retry when available.

    Raised only when ``persist=True``. Memory-only scans (``persist=False``) never
    require PostgreSQL and are intentionally non-durable.
    """

    def __init__(
        self,
        message: str,
        *,
        scan_id: str | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.scan_id = scan_id
        self.operation = operation
        self.retryable = True


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
    scan_id: str = ""

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.trading_day,
            self.universe_version,
            self.calculation_version,
            self.symbol,
        )

    def as_durable_tuple(self) -> tuple[str, str, str, str, str]:
        return (*self.as_tuple(), self.scan_id)


@dataclass
class SymbolScanRecord:
    stock_id: str
    stage: Literal["A", "B", "rejected"]
    readiness: Literal[
        "processed",
        "technically_qualified",
        "source_incomplete",
        "verified_ready",
        "rejected",
    ] = "processed"
    daily: dict[str, Any] | None = None
    monthly: dict[str, Any] | None = None
    sma20: float | None = None
    sma60: float | None = None
    sma120: float | None = None
    sma240: float | None = None
    week52_position: float | None = None
    rejection_reason: str | None = None
    checkpoint: tuple[str, str, str, str] | None = None
    theme_keys: tuple[str, ...] = ()
    enrichment: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None


@dataclass
class ScanState:
    scan_id: str
    trading_day: str
    universe_version: str
    calculation_version: str
    selector: UniverseSelector
    stock_ids: tuple[str, ...]
    cursor: int = 0
    enrich_cursor: int = 0
    target_count: int = DEFAULT_TARGET_COUNT
    status: ScanStatus = "queued"
    quality_status: QualityStatus = "processed"
    records: dict[str, SymbolScanRecord] = field(default_factory=dict)
    day_pass_ids: list[str] = field(default_factory=list)
    qualified_ids: list[str] = field(default_factory=list)
    upstream_month_fetches: int = 0
    max_upstream_month_fetches: int = DEFAULT_MAX_UPSTREAM_MONTH_FETCHES
    enrichments_run: int = 0
    max_enrichments_per_run: int = DEFAULT_MAX_ENRICHMENTS_PER_RUN
    # Active enrichment lease generation; bumped by durable claim_enrichment_batch.
    enrich_lease_generation: int = 0
    created_at: str = ""
    updated_at: str = ""
    notes: list[str] = field(default_factory=list)
    user_id: str | None = None
    active_snapshot_id: str | None = None
    # Lease deadlines copied from PostgreSQL. A status of "running" is not a heartbeat.
    reserve_expires_at: str | None = None
    enrich_reserve_expires_at: str | None = None
    run_lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        return {
            "scanId": self.scan_id,
            "tradingDay": self.trading_day,
            "universeVersion": self.universe_version,
            "calculationVersion": self.calculation_version,
            "status": self.status,
            "qualityStatus": self.quality_status,
            "cursor": self.cursor,
            "enrichCursor": self.enrich_cursor,
            "universeSize": len(self.stock_ids),
            "processedCount": len(self.records),
            "remainingCount": max(0, len(self.stock_ids) - self.cursor),
            "targetCount": self.target_count,
            "dayPassCount": len(self.day_pass_ids),
            "dayPassIds": list(self.day_pass_ids),
            "qualifiedCount": len(self.qualified_ids),
            "qualifiedIds": list(self.qualified_ids),
            "verifiedReadyCount": sum(
                1 for record in self.records.values() if record.readiness == "verified_ready"
            ),
            "sourceIncompleteCount": sum(
                1
                for record in self.records.values()
                if record.readiness == "source_incomplete"
            ),
            "enrichmentsRun": self.enrichments_run,
            "maxEnrichmentsPerRun": self.max_enrichments_per_run,
            "upstreamMonthFetches": self.upstream_month_fetches,
            "maxUpstreamMonthFetches": self.max_upstream_month_fetches,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "notes": list(self.notes),
            "ledger": [asdict(record) for record in self.records.values()],
            "activeSnapshotId": self.active_snapshot_id,
            "reserveExpiresAt": self.reserve_expires_at,
            "enrichReserveExpiresAt": self.enrich_reserve_expires_at,
            "workerActivity": describe_worker_activity(
                self.status,
                self.reserve_expires_at,
                self.enrich_reserve_expires_at,
            ),
            "checkpointsDurable": True,
            "checkpointsEphemeral": False,
            "checkpointContract": {
                "key": [
                    "trading_day",
                    "universe_version",
                    "calculation_version",
                    "symbol",
                    "scan_id",
                ],
                "durable": True,
                "note": (
                    "Persisted to PostgreSQL when DATABASE_URL is configured. "
                    "In-memory cache is a process accelerator only."
                ),
            },
        }


_NONTERMINAL_SCAN_STATUSES = {"queued", "running", "partial"}


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def describe_worker_activity(
    status: str,
    reserve_expires_at: datetime | str | None,
    enrich_reserve_expires_at: datetime | str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Lease-based liveness. A stored status of running is not a heartbeat."""
    current = now or datetime.now(timezone.utc)
    for lease in (reserve_expires_at, enrich_reserve_expires_at):
        expires = _as_utc(lease)
        if expires is not None and expires > current:
            return "active"
    if status in _NONTERMINAL_SCAN_STATUSES:
        return "unknown"
    return "idle"


def _summary_from_state(state: ScanState) -> dict[str, Any]:
    universe_size = len(state.stock_ids)
    return {
        "scanId": state.scan_id,
        "tradingDay": state.trading_day,
        "universeVersion": state.universe_version,
        "status": state.status,
        "qualityStatus": state.quality_status,
        "targetCount": state.target_count,
        "universeSize": universe_size,
        "cursor": state.cursor,
        "remainingCount": max(0, universe_size - state.cursor),
        "updatedAt": state.updated_at,
        "activeSnapshotId": state.active_snapshot_id,
        "workerActivity": describe_worker_activity(
            state.status,
            state.reserve_expires_at,
            state.enrich_reserve_expires_at,
        ),
        "selectorMode": state.selector.mode,
        "themeKeys": list(state.selector.theme_keys),
        "stockIds": list(state.selector.stock_ids),
    }


def _summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    stock_ids = list(row.get("stock_ids_json") or [])
    selector = row.get("selector_json") or {}
    cursor = int(row.get("cursor") or 0)
    updated = row.get("updated_at")
    return {
        "scanId": row["scan_id"],
        "tradingDay": row["trading_day"],
        "universeVersion": row["universe_version"],
        "status": row["status"],
        "qualityStatus": row.get("quality_status") or "processed",
        "targetCount": int(row.get("target_count") or 0),
        "universeSize": len(stock_ids),
        "cursor": cursor,
        "remainingCount": max(0, len(stock_ids) - cursor),
        "updatedAt": updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
        "activeSnapshotId": row.get("active_snapshot_id"),
        "workerActivity": describe_worker_activity(
            str(row.get("status") or ""),
            row.get("reserve_expires_at"),
            row.get("enrich_reserve_expires_at"),
        ),
        "selectorMode": selector.get("mode") or "starter_preset",
        "themeKeys": list(selector.get("themeKeys") or []),
        "stockIds": list(selector.get("stockIds") or []),
    }


class TwResearchScanService:
    def __init__(
        self,
        *,
        history_loader: HistoryLoader | None = None,
        stock_loader: StockLoader | None = None,
        persist: bool = True,
    ) -> None:
        """Create a scan service.

        ``persist=True`` (default) requires a configured PostgreSQL durable store
        for batch claims and writes. Failures raise ``TwResearchDurableStoreError``
        instead of silently falling back to memory-only execution.

        ``persist=False`` is the explicit in-memory path for unit tests and
        deliberate non-durable runs; it does not require PostgreSQL.
        """
        self._history_loader = history_loader or _default_history_loader
        self._stock_loader = stock_loader or quote_service.get_catalog_stock
        self._persist = persist
        self._lock = Lock()
        # Process-local accelerator for persist=False. Durable reads do not use it.
        self._scans: dict[str, ScanState] = {}
        # Stable across get_scan() reloads so two batches in this process still queue.
        self._run_locks: dict[str, Lock] = {}
        self._checkpoints: set[tuple[str, str, str, str]] = set()

    def _run_lock_for(self, scan_id: str) -> Lock:
        with self._lock:
            lock = self._run_locks.get(scan_id)
            if lock is None:
                lock = Lock()
                self._run_locks[scan_id] = lock
            return lock

    def create_scan(
        self,
        selector: UniverseSelector,
        *,
        target_count: int = DEFAULT_TARGET_COUNT,
        trading_day: str | None = None,
        max_upstream_month_fetches: int = DEFAULT_MAX_UPSTREAM_MONTH_FETCHES,
        max_enrichments_per_run: int = DEFAULT_MAX_ENRICHMENTS_PER_RUN,
        now: datetime | None = None,
        user_id: str | None = None,
    ) -> ScanState:
        if target_count not in {10, 20}:
            # Output target only; never changes pass rules.
            raise ValueError("target_count must be 10 or 20")
        if max_enrichments_per_run < 0:
            raise ValueError("max_enrichments_per_run must be >= 0")
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
            status="queued",
            max_upstream_month_fetches=max_upstream_month_fetches,
            max_enrichments_per_run=max_enrichments_per_run,
            created_at=stamp,
            updated_at=stamp,
            notes=list(resolved.notes),
            user_id=user_id,
        )
        for item in resolved.rejected:
            state.records[item.stock_id] = SymbolScanRecord(
                stock_id=item.stock_id,
                stage="rejected",
                readiness="rejected",
                rejection_reason=item.reason,
            )
        with self._lock:
            self._scans[state.scan_id] = state
        try:
            self._persist_scan(state)
            for record in state.records.values():
                self._persist_symbol(state, record)
        except TwResearchDurableStoreError:
            with self._lock:
                self._scans.pop(state.scan_id, None)
            raise
        return state

    def get_scan(self, scan_id: str) -> ScanState | None:
        """Return the scan.

        Durable mode always reads committed PostgreSQL rows. The process cache
        is not consulted, so a write from another worker or pod is visible and
        is not merged with a stale in-memory copy.
        """
        if not self._persist:
            with self._lock:
                return self._scans.get(scan_id)
        from app.services.tw_research.persistence import load_scan_state

        return load_scan_state(scan_id)

    def list_scans(self, user_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Recent scans for one user. Does not load symbol rows or upstream data."""
        bounded = max(1, min(int(limit), 50))
        if not self._persist:
            with self._lock:
                states = [state for state in self._scans.values() if state.user_id == user_id]
            states.sort(key=lambda state: state.updated_at or "", reverse=True)
            return [_summary_from_state(state) for state in states[:bounded]]
        from app.repositories.tw_research_repository import tw_research_repository

        if not tw_research_repository.is_available():
            raise TwResearchDurableStoreError(
                "Durable research store is unavailable",
                operation="list_scans",
            )
        try:
            rows = tw_research_repository.list_scans_for_user(user_id, limit=bounded)
        except Exception as exc:  # noqa: BLE001
            raise TwResearchDurableStoreError(
                "Failed to list research scans",
                operation="list_scans",
            ) from exc
        return [_summary_from_row(row) for row in rows]

    def get_scan_progress(
        self,
        scan_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Counters-only progress. Durable mode never returns a stale process cache."""
        if not self._persist:
            with self._lock:
                state = self._scans.get(scan_id)
            if state is None:
                return None
            if user_id is not None and state.user_id != user_id:
                return None
            snapshot = state.snapshot()
            from app.services.tw_research.persistence import progress_revision

            revision = progress_revision(
                updated_at=str(snapshot.get("updatedAt") or ""),
                status=str(snapshot.get("status") or ""),
                quality_status=str(snapshot.get("qualityStatus") or ""),
                cursor=int(snapshot.get("cursor") or 0),
                enrich_cursor=int(snapshot.get("enrichCursor") or 0),
                processed_count=int(snapshot.get("processedCount") or 0),
                upstream_month_fetches=int(snapshot.get("upstreamMonthFetches") or 0),
                enrichments_run=int(snapshot.get("enrichmentsRun") or 0),
                worker_activity=str(snapshot.get("workerActivity") or "unknown"),
                active_snapshot_id=snapshot.get("activeSnapshotId"),
            )
            return {
                "scanId": snapshot["scanId"],
                "tradingDay": snapshot["tradingDay"],
                "universeVersion": snapshot["universeVersion"],
                "status": snapshot["status"],
                "qualityStatus": snapshot["qualityStatus"],
                "cursor": snapshot["cursor"],
                "enrichCursor": snapshot["enrichCursor"],
                "universeSize": snapshot["universeSize"],
                "processedCount": snapshot["processedCount"],
                "remainingCount": snapshot["remainingCount"],
                "targetCount": snapshot["targetCount"],
                "dayPassCount": snapshot["dayPassCount"],
                "qualifiedCount": snapshot["qualifiedCount"],
                "enrichmentsRun": snapshot["enrichmentsRun"],
                "maxEnrichmentsPerRun": snapshot["maxEnrichmentsPerRun"],
                "upstreamMonthFetches": snapshot["upstreamMonthFetches"],
                "maxUpstreamMonthFetches": snapshot["maxUpstreamMonthFetches"],
                "createdAt": snapshot["createdAt"],
                "updatedAt": snapshot["updatedAt"],
                "activeSnapshotId": snapshot["activeSnapshotId"],
                "reserveExpiresAt": snapshot.get("reserveExpiresAt"),
                "enrichReserveExpiresAt": snapshot.get("enrichReserveExpiresAt"),
                "workerActivity": snapshot["workerActivity"],
                "ledgerRevision": revision,
                "includesLedger": False,
            }
        from app.services.tw_research.persistence import load_scan_progress

        return load_scan_progress(scan_id, user_id=user_id)

    def _remember_checkpoints(self, state: ScanState) -> None:
        with self._lock:
            for stock_id, record in state.records.items():
                if record.checkpoint is not None:
                    self._checkpoints.add(record.checkpoint)
                else:
                    self._checkpoints.add(
                        (
                            state.trading_day,
                            state.universe_version,
                            state.calculation_version,
                            stock_id,
                        )
                    )

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

        # Serialize overlapping batch POSTs for the same scan (process lock +
        # durable SELECT FOR UPDATE claim when Postgres is available).
        # Reload inside the lock so a waiter does not apply a stale copy.
        with self._run_lock_for(scan_id):
            state = self.get_scan(scan_id)
            if state is None:
                raise KeyError(f"Unknown scan_id: {scan_id}")
            self._remember_checkpoints(state)
            batch_ids = self._claim_batch_ids(state, batch_size=batch_size)
            if batch_ids is None:
                return state
            if not batch_ids:
                # Active lease held by another worker, or empty reservation.
                return state

            # Bounded TW calendar preflight for indicator warm-up years; missing
            # years fail closed in coverage and can continue on later batches.
            calendar_preflight = preflight_tw_trading_calendars(
                trading_day=state.trading_day,
                allow_network=True,
            )
            if not calendar_preflight.get("ok"):
                missing = ",".join(str(year) for year in calendar_preflight.get("missing") or [])
                note = f"trading_calendar_preflight_missing:{missing or 'unknown'}"
                with self._lock:
                    if note not in state.notes:
                        state.notes.append(note)
                    state.updated_at = datetime.now(TAIPEI).isoformat()
                # Persist outside self._lock — DB work must not nest with hydrate/rewind.
                self._persist_scan(state)

            calculation_now = _calculation_now(state.trading_day, override=now)
            for stock_id in batch_ids:
                if (datetime.now(TAIPEI) - started).total_seconds() > timeout_seconds:
                    with self._lock:
                        # Rewind unprocessed reserved ids so a later batch can retry.
                        remaining = batch_ids[batch_ids.index(stock_id) :]
                        state.cursor = max(0, state.cursor - len(remaining))
                        state.status = "partial"
                        state.notes.append("Batch stopped: run timeout.")
                        state.updated_at = datetime.now(TAIPEI).isoformat()
                    # Persist/rewind acquire repository locks and self._lock — outside.
                    self._persist_scan(state)
                    self._rewind_durable_cursor(state)
                    return state
                try:
                    self._process_symbol(state, stock_id, now=calculation_now)
                except TwResearchDurableStoreError:
                    # Required durable write failed — stop the batch; do not
                    # convert this into a per-symbol screening error.
                    raise
                except Exception as exc:  # noqa: BLE001 — isolate per symbol
                    record = SymbolScanRecord(
                        stock_id=stock_id,
                        stage="A",
                        readiness="processed",
                        rejection_reason=f"symbol_error:{type(exc).__name__}",
                        checkpoint=CheckpointKey(
                            trading_day=state.trading_day,
                            universe_version=state.universe_version,
                            calculation_version=state.calculation_version,
                            symbol=stock_id,
                            scan_id=state.scan_id,
                        ).as_tuple(),
                    )
                    with self._lock:
                        state.records[stock_id] = record
                        self._checkpoints.add(record.checkpoint)  # type: ignore[arg-type]
                    self._persist_symbol(state, record)

            with self._lock:
                self._rebuild_aggregates_locked(state)
                self._refresh_status_locked(state)
                state.updated_at = datetime.now(TAIPEI).isoformat()
                state.notes.append(
                    "target_count is an output target only and does not relax pass rules."
                )
            self._persist_scan(state)
            self._complete_durable_batch(state)
            if state.status != "failed":
                self._maybe_finalize(state)
            return state

    def _process_symbol(self, state: ScanState, stock_id: str, *, now: datetime) -> None:
        checkpoint = CheckpointKey(
            trading_day=state.trading_day,
            universe_version=state.universe_version,
            calculation_version=state.calculation_version,
            symbol=stock_id,
            scan_id=state.scan_id,
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
                readiness="rejected",
                rejection_reason="not_in_catalog",
                checkpoint=checkpoint.as_tuple(),
            )
            with self._lock:
                state.records[stock_id] = record
            self._persist_symbol(state, record)
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

        completed = completed_daily_candles(candles, "TW", now=now)
        try:
            expect_through = date.fromisoformat(state.trading_day)
        except ValueError:
            expect_through = None
        daily_coverage = validate_ohlcv_series(
            completed,
            market="TW",
            check_calendar_gaps=True,
            allow_holiday_fetch=False,
            expect_through=expect_through,
        )
        daily = daily_macd_kd_snapshot(candles, market="TW", now=now)
        record = SymbolScanRecord(
            stock_id=stock_id,
            stage="A",
            readiness="processed",
            daily=_snapshot_dict(daily),
            checkpoint=checkpoint.as_tuple(),
            coverage={
                "daily": {
                    "ok": daily_coverage.ok,
                    "reason": daily_coverage.reason,
                    "barCount": daily_coverage.bar_count,
                    "missingTradingDays": list(daily_coverage.missing_trading_days),
                    "duplicateDates": list(daily_coverage.duplicate_dates),
                    "invalidBars": daily_coverage.invalid_bars,
                    "priceBasis": daily_coverage.price_basis,
                }
            },
        )

        monthly_coverage_ok = False
        if daily.pass_status == "pass":
            # Stage B is local computation over already-loaded candles. History
            # budget was enforced before the fetch above.
            if not candles and remaining_after <= 0 and months_used == 0:
                record.rejection_reason = "history_budget_exhausted"
                record.readiness = "rejected"
            else:
                monthly = monthly_macd_kd_snapshot(candles, market="TW", now=now)
                monthly_bars = assemble_monthly_candles(completed, now=now)
                from app.services.tw_research.coverage import monthly_series_verified

                monthly_coverage = monthly_series_verified(
                    completed,
                    monthly_bars,
                    market="TW",
                    allow_holiday_fetch=False,
                )
                monthly_coverage_ok = monthly_coverage.ok
                record.stage = "B"
                record.monthly = _snapshot_dict(monthly)
                record.sma20 = latest_sma(completed, 20)
                record.sma60 = latest_sma(completed, 60)
                record.sma120 = latest_sma(completed, 120)
                record.sma240 = latest_sma(completed, 240)
                record.week52_position = fifty_two_week_position(completed)
                record.coverage = {
                    **(record.coverage or {}),
                    "monthly": {
                        "ok": monthly_coverage.ok,
                        "reason": monthly_coverage.reason,
                        "barCount": monthly_coverage.bar_count,
                        "missingTradingDays": list(monthly_coverage.missing_trading_days),
                    },
                }
                with self._lock:
                    state.day_pass_ids.append(stock_id)
                    if is_technically_qualified(record):
                        state.qualified_ids.append(stock_id)
        elif months_used == 0 and remaining_budget <= 0 and not candles:
            record.rejection_reason = "history_budget_exhausted"
            record.readiness = "rejected"

        if record.readiness != "rejected":
            record.readiness = derive_readiness(
                stage=record.stage,
                daily_pass=(record.daily or {}).get("passStatus"),
                monthly_pass=(record.monthly or {}).get("passStatus")
                if record.monthly
                else None,
                daily_coverage_ok=daily_coverage.ok,
                monthly_coverage_ok=monthly_coverage_ok
                if record.stage == "B"
                else daily_coverage.ok,
                rejection_reason=record.rejection_reason,
            )

        record.theme_keys = _theme_keys_for(stock_id)
        with self._lock:
            state.records[stock_id] = record
        self._persist_symbol(state, record)

    def run_enrichment_batch(
        self,
        scan_id: str,
        *,
        batch_size: int = ENRICH_BATCH_SIZE,
        now: datetime | None = None,
        enricher: Callable[..., Any] | None = None,
    ) -> ScanState:
        """Enrich up to ``batch_size`` dual-pass symbols that still lack enrichment (#313)."""
        if batch_size <= 0 or batch_size > ENRICH_BATCH_SIZE:
            raise ValueError(f"batch_size must be 1..{ENRICH_BATCH_SIZE}")
        enrich_fn = enricher or enrich_qualified_symbol

        with self._run_lock_for(scan_id):
            state = self.get_scan(scan_id)
            if state is None:
                raise KeyError(f"Unknown scan_id: {scan_id}")
            self._remember_checkpoints(state)
            batch_ids, remaining_budget = self._claim_enrichment_ids(
                state, batch_size=batch_size
            )
            if remaining_budget <= 0 and not batch_ids:
                with self._lock:
                    state.notes.append(
                        "Enrichment stopped: max_enrichments_per_run budget exhausted."
                    )
                    state.updated_at = datetime.now(TAIPEI).isoformat()
                self._persist_scan(state)
                return state
            if not batch_ids:
                # Active enrichment lease held by another worker.
                return state

            calculation_now = _calculation_now(state.trading_day, override=now)
            for stock_id in batch_ids:
                with self._lock:
                    prior_record = state.records.get(stock_id)
                    prior_week52 = (
                        prior_record.week52_position if prior_record is not None else None
                    )
                payload = _invoke_enricher(
                    enrich_fn,
                    stock_id,
                    calculation_now=calculation_now,
                    prior_week52=prior_week52,
                )
                record_to_persist: SymbolScanRecord | None = None
                with self._lock:
                    record = state.records.get(stock_id)
                    if record is not None:
                        record.enrichment = payload
                        record_to_persist = record
                if record_to_persist is not None:
                    self._persist_symbol(state, record_to_persist)

            self._complete_durable_enrichment(state)
            with self._lock:
                state.updated_at = datetime.now(TAIPEI).isoformat()
                state.notes.append(
                    "Missing chip/valuation data is per-field unavailable; "
                    "it does not convert a technical pass into a fail."
                )
            self._persist_scan(state)
            # Newer immutable snapshot; prior finalized snapshots remain unchanged.
            if state.status != "failed":
                self._maybe_finalize(state, allow_verified_refresh=True)
            return state

    def _refresh_status_locked(self, state: ScanState) -> None:
        if state.status == "failed":
            return
        if state.cursor >= len(state.stock_ids):
            state.status = "completed_processing"
        elif state.upstream_month_fetches >= state.max_upstream_month_fetches:
            state.status = "budget_exhausted"
        elif state.status not in {"partial", "failed"}:
            state.status = "running"

        verified = sum(
            1 for record in state.records.values() if record.readiness == "verified_ready"
        )
        incomplete = sum(
            1
            for record in state.records.values()
            if record.readiness == "source_incomplete"
        )
        qualified = sum(
            1
            for record in state.records.values()
            if record.readiness in {"technically_qualified", "verified_ready"}
            or is_technically_qualified(record)
        )
        # Scan-level verified_ready is reserved for successful immutable finalize.
        if state.active_snapshot_id and state.status == "verified_ready":
            state.quality_status = "verified_ready"
        elif state.status in {"completed_processing", "budget_exhausted"}:
            # Finalize-eligible; incompletes are labeled via sourceCompleteness.
            state.quality_status = (
                "source_incomplete" if incomplete else "technically_qualified"
            )
        elif incomplete:
            state.quality_status = "source_incomplete"
        elif qualified or verified:
            state.quality_status = "technically_qualified"
        else:
            state.quality_status = "processed"

    def _maybe_finalize(
        self, state: ScanState, *, allow_verified_refresh: bool = False
    ) -> None:
        if not self._persist:
            return
        publishable_status = {
            "completed_processing",
            "verified_ready",
            "budget_exhausted",
        }
        publishable_quality = {
            "technically_qualified",
            "verified_ready",
            "source_incomplete",
            "processed",
        }
        refreshing_verified = (
            state.status == "verified_ready" and allow_verified_refresh
        )
        if not refreshing_verified:
            if state.status not in publishable_status:
                return
            if state.quality_status not in publishable_quality:
                return
        from app.services.tw_research.persistence import finalize_verified_snapshot

        # Refresh path: keep verified status so finalize accepts the ledger.
        if refreshing_verified:
            state.quality_status = "verified_ready"

        finalized = finalize_verified_snapshot(state, user_id=state.user_id)
        if finalized is None and state.status != "failed":
            # Never advertise verified without a snapshot row.
            note = "finalize_deferred_or_failed"
            if note not in state.notes:
                state.notes.append(note)
            self._persist_scan(state)

    def _require_durable_repository(self, *, scan_id: str, operation: str):
        from app.repositories.tw_research_repository import tw_research_repository

        if not tw_research_repository.is_available():
            raise TwResearchDurableStoreError(
                "Durable persistence is enabled but PostgreSQL is unavailable.",
                scan_id=scan_id,
                operation=operation,
            )
        return tw_research_repository

    def _run_durable(
        self,
        *,
        scan_id: str,
        operation: str,
        action: Callable[[], Any],
        failure_note: str | None = None,
        state: ScanState | None = None,
    ) -> Any:
        """Execute a durable repository call; never fall back to memory on error."""
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 — surface as retryable durable failure
            logger.exception(
                "tw_research durable %s failed scan_id=%s",
                operation,
                scan_id,
            )
            if failure_note is not None and state is not None:
                from app.services.tw_research.persistence import mark_persist_failure

                mark_persist_failure(state, f"{failure_note}:{type(exc).__name__}")
            raise TwResearchDurableStoreError(
                f"Durable {operation} failed ({type(exc).__name__}).",
                scan_id=scan_id,
                operation=operation,
            ) from exc

    def _claim_batch_ids(self, state: ScanState, *, batch_size: int) -> list[str] | None:
        """Reserve next symbol slice; None means already complete.

        When ``persist=True``, reservations must succeed in PostgreSQL. Failures
        raise ``TwResearchDurableStoreError`` and never fall back to memory.
        """
        terminal = {"completed_processing", "verified_ready", "budget_exhausted"}
        if not self._persist:
            with self._lock:
                if state.status in terminal and state.cursor >= len(state.stock_ids):
                    return None
                if state.status == "queued":
                    state.status = "running"
                cursor = state.cursor
                batch_ids = list(state.stock_ids[cursor : cursor + batch_size])
                state.cursor = cursor + len(batch_ids)
                self._persist_scan(state)
                return batch_ids

        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="claim_scan_batch"
        )
        claimed = self._run_durable(
            scan_id=state.scan_id,
            operation="claim_scan_batch",
            action=lambda: repo.claim_scan_batch(
                state.scan_id,
                batch_size=batch_size,
                terminal_statuses=terminal,
            ),
        )
        if claimed is None:
            raise TwResearchDurableStoreError(
                "Durable scan row missing for claim.",
                scan_id=state.scan_id,
                operation="claim_scan_batch",
            )
        # Pull peer-worker symbol results / counters before processing.
        self._hydrate_state_from_durable(state)
        with self._lock:
            reserved_from = int(claimed.get("reserved_from", state.cursor))
            batch_ids = list(claimed.get("batch_ids") or [])
            # In-memory cursor tracks the reservation end for timeout rewind.
            state.cursor = reserved_from + len(batch_ids)
            state.status = claimed["scan"]["status"]  # type: ignore[assignment]
            state.updated_at = datetime.now(TAIPEI).isoformat()
        if claimed.get("already_complete"):
            return None
        if claimed.get("lease_held"):
            return []
        return batch_ids

    def _claim_enrichment_ids(
        self, state: ScanState, *, batch_size: int
    ) -> tuple[list[str], int]:
        if not self._persist:
            with self._lock:
                pending = [
                    stock_id
                    for stock_id in state.qualified_ids
                    if stock_id in state.records
                    and state.records[stock_id].enrichment is None
                ]
                remaining_budget = state.max_enrichments_per_run - state.enrichments_run
                batch_ids = pending[: min(batch_size, max(0, remaining_budget))]
                return batch_ids, remaining_budget

        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="claim_enrichment_batch"
        )
        claimed = self._run_durable(
            scan_id=state.scan_id,
            operation="claim_enrichment_batch",
            action=lambda: repo.claim_enrichment_batch(
                state.scan_id, batch_size=batch_size
            ),
        )
        if claimed is None:
            raise TwResearchDurableStoreError(
                "Durable scan row missing for enrichment claim.",
                scan_id=state.scan_id,
                operation="claim_enrichment_batch",
            )
        self._hydrate_state_from_durable(state)
        with self._lock:
            state.enrich_cursor = int(claimed["scan"]["enrich_cursor"])
            state.enrichments_run = int(
                claimed["scan"].get("enrichments_run", state.enrichments_run)
            )
            state.enrich_lease_generation = int(
                claimed.get("lease_generation")
                or claimed["scan"].get("enrich_lease_generation")
                or 0
            )
            remaining = int(claimed["remaining_budget"])
            if claimed.get("lease_held"):
                return [], remaining
            pending = list(claimed.get("batch_ids") or [])
        return pending, remaining

    def _complete_durable_enrichment(self, state: ScanState) -> None:
        if not self._persist:
            with self._lock:
                state.enrichments_run = sum(
                    1
                    for stock_id in state.qualified_ids
                    if stock_id in state.records
                    and state.records[stock_id].enrichment is not None
                )
            return
        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="complete_enrichment_batch"
        )
        lease_generation = int(getattr(state, "enrich_lease_generation", 0) or 0) or None
        completed = self._run_durable(
            scan_id=state.scan_id,
            operation="complete_enrichment_batch",
            action=lambda: repo.complete_enrichment_batch(
                state.scan_id,
                lease_generation=lease_generation,
            ),
            failure_note="complete_enrichment",
            state=state,
        )
        if completed is None:
            return
        with self._lock:
            state.enrich_cursor = int(completed["enrich_cursor"])
            state.enrichments_run = int(completed["enrichments_run"])
        # Hydrate outside self._lock: hydrate acquires the same non-reentrant lock.
        self._hydrate_state_from_durable(state)

    def _rewind_durable_cursor(self, state: ScanState) -> None:
        if not self._persist:
            return
        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="rewind_scan_cursor"
        )
        self._run_durable(
            scan_id=state.scan_id,
            operation="rewind_scan_cursor",
            action=lambda: repo.rewind_scan_cursor(
                state.scan_id, new_cursor=state.cursor, status=state.status
            ),
            failure_note="rewind_cursor",
            state=state,
        )
        self._hydrate_state_from_durable(state)

    def _complete_durable_batch(self, state: ScanState) -> None:
        if not self._persist:
            return
        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="complete_scan_batch"
        )
        completed = self._run_durable(
            scan_id=state.scan_id,
            operation="complete_scan_batch",
            action=lambda: repo.complete_scan_batch(
                state.scan_id, status=state.status
            ),
            failure_note="complete_batch",
            state=state,
        )
        if completed is None:
            return
        with self._lock:
            state.cursor = int(completed["cursor"])
            state.status = completed["status"]  # type: ignore[assignment]

    def _hydrate_state_from_durable(self, state: ScanState) -> None:
        """Merge durable scan/symbol rows into the in-memory cache (multi-worker)."""
        if not self._persist:
            return
        from app.services.tw_research.persistence import load_scan_state

        loaded = load_scan_state(state.scan_id)
        if loaded is None:
            return
        with self._lock:
            state.cursor = loaded.cursor
            state.enrich_cursor = loaded.enrich_cursor
            state.status = loaded.status
            state.quality_status = loaded.quality_status
            state.upstream_month_fetches = loaded.upstream_month_fetches
            state.enrichments_run = loaded.enrichments_run
            state.enrich_lease_generation = int(
                getattr(loaded, "enrich_lease_generation", 0) or 0
            )
            state.active_snapshot_id = loaded.active_snapshot_id
            state.notes = list(loaded.notes)
            for stock_id, record in loaded.records.items():
                state.records[stock_id] = record
                if record.checkpoint is not None:
                    self._checkpoints.add(record.checkpoint)
            self._rebuild_aggregates_locked(state)

    def _rebuild_aggregates_locked(self, state: ScanState) -> None:
        state.day_pass_ids = [
            stock_id
            for stock_id in state.stock_ids
            if (state.records.get(stock_id) is not None)
            and (state.records[stock_id].daily or {}).get("passStatus") == "pass"
        ]
        state.qualified_ids = [
            stock_id
            for stock_id in state.stock_ids
            if (record := state.records.get(stock_id)) is not None
            and (
                record.readiness in {"technically_qualified", "verified_ready"}
                or is_technically_qualified(record)
            )
        ]

    def _persist_scan(self, state: ScanState) -> None:
        if not self._persist:
            return
        from app.repositories.tw_research_repository import tw_research_repository
        from app.services.tw_research.persistence import persist_scan

        if not tw_research_repository.is_available():
            raise TwResearchDurableStoreError(
                "Durable persistence is enabled but PostgreSQL is unavailable.",
                scan_id=state.scan_id,
                operation="persist_scan",
            )
        if not persist_scan(state, user_id=state.user_id):
            raise TwResearchDurableStoreError(
                "Durable scan write failed.",
                scan_id=state.scan_id,
                operation="persist_scan",
            )

    def _persist_symbol(self, state: ScanState, record: SymbolScanRecord) -> None:
        if not self._persist:
            return
        from app.repositories.tw_research_repository import tw_research_repository
        from app.services.tw_research.persistence import persist_symbol

        if not tw_research_repository.is_available():
            raise TwResearchDurableStoreError(
                "Durable persistence is enabled but PostgreSQL is unavailable.",
                scan_id=state.scan_id,
                operation="persist_symbol",
            )
        lease_generation = None
        if record.enrichment is not None:
            lease_generation = int(getattr(state, "enrich_lease_generation", 0) or 0) or None
        if not persist_symbol(
            state,
            record,
            enrich_lease_generation=lease_generation,
        ):
            raise TwResearchDurableStoreError(
                f"Durable symbol write failed for {record.stock_id}.",
                scan_id=state.scan_id,
                operation="persist_symbol",
            )


def _invoke_enricher(
    enrich_fn: Callable[..., Any],
    stock_id: str,
    *,
    calculation_now: datetime,
    prior_week52: float | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"now": calculation_now}
    try:
        parameters = signature(enrich_fn).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_var_kw = any(
        parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    if accepts_var_kw or "prior_week52_position" in parameters:
        kwargs["prior_week52_position"] = prior_week52
    try:
        enriched = enrich_fn(stock_id, **kwargs)
        payload = enriched.as_dict() if hasattr(enriched, "as_dict") else enriched
        return payload if isinstance(payload, dict) else {"stockId": stock_id, "fields": {}}
    except Exception as exc:  # noqa: BLE001 — isolate per symbol
        return {
            "stockId": stock_id,
            "qualified": True,
            "error": f"symbol_error:{type(exc).__name__}",
            "fields": {},
        }


def _theme_keys_for(stock_id: str) -> tuple[str, ...]:
    try:
        from app.services.stock_theme_service import DEFAULT_THEME_ASSIGNMENTS

        for assignment in DEFAULT_THEME_ASSIGNMENTS:
            if assignment.stock_id == stock_id:
                return tuple(assignment.theme_keys)
    except Exception:  # noqa: BLE001 — themes are optional metadata
        return ()
    return ()


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
