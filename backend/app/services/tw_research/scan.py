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

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dt_time, timezone
from inspect import Parameter, signature
from threading import Event, Lock, Thread
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
    DEFAULT_BUDGET_WINDOW_MONTH_FETCHES,
    DEFAULT_ENRICHMENT_WINDOW_SIZE,
    DEFAULT_MAX_ENRICHMENTS_PER_RUN,
    DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
    DEFAULT_MAX_UPSTREAM_MONTH_FETCHES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    DEFAULT_TARGET_COUNT,
    ENRICH_BATCH_SIZE,
    HISTORY_BUDGET_EXHAUSTED_REASON,
    HISTORY_FLIGHT_LEASE_SECONDS,
    HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES,
    HISTORY_FLIGHT_POLL_INITIAL_SECONDS,
    HISTORY_FLIGHT_POLL_MAX_SECONDS,
    HISTORY_FLIGHT_RENEW_INTERVAL_SECONDS,
    HISTORY_FLIGHT_WAIT_MAX_SECONDS,
    MAX_ENRICHMENTS_PER_SCAN_CAP,
    MAX_UPSTREAM_MONTH_FETCHES_CAP,
    PREFERRED_DAILY_HISTORY_LIMIT,
    PRICE_BASIS,
    SCAN_BATCH_SIZE,
)
from app.services.tw_research.evaluation import evaluation_counts
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
# Cross-scan single-flight for equivalent history downloads within one process (#329).
_HISTORY_FLIGHT_GUARD = Lock()
_HISTORY_FLIGHT_LOCKS: dict[str, Lock] = {}
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


class TwResearchConflictError(ValueError):
    """Caller must take an explicit action before retrying (HTTP 409)."""

    def __init__(
        self,
        message: str,
        *,
        scan_id: str | None = None,
        operation: str | None = None,
        code: str = "conflict",
    ) -> None:
        super().__init__(message)
        self.scan_id = scan_id
        self.operation = operation
        self.code = code


class TwResearchAdmissionError(RuntimeError):
    """Cluster-wide in-flight scan batch limit reached (HTTP 503, retryable)."""

    def __init__(
        self,
        message: str,
        *,
        scan_id: str | None = None,
        operation: str | None = None,
        in_flight: int | None = None,
        limit: int | None = None,
    ) -> None:
        super().__init__(message)
        self.scan_id = scan_id
        self.operation = operation
        self.in_flight = in_flight
        self.limit = limit
        self.retryable = True


_DURABLE_POSTGRES_UNAVAILABLE = (
    "Durable persistence is enabled but PostgreSQL is unavailable."
)


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


class TwResearchHistoryCoordinationError(RuntimeError):
    """History-flight ownership was lost or became uncertain mid-fetch (#329).

    Not a definitive technical screening outcome. Callers must not persist a
    screening result for the affected symbol; a later worker may retry after
    reclaiming the flight. Upstream bars already written remain reusable.
    """

    def __init__(
        self,
        message: str,
        *,
        scan_id: str | None = None,
        stock_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.scan_id = scan_id
        self.stock_id = stock_id
        self.retryable = True
        self.operation = "history_flight"


class TwResearchScanLeaseStaleError(RuntimeError):
    """Technical symbol write rejected: scan-batch lease generation is stale (#329).

    Raised when a worker's ``scan_lease_generation`` no longer matches an active
    PostgreSQL batch lease. Callers must stop the batch without advancing the
    watermark or publishing a verified snapshot from the stale worker.
    """

    def __init__(
        self,
        message: str,
        *,
        scan_id: str | None = None,
        stock_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.scan_id = scan_id
        self.stock_id = stock_id
        self.retryable = True
        self.operation = "persist_symbol"


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
    stock_name: str | None = None
    name_status: Literal["ok", "unavailable"] = "unavailable"
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
    # Active scan-batch lease generation; bumped by durable claim_scan_batch (#329).
    scan_lease_generation: int = 0
    budget_windows_opened: int = 0
    created_at: str = ""
    updated_at: str = ""
    notes: list[str] = field(default_factory=list)
    user_id: str | None = None
    active_snapshot_id: str | None = None
    # Optional immutable candidate-set provenance for second-pass scans (#336).
    candidate_provenance: dict[str, Any] | None = None
    # Lease deadlines copied from PostgreSQL. A status of "running" is not a heartbeat.
    reserve_expires_at: str | None = None
    enrich_reserve_expires_at: str | None = None
    run_lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def snapshot(self) -> dict[str, Any]:
        counts = evaluation_counts(
            stock_ids=self.stock_ids,
            records=self.records,
            cursor=self.cursor,
        )
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
            "processedCount": counts["processedCount"],
            "definitiveEvaluatedCount": counts["definitiveEvaluatedCount"],
            "budgetExhaustedCount": counts["budgetExhaustedCount"],
            "sourceIncompleteCount": counts["sourceIncompleteCount"],
            "unresolvedCount": counts["unresolvedCount"],
            "unprocessedCount": counts["unprocessedCount"],
            "remainingCount": counts["remainingCount"],
            "targetCount": self.target_count,
            "dayPassCount": len(self.day_pass_ids),
            "dayPassIds": list(self.day_pass_ids),
            "qualifiedCount": len(self.qualified_ids),
            "qualifiedIds": list(self.qualified_ids),
            "verifiedReadyCount": sum(
                1 for record in self.records.values() if record.readiness == "verified_ready"
            ),
            "enrichmentsRun": self.enrichments_run,
            "maxEnrichmentsPerRun": self.max_enrichments_per_run,
            "upstreamMonthFetches": self.upstream_month_fetches,
            "maxUpstreamMonthFetches": self.max_upstream_month_fetches,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "notes": list(self.notes),
            "stockIds": list(self.stock_ids),
            "selectorMode": self.selector.mode,
            "candidateProvenance": (
                dict(self.candidate_provenance)
                if self.candidate_provenance is not None
                else None
            ),
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


def _history_flight_lock(stock_id: str) -> Lock:
    with _HISTORY_FLIGHT_GUARD:
        lock = _HISTORY_FLIGHT_LOCKS.get(stock_id)
        if lock is None:
            lock = Lock()
            _HISTORY_FLIGHT_LOCKS[stock_id] = lock
        return lock


def history_flight_identity(
    stock_id: str,
    *,
    limit: int,
    expect_through: str,
    provider: str = "tw_default",
) -> tuple[str, str]:
    """Return ``(flight_key, compatibility_key)`` for equivalent OHLCV backfills.

    Compatibility contract (#329): same symbol, unadjusted close basis, no
    corporate-action adjustment, same provider family, same bar limit, and the
    same required completed trading day (``expect_through``). Requests that
    differ on any of these fields must not share a ready marker.
    """
    compatibility = (
        f"{stock_id}|basis={PRICE_BASIS}|adj=none|provider={provider}"
        f"|limit={int(limit)}|through={expect_through}|bars=daily_v1"
    )
    flight_key = hashlib.sha256(compatibility.encode("utf-8")).hexdigest()[:48]
    return flight_key, compatibility


def stored_history_covers_requirement(
    stock: StockRecord,
    *,
    limit: int,
    expect_through: str,
) -> bool:
    """True when persisted bars cover the flight without missing upstream months."""
    try:
        candles, missing = daily_price_history_service.peek_daily_history(
            stock,
            "MAX",
            limit=limit,
        )
    except Exception:  # noqa: BLE001 — treat unreadable store as incomplete
        return False
    if missing or not candles:
        return False
    try:
        required = date.fromisoformat(expect_through)
    except ValueError:
        return bool(candles) and not missing
    latest = None
    for candle in reversed(candles):
        stamp = getattr(candle, "time", None) or ""
        try:
            latest = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).date()
            break
        except ValueError:
            continue
    if latest is None:
        return False
    return latest >= required


def _summary_from_state(state: ScanState) -> dict[str, Any]:
    universe_size = len(state.stock_ids)
    return {
        "scanId": state.scan_id,
        "tradingDay": state.trading_day,
        "universeVersion": state.universe_version,
        "calculationVersion": state.calculation_version,
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
        "calculationVersion": row.get("calculation_version") or "",
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
        extra_notes: list[str] | None = None,
        candidate_provenance: dict[str, Any] | None = None,
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
        provenance = dict(candidate_provenance) if candidate_provenance else None
        if provenance is not None:
            provenance.setdefault("tradingDay", day)
            provenance.setdefault("calculationVersion", CALCULATION_VERSION)
            provenance.setdefault("universeVersion", resolved.universe_version)
            provenance.setdefault("approvedStockIds", list(resolved.stock_ids))
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
            notes=list(resolved.notes) + list(extra_notes or []),
            user_id=user_id,
            candidate_provenance=provenance,
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
            # Notes + candidate provenance are included in this first durable write.
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
                "definitiveEvaluatedCount": snapshot["definitiveEvaluatedCount"],
                "budgetExhaustedCount": snapshot["budgetExhaustedCount"],
                "sourceIncompleteCount": snapshot["sourceIncompleteCount"],
                "unresolvedCount": snapshot["unresolvedCount"],
                "unprocessedCount": snapshot["unprocessedCount"],
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

    def resume_budget_window(
        self,
        scan_id: str,
        *,
        additional_month_fetches: int = DEFAULT_BUDGET_WINDOW_MONTH_FETCHES,
    ) -> ScanState:
        """Open a bounded history-budget window and reopen unresolved symbols (#329).

        Clears ``history_budget_exhausted`` results so a later batch can retry them
        without re-fetching already-validated warm OHLCV months. Does not raise the
        safety cap above ``MAX_UPSTREAM_MONTH_FETCHES_CAP``.

        Durable path is a single PostgreSQL transaction (FOR UPDATE) so concurrent
        resume requests cannot double-grant windows and mid-flight crashes cannot
        leave cursor/budget/symbol rows half-applied.
        """
        if additional_month_fetches <= 0:
            raise ValueError("additional_month_fetches must be > 0")
        if additional_month_fetches > DEFAULT_BUDGET_WINDOW_MONTH_FETCHES:
            raise ValueError(
                f"additional_month_fetches must be 1..{DEFAULT_BUDGET_WINDOW_MONTH_FETCHES}"
            )

        # verified_ready / completed_processing may still need a bounded reopen
        # after an early snapshot left unprocessed or budget-exhausted symbols.
        allowed = {
            "budget_exhausted",
            "partial",
            "running",
            "verified_ready",
            "completed_processing",
        }
        with self._run_lock_for(scan_id):
            state = self.get_scan(scan_id)
            if state is None:
                raise KeyError(f"Unknown scan_id: {scan_id}")

            if self._persist:
                return self._resume_budget_window_durable(
                    state,
                    additional_month_fetches=additional_month_fetches,
                    allowed_statuses=allowed,
                )

            if state.status == "failed":
                raise TwResearchConflictError(
                    "Cannot resume a failed scan; create a new scan after fixing persistence.",
                    scan_id=scan_id,
                    operation="resume_budget_window",
                    code="scan_failed",
                )
            if state.status not in allowed:
                raise TwResearchConflictError(
                    "Resume requires budget_exhausted/partial/running/"
                    f"verified_ready/completed_processing (got {state.status}).",
                    scan_id=scan_id,
                    operation="resume_budget_window",
                    code="resume_not_applicable",
                )

            prior_status = state.status
            cleared = self._clear_budget_exhausted_symbols(state)
            previous_max = state.max_upstream_month_fetches
            new_max = min(
                MAX_UPSTREAM_MONTH_FETCHES_CAP,
                previous_max + additional_month_fetches,
            )
            open_index = self._first_open_symbol_index(state)
            reopen_terminal_with_gap = (
                prior_status in {"verified_ready", "completed_processing"}
                and open_index < len(state.stock_ids)
            )
            if (
                prior_status != "budget_exhausted"
                and not cleared
                and not reopen_terminal_with_gap
            ):
                raise TwResearchConflictError(
                    "Resume requires budget_exhausted or retryable unresolved symbols.",
                    scan_id=scan_id,
                    operation="resume_budget_window",
                    code="resume_not_applicable",
                )
            if (
                new_max <= previous_max
                and not cleared
                and not reopen_terminal_with_gap
            ):
                raise TwResearchConflictError(
                    "Budget window already at the safety cap and no retryable symbols remain.",
                    scan_id=scan_id,
                    operation="resume_budget_window",
                    code="budget_cap_reached",
                )

            with self._lock:
                state.max_upstream_month_fetches = new_max
                state.budget_windows_opened = int(state.budget_windows_opened or 0) + 1
                state.cursor = self._first_open_symbol_index(state)
                if state.cursor >= len(state.stock_ids):
                    state.status = "completed_processing"
                else:
                    state.status = "partial"
                state.updated_at = datetime.now(TAIPEI).isoformat()
                state.notes.append(
                    "budget_window_opened:"
                    f"+{new_max - previous_max}:max={new_max}:cleared={len(cleared)}"
                )
                self._rebuild_aggregates_locked(state)
                self._refresh_status_locked(state)
            return state

    def resume_enrichment_window(
        self,
        scan_id: str,
        *,
        additional_enrichments: int = DEFAULT_ENRICHMENT_WINDOW_SIZE,
    ) -> ScanState:
        """Open another bounded enrichment window when the run budget is spent (#330).

        ``max_enrichments_per_run`` is a per-scan window allowance (not a lifetime
        prohibition). This raises it by ``additional_enrichments`` up to
        ``MAX_ENRICHMENTS_PER_SCAN_CAP``. Durable path is a single PostgreSQL
        transaction; concurrent grants and mid-lease resumes are rejected.
        """
        if additional_enrichments <= 0:
            raise ValueError("additional_enrichments must be > 0")
        if additional_enrichments > DEFAULT_ENRICHMENT_WINDOW_SIZE:
            raise ValueError(
                f"additional_enrichments must be 1..{DEFAULT_ENRICHMENT_WINDOW_SIZE}"
            )

        with self._run_lock_for(scan_id):
            state = self.get_scan(scan_id)
            if state is None:
                raise KeyError(f"Unknown scan_id: {scan_id}")
            if self._persist:
                return self._resume_enrichment_window_durable(
                    state, additional_enrichments=additional_enrichments
                )

            if state.status == "failed":
                raise TwResearchConflictError(
                    "Cannot resume enrichment on a failed scan.",
                    scan_id=scan_id,
                    operation="resume_enrichment_window",
                    code="scan_failed",
                )
            pending = [
                stock_id
                for stock_id in state.qualified_ids
                if stock_id in state.records
                and state.records[stock_id].enrichment is None
            ]
            if not pending:
                raise TwResearchConflictError(
                    "No pending enrichment remains.",
                    scan_id=scan_id,
                    operation="resume_enrichment_window",
                    code="enrichment_complete",
                )
            remaining = max(0, state.max_enrichments_per_run - state.enrichments_run)
            if remaining > 0:
                raise TwResearchConflictError(
                    "Enrichment budget still remains; resume not required.",
                    scan_id=scan_id,
                    operation="resume_enrichment_window",
                    code="enrichment_budget_remaining",
                )
            previous_max = state.max_enrichments_per_run
            new_max = min(
                MAX_ENRICHMENTS_PER_SCAN_CAP,
                previous_max + additional_enrichments,
            )
            if new_max <= previous_max:
                raise TwResearchConflictError(
                    "Enrichment window already at the safety cap.",
                    scan_id=scan_id,
                    operation="resume_enrichment_window",
                    code="enrichment_cap_reached",
                )
            with self._lock:
                state.max_enrichments_per_run = new_max
                state.notes.append(
                    "enrichment_window_opened:"
                    f"+{new_max - previous_max}:max={new_max}:pending={len(pending)}"
                )
                state.updated_at = datetime.now(TAIPEI).isoformat()
            return state

    def _resume_enrichment_window_durable(
        self,
        state: ScanState,
        *,
        additional_enrichments: int,
    ) -> ScanState:
        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="resume_enrichment_window"
        )
        result = self._run_durable(
            scan_id=state.scan_id,
            operation="resume_enrichment_window",
            action=lambda: repo.resume_enrichment_window(
                state.scan_id,
                additional_enrichments=additional_enrichments,
                max_cap=MAX_ENRICHMENTS_PER_SCAN_CAP,
            ),
            state=state,
        )
        if result is None:
            raise KeyError(f"Unknown scan_id: {state.scan_id}")
        if not result.get("ok"):
            code = str(result.get("code") or "conflict")
            messages = {
                "active_enrichment_lease": (
                    "Cannot resume enrichment while an enrichment lease is held."
                ),
                "enrichment_complete": "No pending enrichment remains.",
                "enrichment_budget_remaining": (
                    "Enrichment budget still remains; resume not required."
                ),
                "enrichment_cap_reached": "Enrichment window already at the safety cap.",
                "scan_failed": "Cannot resume enrichment on a failed scan.",
            }
            raise TwResearchConflictError(
                messages.get(code, f"Enrichment resume conflict ({code})."),
                scan_id=state.scan_id,
                operation="resume_enrichment_window",
                code=code,
            )
        self._hydrate_state_from_durable(state)
        return state

    def _resume_budget_window_durable(
        self,
        state: ScanState,
        *,
        additional_month_fetches: int,
        allowed_statuses: set[str],
    ) -> ScanState:
        repo = self._require_durable_repository(
            scan_id=state.scan_id, operation="resume_budget_window"
        )
        result = self._run_durable(
            scan_id=state.scan_id,
            operation="resume_budget_window",
            action=lambda: repo.resume_budget_window(
                state.scan_id,
                additional_month_fetches=additional_month_fetches,
                max_cap=MAX_UPSTREAM_MONTH_FETCHES_CAP,
                rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
                allowed_statuses=allowed_statuses,
            ),
            state=state,
        )
        if result is None:
            raise KeyError(f"Unknown scan_id: {state.scan_id}")
        if not result.get("ok"):
            code = str(result.get("code") or "conflict")
            messages = {
                "active_batch_lease": (
                    "Cannot resume while an active scan batch lease is held; retry later."
                ),
                "scan_failed": (
                    "Cannot resume a failed scan; create a new scan after fixing persistence."
                ),
                "resume_not_applicable": (
                    "Resume requires budget_exhausted/partial/running/"
                    "verified_ready/completed_processing with open work."
                ),
                "budget_cap_reached": (
                    "Budget window already at the safety cap and no retryable symbols remain."
                ),
            }
            raise TwResearchConflictError(
                messages.get(code, f"Resume conflict ({code})."),
                scan_id=state.scan_id,
                operation="resume_budget_window",
                code=code,
            )
        # Drop cleared budget rows from the process cache, then hydrate truth.
        cleared = set(result.get("cleared") or [])
        with self._lock:
            for stock_id in cleared:
                record = state.records.pop(stock_id, None)
                if record is None:
                    continue
                if record.checkpoint is not None:
                    self._checkpoints.discard(record.checkpoint)
                else:
                    self._checkpoints.discard(
                        (
                            state.trading_day,
                            state.universe_version,
                            state.calculation_version,
                            stock_id,
                        )
                    )
        self._hydrate_state_from_durable(state)
        refreshed = self.get_scan(state.scan_id)
        if refreshed is None:
            raise KeyError(f"Unknown scan_id: {state.scan_id}")
        return refreshed

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
            if state.status == "budget_exhausted":
                # Do not silently burn zero-budget claims as HTTP 200 progress.
                raise TwResearchConflictError(
                    "Scan history budget is exhausted. Open a resume window before running another batch.",
                    scan_id=scan_id,
                    operation="run_batch",
                    code="budget_resume_required",
                )
            # Admission is atomic inside durable claim_scan_batch (#329).
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
                except TwResearchHistoryCoordinationError:
                    # Ownership lost mid-fetch: rewind this and remaining reserved
                    # ids without writing a definitive screening outcome.
                    with self._lock:
                        remaining = batch_ids[batch_ids.index(stock_id) :]
                        state.cursor = max(0, state.cursor - len(remaining))
                        state.status = "partial"
                        note = f"history_flight_coordination_lost:{stock_id}"
                        if note not in state.notes:
                            state.notes.append(note)
                        state.updated_at = datetime.now(TAIPEI).isoformat()
                    self._persist_scan(state)
                    self._rewind_durable_cursor(state)
                    return state
                except TwResearchScanLeaseStaleError:
                    # Another worker reclaimed the batch (or lease expired). Stop
                    # without completing under the stale generation.
                    with self._lock:
                        remaining = batch_ids[batch_ids.index(stock_id) :]
                        state.cursor = max(0, state.cursor - len(remaining))
                        state.status = "partial"
                        note = f"scan_lease_stale_write_rejected:{stock_id}"
                        if note not in state.notes:
                            state.notes.append(note)
                        state.updated_at = datetime.now(TAIPEI).isoformat()
                    self._persist_scan(state)
                    return state
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
                stock_name=None,
                name_status="unavailable",
                checkpoint=checkpoint.as_tuple(),
            )
            with self._lock:
                state.records[stock_id] = record
            self._persist_symbol(state, record)
            return

        display_name = (stock.name_zh_tw or stock.name or "").strip() or None
        name_status: Literal["ok", "unavailable"] = (
            "ok" if display_name else "unavailable"
        )

        with self._lock:
            remaining_budget = max(
                0, state.max_upstream_month_fetches - state.upstream_month_fetches
            )

        # Process lock + durable cross-pod history flight (#329).
        with _history_flight_lock(stock_id):
            candles, months_used = self._load_history_single_flight(
                state,
                stock,
                remaining_budget=remaining_budget,
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
            stock_name=display_name,
            name_status=name_status,
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
                record.rejection_reason = HISTORY_BUDGET_EXHAUSTED_REASON
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
            record.rejection_reason = HISTORY_BUDGET_EXHAUSTED_REASON
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
                _DURABLE_POSTGRES_UNAVAILABLE,
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

    def _first_open_symbol_index(self, state: ScanState) -> int:
        """Index of the first universe symbol without a non-retryable result."""
        for index, stock_id in enumerate(state.stock_ids):
            record = state.records.get(stock_id)
            if record is None:
                return index
            if record.rejection_reason == HISTORY_BUDGET_EXHAUSTED_REASON:
                return index
        return len(state.stock_ids)

    def _clear_budget_exhausted_symbols(self, state: ScanState) -> list[str]:
        """Drop retryable budget-exhausted rows so a new window can reclaim them."""
        cleared: list[str] = []
        with self._lock:
            for stock_id, record in list(state.records.items()):
                if record.rejection_reason != HISTORY_BUDGET_EXHAUSTED_REASON:
                    continue
                cleared.append(stock_id)
                del state.records[stock_id]
                if record.checkpoint is not None:
                    self._checkpoints.discard(record.checkpoint)
                else:
                    self._checkpoints.discard(
                        (
                            state.trading_day,
                            state.universe_version,
                            state.calculation_version,
                            stock_id,
                        )
                    )
        if cleared and self._persist:
            repo = self._require_durable_repository(
                scan_id=state.scan_id, operation="delete_budget_exhausted_symbols"
            )
            self._run_durable(
                scan_id=state.scan_id,
                operation="delete_budget_exhausted_symbols",
                action=lambda: repo.delete_symbol_results_by_rejection(
                    state.scan_id,
                    rejection_reason=HISTORY_BUDGET_EXHAUSTED_REASON,
                ),
                state=state,
            )
        return cleared

    def _load_history_single_flight(
        self,
        state: ScanState,
        stock: StockRecord,
        *,
        remaining_budget: int,
    ) -> tuple[list[CandleRecord], int]:
        """Deduplicate compatible upstream OHLCV backfills across pods (#329).

        Short DB transactions acquire/renew/complete a fenced lease. Waiting
        workers poll with bounded backoff. A ``ready`` marker is never treated as
        proof of coverage — waiters validate persisted bars first and only then
        load with budget 0. Incomplete ready/incomplete markers force
        re-coordination before any positive upstream budget is spent.
        """
        limit = PREFERRED_DAILY_HISTORY_LIMIT
        if not self._persist:
            return self._history_loader(
                stock, limit, max_month_fetches=remaining_budget
            )

        from app.repositories.tw_research_repository import tw_research_repository

        if not tw_research_repository.is_available():
            raise TwResearchDurableStoreError(
                "Durable history flight coordination requires PostgreSQL.",
                scan_id=state.scan_id,
                operation="history_flight",
            )

        # Warm path: validated store coverage needs no flight / no upstream.
        if stored_history_covers_requirement(
            stock, limit=limit, expect_through=state.trading_day
        ):
            return self._history_loader(stock, limit, max_month_fetches=0)

        flight_key, compatibility = history_flight_identity(
            stock.id,
            limit=limit,
            expect_through=state.trading_day,
        )
        owner_token = f"{state.scan_id}:{uuid4().hex[:12]}"
        deadline = time.monotonic() + HISTORY_FLIGHT_WAIT_MAX_SECONDS
        sleep_for = HISTORY_FLIGHT_POLL_INITIAL_SECONDS
        acquired_generation: int | None = None

        while True:
            try:
                claim = tw_research_repository.acquire_history_flight(
                    flight_key=flight_key,
                    stock_id=stock.id,
                    compatibility_key=compatibility,
                    owner_token=owner_token,
                    lease_seconds=HISTORY_FLIGHT_LEASE_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                raise TwResearchDurableStoreError(
                    f"History flight acquire failed ({type(exc).__name__}).",
                    scan_id=state.scan_id,
                    operation="history_flight",
                ) from exc

            status = str(claim.get("status") or "")
            if status == "acquired":
                acquired_generation = int(claim["generation"])
                break
            if status == "ready":
                if stored_history_covers_requirement(
                    stock, limit=limit, expect_through=state.trading_day
                ):
                    return self._history_loader(stock, limit, max_month_fetches=0)
                # Misleading/expired ready without coverage — keep waiting or reclaim.
            if time.monotonic() >= deadline:
                try:
                    claim = tw_research_repository.acquire_history_flight(
                        flight_key=flight_key,
                        stock_id=stock.id,
                        compatibility_key=compatibility,
                        owner_token=owner_token,
                        lease_seconds=HISTORY_FLIGHT_LEASE_SECONDS,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise TwResearchDurableStoreError(
                        f"History flight acquire failed ({type(exc).__name__}).",
                        scan_id=state.scan_id,
                        operation="history_flight",
                    ) from exc
                if claim.get("status") == "acquired":
                    acquired_generation = int(claim["generation"])
                    break
                # Still busy without validated coverage: do not persist a
                # definitive source_incomplete outcome for a temporary wait.
                raise TwResearchHistoryCoordinationError(
                    "History flight wait timed out without ownership or "
                    "validated coverage; retry after the leader finishes.",
                    scan_id=state.scan_id,
                    stock_id=stock.id,
                )
            time.sleep(sleep_for)
            sleep_for = min(sleep_for * 2, HISTORY_FLIGHT_POLL_MAX_SECONDS)

        if acquired_generation is None:
            raise TwResearchHistoryCoordinationError(
                "History flight acquired without a generation; retry reclaim.",
                scan_id=state.scan_id,
                stock_id=stock.id,
            )
        stop_renew = Event()
        renew_state = {"stale": False, "consecutive_failures": 0}

        def _renew_loop() -> None:
            while not stop_renew.wait(HISTORY_FLIGHT_RENEW_INTERVAL_SECONDS):
                try:
                    renewed = tw_research_repository.renew_history_flight(
                        flight_key=flight_key,
                        owner_token=owner_token,
                        generation=acquired_generation,
                        lease_seconds=HISTORY_FLIGHT_LEASE_SECONDS,
                    )
                except Exception:  # noqa: BLE001 — bound consecutive outages
                    renew_state["consecutive_failures"] = (
                        int(renew_state["consecutive_failures"]) + 1
                    )
                    logger.warning(
                        "tw_research history flight renew failed",
                        exc_info=True,
                        extra={
                            "stock_id": stock.id,
                            "flight_key": flight_key,
                            "consecutive_failures": renew_state["consecutive_failures"],
                        },
                    )
                    if (
                        int(renew_state["consecutive_failures"])
                        >= HISTORY_FLIGHT_MAX_CONSECUTIVE_RENEW_FAILURES
                    ):
                        # Fail closed: lease may already be stolen; do not publish ready.
                        renew_state["stale"] = True
                        return
                    continue
                renew_state["consecutive_failures"] = 0
                if not renewed.get("ok"):
                    renew_state["stale"] = True
                    return

        renew_thread = Thread(target=_renew_loop, name="tw-history-flight-renew", daemon=True)
        renew_thread.start()
        success = False
        coverage_complete = False
        candles: list[CandleRecord] = []
        months_used = 0
        try:
            candles, months_used = self._history_loader(
                stock, limit, max_month_fetches=remaining_budget
            )
            success = True
            coverage_complete = stored_history_covers_requirement(
                stock, limit=limit, expect_through=state.trading_day
            )
        finally:
            stop_renew.set()
            renew_thread.join(timeout=1.0)
            try:
                # Stale / ownership-uncertain leaders must not publish ready.
                # In-flight HTTP may already have persisted bars idempotently;
                # coordination fails closed so waiters re-acquire before more
                # upstream work rather than trusting a false ready marker.
                if renew_state["stale"]:
                    tw_research_repository.complete_history_flight(
                        flight_key=flight_key,
                        owner_token=owner_token,
                        generation=acquired_generation,
                        success=False,
                        coverage_complete=False,
                    )
                else:
                    tw_research_repository.complete_history_flight(
                        flight_key=flight_key,
                        owner_token=owner_token,
                        generation=acquired_generation,
                        success=success,
                        coverage_complete=coverage_complete,
                    )
            except Exception:  # noqa: BLE001 — lease expiry recovers
                logger.warning(
                    "tw_research history flight complete failed",
                    exc_info=True,
                    extra={"stock_id": stock.id, "flight_key": flight_key},
                )
        if renew_state["stale"]:
            # Ownership lost during fetch: do not return a coordinated success
            # that would drive screening/persistence under a stolen lease.
            raise TwResearchHistoryCoordinationError(
                "History flight lease lost or uncertain during upstream fetch; "
                "retry after reclaim.",
                scan_id=state.scan_id,
                stock_id=stock.id,
            )
        return candles, months_used

    def _claim_batch_ids(self, state: ScanState, *, batch_size: int) -> list[str] | None:
        """Reserve next symbol slice; None means already complete.

        When ``persist=True``, reservations must succeed in PostgreSQL. Failures
        raise ``TwResearchDurableStoreError`` and never fall back to memory.
        """
        # budget_exhausted is handled before claim (explicit resume required).
        terminal = {"completed_processing", "verified_ready"}
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
                max_in_flight=DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
            ),
        )
        if claimed is None:
            raise TwResearchDurableStoreError(
                "Durable scan row missing for claim.",
                scan_id=state.scan_id,
                operation="claim_scan_batch",
            )
        if claimed.get("admission_denied"):
            raise TwResearchAdmissionError(
                "Too many in-flight Taiwan research batches; retry after a lease expires.",
                scan_id=state.scan_id,
                operation="run_batch",
                in_flight=int(claimed.get("in_flight") or 0),
                limit=int(claimed.get("limit") or DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES),
            )
        # Pull peer-worker symbol results / counters before processing.
        self._hydrate_state_from_durable(state)
        with self._lock:
            reserved_from = int(claimed.get("reserved_from", state.cursor))
            batch_ids = list(claimed.get("batch_ids") or [])
            # In-memory cursor tracks the reservation end for timeout rewind.
            state.cursor = reserved_from + len(batch_ids)
            state.status = claimed["scan"]["status"]  # type: ignore[assignment]
            state.scan_lease_generation = int(
                claimed.get("lease_generation")
                or claimed["scan"].get("scan_lease_generation")
                or 0
            )
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
        lease_generation = int(getattr(state, "scan_lease_generation", 0) or 0) or None
        completed = self._run_durable(
            scan_id=state.scan_id,
            operation="complete_scan_batch",
            action=lambda: repo.complete_scan_batch(
                state.scan_id,
                status=state.status,
                lease_generation=lease_generation,
            ),
            failure_note="complete_batch",
            state=state,
        )
        if completed is None:
            return
        if completed.get("stale_lease"):
            with self._lock:
                note = "scan_batch_lease_stale_complete_rejected"
                if note not in state.notes:
                    state.notes.append(note)
            self._hydrate_state_from_durable(state)
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
            state.scan_lease_generation = int(
                getattr(loaded, "scan_lease_generation", 0) or 0
            )
            state.budget_windows_opened = int(
                getattr(loaded, "budget_windows_opened", 0) or 0
            )
            state.max_upstream_month_fetches = int(
                getattr(loaded, "max_upstream_month_fetches", state.max_upstream_month_fetches)
            )
            state.max_enrichments_per_run = int(
                getattr(loaded, "max_enrichments_per_run", state.max_enrichments_per_run)
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
                _DURABLE_POSTGRES_UNAVAILABLE,
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
                _DURABLE_POSTGRES_UNAVAILABLE,
                scan_id=state.scan_id,
                operation="persist_symbol",
            )
        enrich_lease_generation = None
        scan_lease_generation = None
        if record.enrichment is not None:
            # Enrichment path: fence enrichment only (independent of scan batch).
            enrich_lease_generation = (
                int(getattr(state, "enrich_lease_generation", 0) or 0) or None
            )
        else:
            # Technical screening under a claimed scan batch (generation > 0).
            # Seed rows / fixtures leave generation at 0 and remain unfenced.
            scan_lease_generation = (
                int(getattr(state, "scan_lease_generation", 0) or 0) or None
            )
        if not persist_symbol(
            state,
            record,
            enrich_lease_generation=enrich_lease_generation,
            scan_lease_generation=scan_lease_generation,
        ):
            if "scan_lease_stale_write_rejected" in state.notes:
                raise TwResearchScanLeaseStaleError(
                    f"Stale scan-batch lease rejected technical write for {record.stock_id}.",
                    scan_id=state.scan_id,
                    stock_id=record.stock_id,
                )
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
    return datetime.combine(day, dt_time(14, 0), tzinfo=TAIPEI)


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
