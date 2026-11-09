"""Bounded configurable Taiwan research batch scan (#312/#318/#320).

Stage A: daily close + completed-day MACD/KD pass for each symbol in the batch.
Stage B: for day-pass symbols only, completed-month indicators, SMAs, 52-week
position. Strict eligibility never changes to fill target_count.

Checkpoint contract key:
  (trading_day, universe_version, calculation_version, symbol, scan_id)

Checkpoints and scan ledgers are persisted to PostgreSQL when DATABASE_URL is
configured (#318). An in-memory cache still accelerates a single process; GET
paths fall back to durable rows after restart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
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
    created_at: str = ""
    updated_at: str = ""
    notes: list[str] = field(default_factory=list)
    user_id: str | None = None
    active_snapshot_id: str | None = None
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


class TwResearchScanService:
    def __init__(
        self,
        *,
        history_loader: HistoryLoader | None = None,
        stock_loader: StockLoader | None = None,
        persist: bool = True,
    ) -> None:
        self._history_loader = history_loader or _default_history_loader
        self._stock_loader = stock_loader or quote_service.get_catalog_stock
        self._persist = persist
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
        self._persist_scan(state)
        for record in state.records.values():
            self._persist_symbol(state, record)
        return state

    def get_scan(self, scan_id: str) -> ScanState | None:
        with self._lock:
            cached = self._scans.get(scan_id)
        if cached is not None:
            return cached
        if not self._persist:
            return None
        from app.services.tw_research.persistence import load_scan_state

        loaded = load_scan_state(scan_id)
        if loaded is None:
            return None
        with self._lock:
            self._scans[scan_id] = loaded
            for stock_id, record in loaded.records.items():
                if record.checkpoint is not None:
                    self._checkpoints.add(record.checkpoint)
                else:
                    self._checkpoints.add(
                        (
                            loaded.trading_day,
                            loaded.universe_version,
                            loaded.calculation_version,
                            stock_id,
                        )
                    )
        return loaded

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

        state = self.get_scan(scan_id)
        if state is None:
            raise KeyError(f"Unknown scan_id: {scan_id}")

        # Serialize overlapping batch POSTs for the same scan (process lock +
        # durable SELECT FOR UPDATE claim when Postgres is available).
        with state.run_lock:
            batch_ids = self._claim_batch_ids(state, batch_size=batch_size)
            if batch_ids is None:
                return state

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
                        self._persist_scan(state)
                        self._rewind_durable_cursor(state)
                    return state
                try:
                    self._process_symbol(state, stock_id, now=calculation_now)
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
                self._refresh_status_locked(state)
                state.updated_at = datetime.now(TAIPEI).isoformat()
                state.notes.append(
                    "target_count is an output target only and does not relax pass rules."
                )
                self._persist_scan(state)
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
        daily_coverage = validate_ohlcv_series(
            completed,
            market="TW",
            check_calendar_gaps=True,
            allow_holiday_fetch=False,
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

        state = self.get_scan(scan_id)
        if state is None:
            raise KeyError(f"Unknown scan_id: {scan_id}")

        with state.run_lock:
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
                with self._lock:
                    record = state.records.get(stock_id)
                    if record is not None:
                        record.enrichment = payload
                        self._persist_symbol(state, record)
                    state.enrichments_run += 1

            with self._lock:
                state.updated_at = datetime.now(TAIPEI).isoformat()
                state.notes.append(
                    "Missing chip/valuation data is per-field unavailable; "
                    "it does not convert a technical pass into a fail."
                )
                self._persist_scan(state)
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
        elif incomplete:
            state.quality_status = "source_incomplete"
        elif state.status == "completed_processing" and verified and incomplete == 0:
            state.quality_status = "technically_qualified"
        elif qualified:
            state.quality_status = "technically_qualified"
        else:
            state.quality_status = "processed"

    def _maybe_finalize(self, state: ScanState) -> None:
        if not self._persist:
            return
        if state.quality_status != "technically_qualified":
            return
        if state.status != "completed_processing":
            return
        from app.services.tw_research.persistence import finalize_verified_snapshot

        finalized = finalize_verified_snapshot(state, user_id=state.user_id)
        if finalized is None and state.status != "failed":
            # Keep technically_qualified; never advertise verified without a snapshot.
            note = "finalize_deferred_or_failed"
            if note not in state.notes:
                state.notes.append(note)
            self._persist_scan(state)

    def _claim_batch_ids(self, state: ScanState, *, batch_size: int) -> list[str] | None:
        """Reserve next symbol slice; None means already complete."""
        terminal = {"completed_processing", "verified_ready", "budget_exhausted"}
        if self._persist:
            from app.repositories.tw_research_repository import tw_research_repository

            if tw_research_repository.is_available():
                try:
                    claimed = tw_research_repository.claim_scan_batch(
                        state.scan_id,
                        batch_size=batch_size,
                        terminal_statuses=terminal,
                    )
                except Exception:  # noqa: BLE001
                    claimed = None
                if claimed is not None:
                    with self._lock:
                        state.cursor = int(claimed["scan"]["cursor"])
                        state.status = claimed["scan"]["status"]  # type: ignore[assignment]
                        state.updated_at = datetime.now(TAIPEI).isoformat()
                    if claimed.get("already_complete"):
                        return None
                    return list(claimed.get("batch_ids") or [])

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

    def _claim_enrichment_ids(
        self, state: ScanState, *, batch_size: int
    ) -> tuple[list[str], int]:
        if self._persist:
            from app.repositories.tw_research_repository import tw_research_repository

            if tw_research_repository.is_available():
                try:
                    claimed = tw_research_repository.claim_enrichment_batch(
                        state.scan_id, batch_size=batch_size
                    )
                except Exception:  # noqa: BLE001
                    claimed = None
                if claimed is not None:
                    with self._lock:
                        state.enrich_cursor = int(claimed["scan"]["enrich_cursor"])
                        remaining = int(claimed["remaining_budget"])
                        pending = [
                            stock_id
                            for stock_id in claimed.get("batch_ids") or []
                            if stock_id in state.records
                            and state.records[stock_id].enrichment is None
                        ]
                    return pending, remaining

        with self._lock:
            pending = [
                stock_id
                for stock_id in state.qualified_ids[state.enrich_cursor :]
                if stock_id in state.records and state.records[stock_id].enrichment is None
            ]
            remaining_budget = state.max_enrichments_per_run - state.enrichments_run
            batch_ids = pending[: min(batch_size, max(0, remaining_budget))]
            if batch_ids:
                last = batch_ids[-1]
                if last in state.qualified_ids:
                    state.enrich_cursor = state.qualified_ids.index(last) + 1
            return batch_ids, remaining_budget

    def _rewind_durable_cursor(self, state: ScanState) -> None:
        if not self._persist:
            return
        from app.repositories.tw_research_repository import tw_research_repository

        if not tw_research_repository.is_available():
            return
        try:
            tw_research_repository.rewind_scan_cursor(
                state.scan_id, new_cursor=state.cursor, status=state.status
            )
        except Exception:  # noqa: BLE001
            state.notes.append("rewind_cursor_persist_failed")

    def _persist_scan(self, state: ScanState) -> None:
        if not self._persist:
            return
        from app.services.tw_research.persistence import persist_scan

        persist_scan(state, user_id=state.user_id)

    def _persist_symbol(self, state: ScanState, record: SymbolScanRecord) -> None:
        if not self._persist:
            return
        from app.services.tw_research.persistence import persist_symbol

        persist_symbol(state, record)


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
