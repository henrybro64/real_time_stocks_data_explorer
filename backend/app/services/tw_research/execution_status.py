"""Honest First-Pass / Phase-2 execution readiness for managed research (#359/#361).

A job or technical-scan row insert alone is never "work started". These helpers
classify whether drain is authorized, whether a worker can claim work, and what
the UI should show next.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.tw_research.research_run import (
    ResearchRun,
    first_pass_drain_availability,
)

_DRAIN_STALE_AFTER = timedelta(minutes=15)
_PHASE2_TERMINAL_OK = frozenset({"completed_processing", "verified_ready"})
_PHASE2_ACTIVE = frozenset({"queued", "running", "partial"})


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_first_pass_execution(
    run: ResearchRun,
    *,
    job: dict[str, Any] | None = None,
    symbol_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an additive execution block for Start / current-run responses."""
    available, drain_reason = first_pass_drain_availability(run.user_id)
    job_status = ""
    lease_active = False
    lease_expires_at = None
    processed = None
    pending = None
    universe_size = None
    if job is not None:
        job_status = str(job.get("status") or job.get("job_status") or "")
        lease_expires = job.get("reserve_expires_at") or job.get("reserveExpiresAt")
        parsed_lease = _parse_iso(lease_expires) if lease_expires else None
        if parsed_lease is not None:
            lease_expires_at = parsed_lease.isoformat()
            lease_active = parsed_lease > datetime.now(UTC)
        universe_size = job.get("universe_size") or job.get("universeSize")
        if universe_size is None:
            stock_ids = job.get("stock_ids") or job.get("stockIds") or []
            if isinstance(stock_ids, list):
                universe_size = len(stock_ids)
        counts = symbol_counts
        if counts is None:
            # Prefer explicit aggregates over cursor (cursor is a watermark).
            counts = {
                "ok": job.get("okCount") or job.get("ok_count"),
                "partial": job.get("partialCount") or job.get("partial_count"),
                "failed": job.get("failedCount") or job.get("failed_count"),
                "total": job.get("symbolTotal") or job.get("symbol_total"),
            }
        try:
            universe_size_i = int(universe_size) if universe_size is not None else None
        except (TypeError, ValueError):
            universe_size_i = None
        universe_size = universe_size_i
        if universe_size_i is not None:
            try:
                ok = int(counts.get("ok") or 0) if isinstance(counts, dict) else 0
                partial = int(counts.get("partial") or 0) if isinstance(counts, dict) else 0
                failed = int(counts.get("failed") or 0) if isinstance(counts, dict) else 0
                processed = ok + partial + failed
                pending = max(0, universe_size_i - processed)
            except (TypeError, ValueError):
                processed = None
                pending = None

    drain_meta = {}
    if isinstance(run.dimensions, dict):
        raw = run.dimensions.get("firstPassDrain")
        if isinstance(raw, dict):
            drain_meta = raw
    last_tick_at = drain_meta.get("lastTickAt")
    last_tick = _parse_iso(last_tick_at)
    drain_fresh = (
        last_tick is not None
        and datetime.now(UTC) - last_tick <= _DRAIN_STALE_AFTER
    )

    run_status = str(run.status or "")
    phase = str(run.phase or "")
    schedule_enabled = bool(run.schedule_enabled)
    authorized = bool(schedule_enabled and available and run.first_pass_job_id)

    if phase == "awaiting_analyst_candidate_selection" or run_status == "gated":
        state = "awaiting_analyst_candidate_selection"
    elif run_status == "cancelled" or job_status == "cancelled":
        state = "cancelled"
    elif run_status in {"failed", "budget_exhausted"} or job_status in {
        "failed",
        "budget_exhausted",
    }:
        state = "failed"
    elif job_status in {"completed", "partial"}:
        state = "completed" if job_status == "completed" else "partially_complete"
    elif lease_active and job_status in {"running", "pending", "queued"}:
        state = "actively_collecting"
    elif job_status == "deferred" or run_status == "waiting_for_next_window":
        state = "waiting_for_retry"
    elif run.first_pass_job_id and not available:
        state = "executor_unavailable"
    elif (
        run.first_pass_job_id
        and schedule_enabled
        and available
        and not drain_fresh
    ):
        state = "executor_not_ticking"
    elif run.first_pass_job_id and schedule_enabled and available:
        state = "execution_authorized"
    elif run.first_pass_job_id:
        state = "job_created"
    else:
        state = "queued"

    blocker = None
    next_action = None
    if state == "executor_unavailable":
        blocker = drain_reason or "first_pass_drain_unavailable"
        next_action = "wait_for_executor_or_run_manual_batch"
    elif state == "executor_not_ticking":
        blocker = "first_pass_drain_not_ticking"
        next_action = "wait_or_contact_operator"
    elif state == "execution_authorized":
        next_action = "waiting_for_drain_tick"
    elif state == "job_created" and not schedule_enabled:
        blocker = drain_reason or "schedule_not_authorized"
        next_action = "enable_drain_or_run_manual_batch"
    elif state == "actively_collecting":
        next_action = "poll_progress"
    elif state == "waiting_for_retry":
        next_action = "wait_for_retry_window"
    elif state == "awaiting_analyst_candidate_selection":
        next_action = "select_candidates"
    elif state in {"completed", "partially_complete"}:
        next_action = "review_evidence"
    elif state == "failed":
        next_action = "inspect_errors_or_retry"
    else:
        next_action = "start_or_wait"

    return {
        "state": state,
        "authorized": authorized,
        "scheduleEnabled": schedule_enabled,
        "drainAvailable": available,
        "drainBlocker": drain_reason,
        "drainLastTickAt": last_tick_at if isinstance(last_tick_at, str) else None,
        "drainFresh": bool(drain_fresh) if schedule_enabled and available else None,
        "blocker": blocker,
        "nextAction": next_action,
        "jobId": run.first_pass_job_id,
        "jobStatus": job_status or None,
        "leaseActive": lease_active,
        "leaseExpiresAt": lease_expires_at,
        "universeSize": universe_size,
        "processedCount": processed,
        "pendingCount": pending,
        # English operator notes stay out of the default UI (#359 P3).
        "notes": [
            "HTTP 200 / job insert is not proof that collection is running.",
            "schedule_enabled alone is not proof an active worker holds a lease.",
        ],
    }


def classify_phase2_execution(
    run: ResearchRun,
    *,
    scan: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Honest Phase-2 technical execution block for managed research runs (#361).

    Returns ``None`` when the run has no technical scan and candidates are not
    approved — Stage 4 has nothing to classify yet.
    """
    approved = str((run.gates or {}).get("candidateSelection") or "") == "approved"
    scan_id = str(run.technical_scan_id or "") or None
    if not approved and not scan_id:
        return None

    available, drain_reason = first_pass_drain_availability(run.user_id)
    if approved and not scan_id:
        return {
            "state": "awaiting_technical_start",
            "authorized": False,
            "drainAvailable": available,
            "drainBlocker": drain_reason,
            "drainLastTickAt": None,
            "drainFresh": None,
            "blocker": None,
            "nextAction": "start_technical_scan",
            "technicalScanId": None,
            "scanStatus": None,
            "leaseActive": False,
            "leaseExpiresAt": None,
            "universeSize": None,
            "processedCount": None,
            "remainingCount": None,
            "candidateSelection": (run.gates or {}).get("candidateSelection"),
            "notes": [
                "Candidate approval alone does not start technical analysis.",
                "Press Start technical analysis to attach a run-owned scan.",
            ],
        }
    drain_meta = {}
    if isinstance(run.dimensions, dict):
        raw = run.dimensions.get("firstPassDrain")
        if isinstance(raw, dict):
            drain_meta = raw
    last_tick_at = drain_meta.get("lastTickAt")
    last_tick = _parse_iso(last_tick_at)
    drain_fresh = (
        last_tick is not None
        and datetime.now(UTC) - last_tick <= _DRAIN_STALE_AFTER
    )

    scan_status = ""
    lease_active = False
    lease_expires_at = None
    remaining = None
    universe_size = None
    processed = None
    if scan is not None:
        scan_status = str(scan.get("status") or "")
        lease_expires = scan.get("reserve_expires_at") or scan.get("reserveExpiresAt")
        parsed_lease = _parse_iso(lease_expires) if lease_expires else None
        if parsed_lease is not None:
            lease_expires_at = parsed_lease.isoformat()
            lease_active = parsed_lease > datetime.now(UTC)
        try:
            universe_size = int(
                scan.get("universeSize")
                if scan.get("universeSize") is not None
                else scan.get("universe_size")
                if scan.get("universe_size") is not None
                else len(scan.get("stockIds") or scan.get("stock_ids") or [])
            )
        except (TypeError, ValueError):
            universe_size = None
        try:
            if scan.get("remainingCount") is not None:
                remaining = int(scan.get("remainingCount"))
            elif scan.get("remaining_count") is not None:
                remaining = int(scan.get("remaining_count"))
            elif universe_size is not None and scan.get("cursor") is not None:
                remaining = max(0, universe_size - int(scan.get("cursor") or 0))
        except (TypeError, ValueError):
            remaining = None
        try:
            if scan.get("processedCount") is not None:
                processed = int(scan.get("processedCount"))
            elif scan.get("processed_count") is not None:
                processed = int(scan.get("processed_count"))
            elif universe_size is not None and remaining is not None:
                processed = max(0, universe_size - remaining)
        except (TypeError, ValueError):
            processed = None

    run_status = str(run.status or "")

    if run_status == "cancelled" or scan_status == "cancelled":
        state = "failed"
        blocker = "cancelled"
        next_action = "inspect_errors_or_retry"
    elif run_status == "failed" or scan_status == "failed":
        state = "failed"
        blocker = "phase2_failed"
        next_action = "inspect_errors_or_retry"
    elif scan_status == "budget_exhausted" or run_status == "budget_exhausted":
        state = "waiting_for_budget_window"
        blocker = "budget_exhausted"
        next_action = "resume_budget_or_wait"
    elif scan_status in _PHASE2_TERMINAL_OK or str(run.phase or "") in {
        "awaiting_analyst_report_decision",
        "complete_evidence_ready",
    }:
        state = "completed"
        blocker = None
        next_action = "review_evidence"
    elif not available:
        state = "executor_unavailable"
        blocker = drain_reason or "first_pass_drain_unavailable"
        next_action = "wait_for_executor"
    elif lease_active and scan_status in _PHASE2_ACTIVE:
        state = "actively_analyzing"
        blocker = None
        next_action = "poll_progress"
    elif drain_fresh and (
        (processed is not None and int(processed) > 0) or scan_status == "running"
    ):
        state = "actively_analyzing"
        blocker = None
        next_action = "poll_progress"
    elif available and last_tick is None:
        # Attached scan, drain can run, but no tick yet — do not imply analysis ran.
        state = "technical_scan_created"
        blocker = None
        next_action = "waiting_for_drain_tick"
    elif available and not drain_fresh:
        state = "executor_not_ticking"
        blocker = "first_pass_drain_not_ticking"
        next_action = "wait_or_contact_operator"
    else:
        state = "technical_scan_created"
        blocker = None
        next_action = "waiting_for_drain_tick"

    return {
        "state": state,
        "authorized": bool(approved and scan_id and available),
        "drainAvailable": available,
        "drainBlocker": drain_reason,
        "drainLastTickAt": last_tick_at if isinstance(last_tick_at, str) else None,
        "drainFresh": bool(drain_fresh) if available else None,
        "blocker": blocker,
        "nextAction": next_action,
        "technicalScanId": scan_id,
        "scanStatus": scan_status or None,
        "leaseActive": lease_active,
        "leaseExpiresAt": lease_expires_at,
        "universeSize": universe_size,
        "processedCount": processed,
        "remainingCount": remaining,
        "candidateSelection": (run.gates or {}).get("candidateSelection"),
        "notes": [
            "Attaching a technical scan is not proof that analysis is running.",
            "Phase-2 drain reuses the First-Pass drain CronJob token/owner/flag.",
        ],
    }
