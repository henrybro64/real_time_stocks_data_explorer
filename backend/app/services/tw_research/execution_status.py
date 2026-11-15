"""Honest First-Pass execution readiness for managed research starts (#359).

A job row insert alone is never "collection started". This helper classifies
whether drain is authorized, whether a worker can claim work, and what the UI
should show next.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.tw_research.research_run import (
    ResearchRun,
    first_pass_drain_availability,
)


def classify_first_pass_execution(
    run: ResearchRun,
    *,
    job: dict[str, Any] | None = None,
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
        if isinstance(lease_expires, str) and lease_expires:
            try:
                lease_expires_at = lease_expires
                parsed = datetime.fromisoformat(lease_expires.replace("Z", "+00:00"))
                lease_active = parsed > datetime.now(UTC)
            except ValueError:
                lease_active = False
        universe_size = job.get("universe_size") or job.get("universeSize")
        cursor = job.get("cursor")
        if universe_size is not None and cursor is not None:
            try:
                universe_size = int(universe_size)
                processed = int(cursor)
                pending = max(0, universe_size - processed)
            except (TypeError, ValueError):
                processed = None
                pending = None

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
        "blocker": blocker,
        "nextAction": next_action,
        "jobId": run.first_pass_job_id,
        "jobStatus": job_status or None,
        "leaseActive": lease_active,
        "leaseExpiresAt": lease_expires_at,
        "universeSize": universe_size,
        "processedCount": processed,
        "pendingCount": pending,
        "notes": [
            "HTTP 200 / job insert is not proof that collection is running.",
            "schedule_enabled alone is not proof an active worker holds a lease.",
        ],
    }
