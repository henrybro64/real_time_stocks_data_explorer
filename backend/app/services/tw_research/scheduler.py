"""Disabled-by-default post-market Taiwan research orchestration (#330).

Creates or reuses an idempotent scan for the schedule owner, runs a bounded
number of batches (and optional budget resume + enrichment), and never pretends
incomplete work is a verified success. The Kubernetes CronJob stays suspended
until an operator enables it after pilot measurement.

Continuation policy
-------------------
Each CronJob tick is independently bounded (batches, enrichment, wall clock).
Multiple weekday evening windows (see k8s CronJob schedule) continue the same
logical scan until technical screening, enrichment, and verified publication
finish. Incomplete scans from prior trading days within
``SCHEDULE_BACKLOG_MAX_CALENDAR_DAYS`` are resumed **before** opening a newer
day's scan, preserving the original trading-day calculation anchor.

A scan status of ``verified_ready`` with an immutable snapshot is **not**
sufficient to stop the scheduler when eligible enrichment remains and budget
allows — export publication and enrichment completion are independent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event, Thread
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.market_clock import is_trading_day, last_completed_session_date
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    DEFAULT_SCHEDULE_MAX_BATCHES,
    DEFAULT_SCHEDULE_MAX_ENRICH_BATCHES,
    DEFAULT_SCHEDULE_TICK_DEADLINE_SECONDS,
    INCOMPLETE_SCHEDULE_STATUSES,
    MAX_ENRICHMENTS_PER_SCAN_CAP,
    PREFERRED_DAILY_HISTORY_LIMIT,
    SCHEDULE_BACKLOG_MAX_CALENDAR_DAYS,
    SCHEDULE_DATA_READY_SAMPLE_SIZE,
    SCHEDULE_TICK_LEASE_SECONDS,
    SCHEDULE_TICK_RENEW_INTERVAL_SECONDS,
    UNIVERSE_PRESET_VERSION,
)
from app.services.tw_research.scan import (
    ScanState,
    TwResearchAdmissionError,
    TwResearchConflictError,
    TwResearchDurableStoreError,
    TwResearchScanService,
    stored_history_covers_requirement,
    tw_research_scan_service,
)
from app.services.tw_research.universe import UniverseSelector

TAIPEI = ZoneInfo("Asia/Taipei")
logger = logging.getLogger(__name__)

ScheduleOutcome = Literal[
    "dry_run",
    "skipped_nontrading_day",
    "skipped_data_not_ready",
    "skipped_market_data_not_ready",
    "skipped_disabled",
    "deferred_lock",
    "deferred_deadline",
    "deferred_lease_lost",
    "reused_scan",
    "created_scan",
    "completed_window",
    "technically_complete",
    "enrichment_partial",
    "enrichment_budget_exhausted",
    "verified_ready",
    "budget_exhausted",
    "partial",
    "failed",
]


@dataclass(frozen=True)
class ScheduleRunResult:
    outcome: ScheduleOutcome
    trading_day: str
    scan_id: str | None
    status: str | None
    quality_status: str | None
    batches_run: int
    enrich_batches_run: int
    resumed: bool
    notes: tuple[str, ...]
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "tradingDay": self.trading_day,
            "scanId": self.scan_id,
            "status": self.status,
            "qualityStatus": self.quality_status,
            "batchesRun": self.batches_run,
            "enrichBatchesRun": self.enrich_batches_run,
            "resumed": self.resumed,
            "notes": list(self.notes),
            "dryRun": self.dry_run,
        }


class _TickLeaseHeartbeat:
    """Renew the schedule tick lock while a long batch runs (#330 Fix 2)."""

    def __init__(
        self,
        *,
        repo: Any,
        lock_name: str,
        owner_token: str,
        lease_seconds: int = SCHEDULE_TICK_LEASE_SECONDS,
        interval_seconds: float = SCHEDULE_TICK_RENEW_INTERVAL_SECONDS,
    ) -> None:
        self._repo = repo
        self._lock_name = lock_name
        self._owner_token = owner_token
        self._lease_seconds = lease_seconds
        # Tests may use sub-second intervals; production uses ~40s.
        self._interval = max(0.05, float(interval_seconds))
        self._stop = Event()
        self._thread: Thread | None = None
        self.lost = False

    def __enter__(self) -> _TickLeaseHeartbeat:
        self._thread = Thread(
            target=self._loop,
            name="tw-schedule-tick-renew",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _renew_once(self) -> bool:
        """Return True while ownership is still held."""
        try:
            renewed = self._repo.renew_schedule_tick_lock(
                lock_name=self._lock_name,
                owner_token=self._owner_token,
                lease_seconds=self._lease_seconds,
            )
        except Exception:  # noqa: BLE001 — treat as ownership uncertainty
            logger.warning(
                "tw_research schedule tick renew failed",
                exc_info=True,
            )
            self.lost = True
            return False
        if not renewed.get("ok"):
            self.lost = True
            return False
        return True

    def _loop(self) -> None:
        # Immediate renew so a batch that starts near lease expiry stays owned,
        # then continue on the configured interval until stopped.
        if not self._renew_once():
            return
        while not self._stop.wait(self._interval):
            if not self._renew_once():
                return


def pending_enrichment_ids(state: ScanState) -> list[str]:
    """Qualified symbols that still lack an enrichment payload."""
    return [
        stock_id
        for stock_id in state.qualified_ids
        if stock_id in state.records and state.records[stock_id].enrichment is None
    ]


def enrichment_budget_remaining(state: ScanState) -> int:
    return max(0, int(state.max_enrichments_per_run) - int(state.enrichments_run))


def enrichment_window_available(state: ScanState) -> bool:
    """True when another bounded enrichment window can still be opened."""
    return int(state.max_enrichments_per_run) < MAX_ENRICHMENTS_PER_SCAN_CAP


def schedule_has_remaining_work(state: ScanState) -> bool:
    """True when a later schedule tick can still make progress on ``state``."""
    if state.status == "failed":
        return False
    if state.status in {"queued", "running", "partial", "budget_exhausted"}:
        return True
    if state.status in {"completed_processing", "verified_ready"}:
        pending = pending_enrichment_ids(state)
        if not pending:
            return False
        if enrichment_budget_remaining(state) > 0:
            return True
        # Spent window with pending work is still actionable via resume (#330).
        return enrichment_window_available(state)
    return False


def resolve_schedule_trading_day(
    *,
    now: datetime | None = None,
    override: str | None = None,
    allow_holiday_fetch: bool = False,
) -> tuple[str, ScheduleOutcome | None]:
    """Return ``(trading_day, skip_outcome)``.

    ``skip_outcome`` is set when the tick should not create work (holiday or
    incomplete session). An explicit ``override`` always proceeds.
    """
    if override:
        return override, None
    current = now or datetime.now(TAIPEI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TAIPEI)
    else:
        current = current.astimezone(TAIPEI)
    today = current.date()
    if not is_trading_day("TW", today, allow_holiday_fetch=allow_holiday_fetch):
        completed = last_completed_session_date(
            "TW", current, allow_holiday_fetch=allow_holiday_fetch
        )
        return (completed or today).isoformat(), "skipped_nontrading_day"
    completed = last_completed_session_date(
        "TW", current, allow_holiday_fetch=allow_holiday_fetch
    )
    if completed is None or completed < today:
        return today.isoformat(), "skipped_data_not_ready"
    return completed.isoformat(), None


def market_ohlcv_ready_for_trading_day(
    trading_day: str,
    *,
    sample_stock_ids: tuple[str, ...] | None = None,
    stock_loader: Any | None = None,
) -> bool:
    """Lightweight post-close OHLCV readiness check (#330 Fix 4).

    Session completion is necessary but not sufficient. We peek a small sample
    of liquid TW symbols from the starter preset; if **any** sample already has
    persisted bars through ``trading_day``, the daily pipeline has landed and
    research may begin. Unavailable symbols remain independently recoverable.
    """
    from app.services.tw_research.preset import starter_preset_stock_ids

    samples = sample_stock_ids or tuple(
        starter_preset_stock_ids()[:SCHEDULE_DATA_READY_SAMPLE_SIZE]
    )
    if not samples:
        return False
    loader = stock_loader
    if loader is None:
        from app.services.quote_service import quote_service

        loader = quote_service.get_stock
    for stock_id in samples:
        stock = loader(stock_id) if callable(loader) else None
        if stock is None:
            continue
        if stored_history_covers_requirement(
            stock,
            limit=PREFERRED_DAILY_HISTORY_LIMIT,
            expect_through=trading_day,
        ):
            return True
    return False


def run_scheduled_research(
    *,
    user_id: str,
    enabled: bool,
    dry_run: bool = False,
    max_batches: int = DEFAULT_SCHEDULE_MAX_BATCHES,
    max_enrich_batches: int = DEFAULT_SCHEDULE_MAX_ENRICH_BATCHES,
    target_count: int = 10,
    trading_day: str | None = None,
    resume_if_exhausted: bool = True,
    now: datetime | None = None,
    service: TwResearchScanService | None = None,
    allow_holiday_fetch: bool = False,
    tick_deadline_seconds: int = DEFAULT_SCHEDULE_TICK_DEADLINE_SECONDS,
    require_market_data: bool = True,
) -> ScheduleRunResult:
    """Idempotent bounded schedule tick for the starter-preset universe."""
    notes: list[str] = []
    day, skip = resolve_schedule_trading_day(
        now=now, override=trading_day, allow_holiday_fetch=allow_holiday_fetch
    )
    if not enabled:
        return ScheduleRunResult(
            outcome="skipped_disabled",
            trading_day=day,
            scan_id=None,
            status=None,
            quality_status=None,
            batches_run=0,
            enrich_batches_run=0,
            resumed=False,
            notes=("schedule_disabled",),
            dry_run=dry_run,
        )

    if skip is not None and trading_day is None:
        if skip == "skipped_data_not_ready":
            return ScheduleRunResult(
                outcome=skip,
                trading_day=day,
                scan_id=None,
                status=None,
                quality_status=None,
                batches_run=0,
                enrich_batches_run=0,
                resumed=False,
                notes=(skip,),
                dry_run=dry_run,
            )
        notes.append(skip)

    if dry_run:
        return ScheduleRunResult(
            outcome="dry_run",
            trading_day=day,
            scan_id=None,
            status=None,
            quality_status=None,
            batches_run=0,
            enrich_batches_run=0,
            resumed=False,
            notes=(
                "dry_run_plan",
                f"max_batches={max_batches}",
                f"max_enrich_batches={max_enrich_batches}",
                f"target_count={target_count}",
            ),
            dry_run=True,
        )

    scan_service = service or tw_research_scan_service
    # Post-close OHLCV readiness for *creating* today's work. Backlog for prior
    # days still proceeds (those sessions already had a chance to land data).
    if (
        require_market_data
        and trading_day is None
        and skip is None
        and not market_ohlcv_ready_for_trading_day(
            day,
            stock_loader=getattr(scan_service, "_stock_loader", None),
        )
    ):
        notes.append("market_ohlcv_not_ready")
        # Still try backlog-only below by disallowing create.
        skip = "skipped_market_data_not_ready"
        notes.append(skip)

    tick_token = uuid4().hex
    lock_name = f"schedule:{user_id}:starter_preset:{CALCULATION_VERSION}:t{target_count}"
    lock_held = False
    repo = None
    # Durable schedule ticks must hold the PostgreSQL tick lock. Never fall
    # through to uncoordinated work when persist=True (#330 Fix 1).
    if getattr(scan_service, "_persist", True):
        try:
            from app.repositories.tw_research_repository import tw_research_repository

            repo = tw_research_repository
            if not repo.is_available():
                return ScheduleRunResult(
                    outcome="deferred_lock",
                    trading_day=day,
                    scan_id=None,
                    status=None,
                    quality_status=None,
                    batches_run=0,
                    enrich_batches_run=0,
                    resumed=False,
                    notes=("schedule_coordination_unavailable",),
                    dry_run=False,
                )
            claimed = repo.claim_schedule_tick_lock(
                lock_name=lock_name,
                owner_token=tick_token,
                lease_seconds=SCHEDULE_TICK_LEASE_SECONDS,
            )
            if not claimed.get("acquired"):
                return ScheduleRunResult(
                    outcome="deferred_lock",
                    trading_day=day,
                    scan_id=None,
                    status=None,
                    quality_status=None,
                    batches_run=0,
                    enrich_batches_run=0,
                    resumed=False,
                    notes=("schedule_tick_lock_held",),
                    dry_run=False,
                )
            lock_held = True
        except Exception:  # noqa: BLE001 — fail closed on coordination errors
            logger.exception("tw_research schedule tick lock failed")
            return ScheduleRunResult(
                outcome="failed",
                trading_day=day,
                scan_id=None,
                status=None,
                quality_status=None,
                batches_run=0,
                enrich_batches_run=0,
                resumed=False,
                notes=("schedule_tick_lock_error",),
                dry_run=False,
            )

    try:
        return _run_scheduled_research_locked(
            scan_service=scan_service,
            user_id=user_id,
            preferred_day=day,
            allow_create_preferred=(skip is None or trading_day is not None),
            max_batches=max_batches,
            max_enrich_batches=max_enrich_batches,
            target_count=target_count,
            resume_if_exhausted=resume_if_exhausted,
            now=now,
            notes=notes,
            tick_deadline_seconds=tick_deadline_seconds,
            lock_name=lock_name if lock_held else None,
            tick_token=tick_token if lock_held else None,
            repo=repo if lock_held else None,
            market_data_skip=skip if skip == "skipped_market_data_not_ready" else None,
        )
    finally:
        if lock_held and repo is not None:
            try:
                repo.release_schedule_tick_lock(
                    lock_name=lock_name,
                    owner_token=tick_token,
                )
            except Exception:  # noqa: BLE001 — lease expiry recovers
                logger.warning(
                    "tw_research schedule tick lock release failed",
                    exc_info=True,
                )


def _run_scheduled_research_locked(
    *,
    scan_service: TwResearchScanService,
    user_id: str,
    preferred_day: str,
    allow_create_preferred: bool,
    max_batches: int,
    max_enrich_batches: int,
    target_count: int,
    resume_if_exhausted: bool,
    now: datetime | None,
    notes: list[str],
    tick_deadline_seconds: int,
    lock_name: str | None,
    tick_token: str | None,
    repo: Any,
    market_data_skip: ScheduleOutcome | None,
) -> ScheduleRunResult:
    selector = UniverseSelector(mode="starter_preset")
    universe_version = UNIVERSE_PRESET_VERSION
    started = time.monotonic()
    last_renew = started

    def _maybe_renew() -> ScheduleOutcome | None:
        nonlocal last_renew
        if lock_name is None or tick_token is None or repo is None:
            return None
        if time.monotonic() - last_renew < SCHEDULE_TICK_RENEW_INTERVAL_SECONDS:
            return None
        try:
            renewed = repo.renew_schedule_tick_lock(
                lock_name=lock_name,
                owner_token=tick_token,
                lease_seconds=SCHEDULE_TICK_LEASE_SECONDS,
            )
        except Exception:  # noqa: BLE001
            notes.append("schedule_tick_renew_error")
            return "deferred_lease_lost"
        last_renew = time.monotonic()
        if not renewed.get("ok"):
            notes.append("schedule_tick_lease_lost")
            return "deferred_lease_lost"
        return None

    def _deadline_hit() -> bool:
        return (time.monotonic() - started) >= max(1, int(tick_deadline_seconds))

    def _run_with_heartbeat(action):  # noqa: ANN001
        if lock_name is None or tick_token is None or repo is None:
            return action(), None
        with _TickLeaseHeartbeat(
            repo=repo,
            lock_name=lock_name,
            owner_token=tick_token,
        ) as heartbeat:
            result = action()
            if heartbeat.lost:
                notes.append("schedule_tick_lease_lost_during_batch")
                return result, "deferred_lease_lost"
            return result, None

    existing = _select_scan_for_tick(
        scan_service,
        user_id=user_id,
        preferred_day=preferred_day,
        universe_version=universe_version,
        calculation_version=CALCULATION_VERSION,
        target_count=target_count,
        now=now,
    )
    resumed = False
    created = False
    if existing is None:
        if not allow_create_preferred:
            notes.append("no_backlog_and_create_skipped")
            outcome: ScheduleOutcome = market_data_skip or (
                "skipped_nontrading_day"
                if "skipped_nontrading_day" in notes
                else "partial"
            )
            return ScheduleRunResult(
                outcome=outcome,
                trading_day=preferred_day,
                scan_id=None,
                status=None,
                quality_status=None,
                batches_run=0,
                enrich_batches_run=0,
                resumed=False,
                notes=tuple(notes),
                dry_run=False,
            )
        state = scan_service.create_scan(
            selector,
            target_count=target_count,  # type: ignore[arg-type]
            trading_day=preferred_day,
            user_id=user_id,
            now=now,
        )
        created = True
        notes.append("created_scan")
    else:
        state = scan_service.get_scan(existing["scan_id"])
        if state is None:
            raise TwResearchDurableStoreError(
                "Scheduled scan disappeared after lookup",
                scan_id=str(existing["scan_id"]),
                operation="schedule_run",
            )
        if state.trading_day != preferred_day:
            notes.append(f"backlog_resume:{state.trading_day}")
        else:
            notes.append("reused_scan")

    day = state.trading_day

    # Snapshot + verified_ready does not stop pending enrichment.
    # Same-day completed technical scans with no remaining enrichment must not
    # create a duplicate scan — report completion and exit.
    pending_upfront = pending_enrichment_ids(state)
    if (
        state.status == "verified_ready"
        and state.active_snapshot_id
        and pending_upfront
    ):
        notes.append("verified_snapshot_enrichment_pending")

    if not schedule_has_remaining_work(state):
        if (
            state.status in {"verified_ready", "completed_processing"}
            and pending_upfront
            and enrichment_budget_remaining(state) <= 0
            and not enrichment_window_available(state)
        ):
            notes.append("enrichment_budget_exhausted")
            notes.append("enrichment_cap_reached")
            return ScheduleRunResult(
                outcome="enrichment_budget_exhausted",
                trading_day=day,
                scan_id=state.scan_id,
                status=state.status,
                quality_status=state.quality_status,
                batches_run=0,
                enrich_batches_run=0,
                resumed=False,
                notes=tuple(notes),
                dry_run=False,
            )
        if state.status == "verified_ready" and state.active_snapshot_id:
            notes.append("already_verified_ready")
            return ScheduleRunResult(
                outcome="verified_ready",
                trading_day=day,
                scan_id=state.scan_id,
                status=state.status,
                quality_status=state.quality_status,
                batches_run=0,
                enrich_batches_run=0,
                resumed=False,
                notes=tuple(notes),
                dry_run=False,
            )
        if state.status == "completed_processing":
            notes.append("already_technically_complete")
            return ScheduleRunResult(
                outcome="technically_complete",
                trading_day=day,
                scan_id=state.scan_id,
                status=state.status,
                quality_status=state.quality_status,
                batches_run=0,
                enrich_batches_run=0,
                resumed=False,
                notes=tuple(notes),
                dry_run=False,
            )

    if state.status == "budget_exhausted" and resume_if_exhausted:
        try:
            state = scan_service.resume_budget_window(state.scan_id)
            resumed = True
            notes.append("budget_window_resumed")
        except TwResearchConflictError as exc:
            notes.append(f"resume_conflict:{exc.code}")

    # Open another enrichment window when the current allowance is spent but
    # persisted qualified symbols still lack enrichment (#330 Fix 3).
    if (
        resume_if_exhausted
        and pending_enrichment_ids(state)
        and enrichment_budget_remaining(state) <= 0
        and enrichment_window_available(state)
    ):
        try:
            state = scan_service.resume_enrichment_window(state.scan_id)
            resumed = True
            notes.append("enrichment_window_resumed")
        except TwResearchConflictError as exc:
            notes.append(f"enrichment_resume_conflict:{exc.code}")
            if exc.code == "enrichment_cap_reached":
                return ScheduleRunResult(
                    outcome="enrichment_budget_exhausted",
                    trading_day=day,
                    scan_id=state.scan_id,
                    status=state.status,
                    quality_status=state.quality_status,
                    batches_run=0,
                    enrich_batches_run=0,
                    resumed=resumed,
                    notes=tuple(notes),
                    dry_run=False,
                )

    batches_run = 0
    enrich_batches_run = 0
    deferred: ScheduleOutcome | None = None
    try:
        while batches_run < max_batches and state.status in {
            "queued",
            "running",
            "partial",
        }:
            if _deadline_hit():
                notes.append("tick_deadline_stop")
                deferred = "deferred_deadline"
                break
            lost = _maybe_renew()
            if lost is not None:
                deferred = lost
                break
            before = (
                state.cursor,
                state.upstream_month_fetches,
                state.status,
                len(state.records),
            )
            state, lost = _run_with_heartbeat(
                lambda: scan_service.run_batch(
                    state.scan_id,
                    now=_calculation_now_for_scan(state.trading_day, override=now),
                )
            )
            batches_run += 1
            if lost is not None:
                deferred = lost
                break
            after = (
                state.cursor,
                state.upstream_month_fetches,
                state.status,
                len(state.records),
            )
            if after == before:
                notes.append("batch_noop_stop")
                break
            if state.status == "budget_exhausted":
                notes.append("budget_exhausted_stop")
                break
            if state.status in {"completed_processing", "verified_ready"}:
                break

        while (
            deferred is None
            and enrich_batches_run < max_enrich_batches
            and state.status
            in {"completed_processing", "partial", "verified_ready", "running"}
            and state.qualified_ids
        ):
            if _deadline_hit():
                notes.append("tick_deadline_stop")
                deferred = "deferred_deadline"
                break
            lost = _maybe_renew()
            if lost is not None:
                deferred = lost
                break
            pending = pending_enrichment_ids(state)
            if not pending:
                notes.append("enrichment_caught_up")
                break
            if enrichment_budget_remaining(state) <= 0:
                if resume_if_exhausted and enrichment_window_available(state):
                    try:
                        state = scan_service.resume_enrichment_window(state.scan_id)
                        resumed = True
                        notes.append("enrichment_window_resumed")
                    except TwResearchConflictError as exc:
                        notes.append(f"enrichment_resume_conflict:{exc.code}")
                        notes.append("enrichment_budget_exhausted")
                        deferred = "enrichment_budget_exhausted"
                        break
                else:
                    notes.append("enrichment_budget_exhausted")
                    deferred = "enrichment_budget_exhausted"
                    break
            before_enrich = (
                state.enrichments_run,
                state.enrich_cursor,
                state.active_snapshot_id,
                len(pending),
            )
            state, lost = _run_with_heartbeat(
                lambda: scan_service.run_enrichment_batch(
                    state.scan_id,
                    now=_calculation_now_for_scan(state.trading_day, override=now),
                )
            )
            enrich_batches_run += 1
            if lost is not None:
                deferred = lost
                break
            after_pending = pending_enrichment_ids(state)
            after_enrich = (
                state.enrichments_run,
                state.enrich_cursor,
                state.active_snapshot_id,
                len(after_pending),
            )
            if after_enrich == before_enrich:
                notes.append("enrichment_noop_stop")
                break
    except TwResearchConflictError as exc:
        notes.append(f"conflict:{exc.code}")
    except TwResearchAdmissionError as exc:
        notes.append(f"admission_limited:in_flight={exc.in_flight}")
    except TwResearchDurableStoreError as exc:
        notes.append(f"durable_error:{exc.operation}")
        logger.exception("tw_research schedule durable failure")
        return ScheduleRunResult(
            outcome="failed",
            trading_day=day,
            scan_id=state.scan_id,
            status=state.status,
            quality_status=state.quality_status,
            batches_run=batches_run,
            enrich_batches_run=enrich_batches_run,
            resumed=resumed,
            notes=tuple(notes),
            dry_run=False,
        )

    if deferred is not None:
        outcome = deferred
    else:
        outcome = _classify_outcome(
            state=state,
            created=created,
            existing=existing,
            batches_run=batches_run,
            enrich_batches_run=enrich_batches_run,
            resumed=resumed,
        )
    notes.append(f"schedule_tick_batches={batches_run}")
    notes.append(f"schedule_tick_enrich_batches={enrich_batches_run}")
    return ScheduleRunResult(
        outcome=outcome,
        trading_day=day,
        scan_id=state.scan_id,
        status=state.status,
        quality_status=state.quality_status,
        batches_run=batches_run,
        enrich_batches_run=enrich_batches_run,
        resumed=resumed,
        notes=tuple(notes),
        dry_run=False,
    )


def _calculation_now_for_scan(
    trading_day: str, *, override: datetime | None
) -> datetime | None:
    """Keep indicator ``now`` anchored to the scan trading day unless overridden."""
    if override is not None:
        return override
    try:
        day = datetime.fromisoformat(trading_day).date()
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, 14, 0, tzinfo=TAIPEI)


def _classify_outcome(
    *,
    state: Any,
    created: bool,
    existing: dict[str, Any] | None,
    batches_run: int,
    enrich_batches_run: int,
    resumed: bool,
) -> ScheduleOutcome:
    pending = pending_enrichment_ids(state)
    if state.status == "verified_ready" and state.active_snapshot_id and not pending:
        return "verified_ready"
    if state.status == "verified_ready" and pending:
        if enrichment_budget_remaining(state) <= 0:
            return "enrichment_budget_exhausted"
        return "enrichment_partial"
    if state.status == "completed_processing":
        if pending:
            if enrichment_budget_remaining(state) <= 0:
                return "enrichment_budget_exhausted"
            return "enrichment_partial"
        return "technically_complete"
    if state.status == "budget_exhausted":
        return "budget_exhausted"
    if created and batches_run == 0 and enrich_batches_run == 0:
        return "created_scan"
    if existing is not None and batches_run == 0 and enrich_batches_run == 0 and not resumed:
        return "reused_scan"
    if state.status in {"completed_processing", "verified_ready"}:
        return "completed_window"
    return "partial"


def _select_scan_for_tick(
    service: TwResearchScanService,
    *,
    user_id: str,
    preferred_day: str,
    universe_version: str,
    calculation_version: str,
    target_count: int,
    now: datetime | None,
) -> dict[str, Any] | None:
    """Prefer oldest backlog with remaining work; else reuse preferred-day scan.

    A preferred-day scan that is already complete (no remaining technical or
    enrichment work) is still returned so the tick reports idempotent completion
    instead of creating a duplicate same-day scan.
    """
    current = now or datetime.now(TAIPEI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TAIPEI)
    else:
        current = current.astimezone(TAIPEI)
    min_day = (current.date() - timedelta(days=SCHEDULE_BACKLOG_MAX_CALENDAR_DAYS)).isoformat()

    candidates: list[dict[str, Any]] = []
    preferred_day_row: dict[str, Any] | None = None
    if getattr(service, "_persist", True):
        try:
            from app.repositories.tw_research_repository import tw_research_repository

            if tw_research_repository.is_available():
                candidates.extend(
                    tw_research_repository.find_incomplete_backlog_scans(
                        user_id=user_id,
                        universe_version=universe_version,
                        calculation_version=calculation_version,
                        selector_mode="starter_preset",
                        target_count=target_count,
                        min_trading_day=min_day,
                        max_trading_day=preferred_day,
                    )
                )
                preferred_day_row = tw_research_repository.find_compatible_scan(
                    user_id=user_id,
                    trading_day=preferred_day,
                    universe_version=universe_version,
                    calculation_version=calculation_version,
                    selector_mode="starter_preset",
                    target_count=target_count,
                )
                if preferred_day_row is not None and not any(
                    (row.get("scan_id") or row.get("scanId"))
                    == (preferred_day_row.get("scan_id") or preferred_day_row.get("scanId"))
                    for row in candidates
                ):
                    candidates.append(preferred_day_row)
        except Exception:  # noqa: BLE001
            logger.warning("tw_research schedule scan selection failed", exc_info=True)

    if not candidates:
        mem = _find_reusable_scan_memory(
            service,
            user_id=user_id,
            preferred_day=preferred_day,
            # Preferred day itself is always eligible; backlog window only
            # bounds older incomplete days.
            min_trading_day=min(min_day, preferred_day),
        )
        if mem is not None:
            candidates.append(mem)

    first_done: dict[str, Any] | None = None
    for candidate in candidates:
        scan_id = candidate.get("scan_id") or candidate.get("scanId")
        if not scan_id:
            continue
        state = service.get_scan(str(scan_id))
        if state is None:
            continue
        if schedule_has_remaining_work(state):
            return {"scan_id": state.scan_id, "status": state.status}
        # Remember preferred-day completed scan for idempotent exit.
        if state.trading_day == preferred_day and first_done is None:
            first_done = {"scan_id": state.scan_id, "status": state.status}

    if first_done is not None:
        return first_done

    # Preferred-day row may be complete and therefore absent from incomplete
    # backlog status filtering — still reuse it to avoid duplicate creates.
    if preferred_day_row is not None:
        scan_id = preferred_day_row.get("scan_id") or preferred_day_row.get("scanId")
        if scan_id:
            state = service.get_scan(str(scan_id))
            if state is not None and state.status != "failed":
                return {"scan_id": state.scan_id, "status": state.status}
    return None


def _find_reusable_scan_memory(
    service: TwResearchScanService,
    *,
    user_id: str,
    preferred_day: str,
    min_trading_day: str,
) -> dict[str, Any] | None:
    try:
        scans = service.list_scans(user_id, limit=50)
    except TwResearchDurableStoreError:
        return None
    incomplete: list[tuple[str, dict[str, Any]]] = []
    for item in scans:
        trading = str(item.get("tradingDay") or item.get("trading_day") or "")
        mode = item.get("selectorMode") or (item.get("selector_json") or {}).get("mode")
        if mode != "starter_preset":
            continue
        status = str(item.get("status") or "")
        if status == "failed":
            continue
        if trading < min_trading_day or trading > preferred_day:
            continue
        if status not in INCOMPLETE_SCHEDULE_STATUSES:
            continue
        incomplete.append(
            (
                trading,
                {
                    "scan_id": item.get("scanId") or item.get("scan_id"),
                    "status": status,
                },
            )
        )
    if not incomplete:
        return None
    incomplete.sort(key=lambda pair: pair[0])
    return incomplete[0][1]
