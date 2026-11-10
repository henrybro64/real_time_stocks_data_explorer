"""Disabled-by-default post-market Taiwan research orchestration (#330).

Creates or reuses an idempotent scan for the schedule owner, runs a bounded
number of batches (and optional budget resume + enrichment), and never pretends
incomplete work is a verified success. The Kubernetes CronJob stays suspended
until an operator enables it after pilot measurement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.market_clock import is_trading_day, last_completed_session_date
from app.services.tw_research.constants import (
    CALCULATION_VERSION,
    DEFAULT_SCHEDULE_MAX_BATCHES,
    DEFAULT_SCHEDULE_MAX_ENRICH_BATCHES,
    UNIVERSE_PRESET_VERSION,
)
from app.services.tw_research.scan import (
    TwResearchAdmissionError,
    TwResearchConflictError,
    TwResearchDurableStoreError,
    TwResearchScanService,
    tw_research_scan_service,
)
from app.services.tw_research.universe import UniverseSelector

TAIPEI = ZoneInfo("Asia/Taipei")
logger = logging.getLogger(__name__)

ScheduleOutcome = Literal[
    "dry_run",
    "skipped_nontrading_day",
    "skipped_data_not_ready",
    "skipped_disabled",
    "deferred_lock",
    "reused_scan",
    "created_scan",
    "completed_window",
    "technically_complete",
    "enrichment_partial",
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
) -> ScheduleRunResult:
    """Idempotent bounded schedule tick for the starter-preset universe.

    One logical research run per (owner, trading day, universe, calculation,
    target). Subsequent CronJob windows continue the same scan until technical
    screening, enrichment, and verified publication complete — never inventing
    a second expensive scan for compatible configuration.
    """
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

    if skip is not None:
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
    tick_token = uuid4().hex
    lock_name = (
        f"schedule:{user_id}:{day}:starter_preset:{CALCULATION_VERSION}:t{target_count}"
    )
    lock_held = False
    if getattr(scan_service, "_persist", True):
        try:
            from app.repositories.tw_research_repository import tw_research_repository

            if tw_research_repository.is_available():
                claimed = tw_research_repository.claim_schedule_tick_lock(
                    lock_name=lock_name,
                    owner_token=tick_token,
                    lease_seconds=max(60, int(max_batches) * 180),
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
            day=day,
            max_batches=max_batches,
            max_enrich_batches=max_enrich_batches,
            target_count=target_count,
            resume_if_exhausted=resume_if_exhausted,
            now=now,
            notes=notes,
        )
    finally:
        if lock_held:
            try:
                from app.repositories.tw_research_repository import tw_research_repository

                tw_research_repository.release_schedule_tick_lock(
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
    day: str,
    max_batches: int,
    max_enrich_batches: int,
    target_count: int,
    resume_if_exhausted: bool,
    now: datetime | None,
    notes: list[str],
) -> ScheduleRunResult:
    selector = UniverseSelector(mode="starter_preset")
    # Starter-preset universe version is stable; avoid catalog resolve on the tick path.
    universe_version = UNIVERSE_PRESET_VERSION

    existing = _find_reusable_scan(
        scan_service,
        user_id=user_id,
        trading_day=day,
        universe_version=universe_version,
        calculation_version=CALCULATION_VERSION,
        target_count=target_count,
    )
    resumed = False
    created = False
    if existing is None:
        state = scan_service.create_scan(
            selector,
            target_count=target_count,  # type: ignore[arg-type]
            trading_day=day,
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
        notes.append("reused_scan")

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

    if state.status == "budget_exhausted" and resume_if_exhausted:
        try:
            state = scan_service.resume_budget_window(state.scan_id)
            resumed = True
            notes.append("budget_window_resumed")
        except TwResearchConflictError as exc:
            notes.append(f"resume_conflict:{exc.code}")

    batches_run = 0
    enrich_batches_run = 0
    try:
        while batches_run < max_batches and state.status in {
            "queued",
            "running",
            "partial",
        }:
            before = (
                state.cursor,
                state.upstream_month_fetches,
                state.status,
                len(state.records),
            )
            state = scan_service.run_batch(state.scan_id, now=now)
            batches_run += 1
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

        # Eligible enrichment only after technical screening has work to enrich.
        while (
            enrich_batches_run < max_enrich_batches
            and state.status
            in {"completed_processing", "partial", "verified_ready", "running"}
            and state.qualified_ids
        ):
            pending = [
                stock_id
                for stock_id in state.qualified_ids
                if stock_id in state.records
                and state.records[stock_id].enrichment is None
            ]
            if not pending:
                notes.append("enrichment_caught_up")
                break
            before_enrich = (
                state.enrichments_run,
                state.enrich_cursor,
                state.active_snapshot_id,
            )
            state = scan_service.run_enrichment_batch(state.scan_id, now=now)
            enrich_batches_run += 1
            after_enrich = (
                state.enrichments_run,
                state.enrich_cursor,
                state.active_snapshot_id,
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

    outcome = _classify_outcome(
        state=state,
        created=created,
        existing=existing,
        batches_run=batches_run,
        enrich_batches_run=enrich_batches_run,
        resumed=resumed,
        notes=notes,
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


def _classify_outcome(
    *,
    state: Any,
    created: bool,
    existing: dict[str, Any] | None,
    batches_run: int,
    enrich_batches_run: int,
    resumed: bool,
    notes: list[str],
) -> ScheduleOutcome:
    if state.status == "verified_ready" and state.active_snapshot_id:
        return "verified_ready"
    if state.status == "completed_processing":
        pending = [
            stock_id
            for stock_id in state.qualified_ids
            if stock_id in state.records
            and state.records[stock_id].enrichment is None
        ]
        if pending:
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
    del notes  # notes already capture deferral reasons
    return "partial"


def _find_reusable_scan(
    service: TwResearchScanService,
    *,
    user_id: str,
    trading_day: str,
    universe_version: str,
    calculation_version: str,
    target_count: int,
) -> dict[str, Any] | None:
    """Prefer a same-day compatible starter-preset scan for the schedule owner."""
    if getattr(service, "_persist", True):
        try:
            from app.repositories.tw_research_repository import tw_research_repository

            if tw_research_repository.is_available():
                found = tw_research_repository.find_compatible_scan(
                    user_id=user_id,
                    trading_day=trading_day,
                    universe_version=universe_version,
                    calculation_version=calculation_version,
                    selector_mode="starter_preset",
                    target_count=target_count,
                )
                if found is not None:
                    return found
        except Exception:  # noqa: BLE001
            logger.warning("tw_research find_compatible_scan failed", exc_info=True)

    try:
        scans = service.list_scans(user_id, limit=20)
    except TwResearchDurableStoreError:
        return None
    for item in scans:
        trading = item.get("tradingDay") or item.get("trading_day")
        if trading != trading_day:
            continue
        mode = item.get("selectorMode") or (item.get("selector_json") or {}).get("mode")
        if mode != "starter_preset":
            continue
        if item.get("status") == "failed":
            continue
        # Normalize to repository-shaped keys for callers.
        return {
            "scan_id": item.get("scanId") or item.get("scan_id"),
            "status": item.get("status"),
        }
    return None
