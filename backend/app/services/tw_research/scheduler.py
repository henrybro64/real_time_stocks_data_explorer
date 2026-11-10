"""Disabled-by-default post-market Taiwan research orchestration (#330).

Creates or reuses an idempotent scan for the schedule owner, runs a bounded
number of batches (and optional budget resume), and never pretends incomplete
work is a verified success. The Kubernetes CronJob stays suspended until an
operator enables it after pilot measurement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.services.market_clock import is_trading_day, last_completed_session_date
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
    "reused_scan",
    "created_scan",
    "completed_window",
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
    batches_run: int
    resumed: bool
    notes: tuple[str, ...]
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "tradingDay": self.trading_day,
            "scanId": self.scan_id,
            "status": self.status,
            "batchesRun": self.batches_run,
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
    max_batches: int = 5,
    target_count: int = 10,
    trading_day: str | None = None,
    resume_if_exhausted: bool = True,
    now: datetime | None = None,
    service: TwResearchScanService | None = None,
    allow_holiday_fetch: bool = False,
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
            batches_run=0,
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
            batches_run=0,
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
            batches_run=0,
            resumed=False,
            notes=("dry_run_plan", f"max_batches={max_batches}", f"target_count={target_count}"),
            dry_run=True,
        )

    scan_service = service or tw_research_scan_service
    selector = UniverseSelector(mode="starter_preset")
    existing = _find_reusable_scan(scan_service, user_id=user_id, trading_day=day)
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
        state = scan_service.get_scan(existing["scanId"])  # type: ignore[index]
        if state is None:
            raise TwResearchDurableStoreError(
                "Scheduled scan disappeared after list",
                scan_id=str(existing["scanId"]),
                operation="schedule_run",
            )
        notes.append("reused_scan")

    if state.status == "budget_exhausted" and resume_if_exhausted:
        state = scan_service.resume_budget_window(state.scan_id)
        resumed = True
        notes.append("budget_window_resumed")

    batches_run = 0
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
            batches_run=batches_run,
            resumed=resumed,
            notes=tuple(notes),
            dry_run=False,
        )

    if state.status in {"completed_processing", "verified_ready"}:
        outcome: ScheduleOutcome = "completed_window"
    elif state.status == "budget_exhausted":
        outcome = "budget_exhausted"
    elif created and batches_run == 0:
        outcome = "created_scan"
    elif existing is not None and batches_run == 0 and not resumed:
        outcome = "reused_scan"
    else:
        outcome = "partial"

    notes.append(f"schedule_tick_batches={batches_run}")
    return ScheduleRunResult(
        outcome=outcome,
        trading_day=day,
        scan_id=state.scan_id,
        status=state.status,
        batches_run=batches_run,
        resumed=resumed,
        notes=tuple(notes),
        dry_run=False,
    )


def _find_reusable_scan(
    service: TwResearchScanService,
    *,
    user_id: str,
    trading_day: str,
) -> dict[str, Any] | None:
    """Prefer a same-day starter-preset scan for the schedule owner."""
    try:
        scans = service.list_scans(user_id, limit=20)
    except TwResearchDurableStoreError:
        # Memory-only / unavailable durable store: no reuse across processes.
        return None
    for item in scans:
        if item.get("tradingDay") != trading_day:
            continue
        if item.get("selectorMode") != "starter_preset":
            continue
        if item.get("status") == "failed":
            continue
        return item
    return None
