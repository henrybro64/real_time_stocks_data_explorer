"""Honest First-Pass / Phase-2 execution readiness for managed research (#359/#361).

A job or technical-scan row insert alone is never "work started". These helpers
classify whether drain is authorized, whether a worker can claim work, and what
the UI should show next.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.tw_research.constants import HISTORY_BUDGET_EXHAUSTED_REASON
from app.services.tw_research.research_run import (
    ResearchRun,
    first_pass_drain_availability,
)
from app.services.tw_research.scheduler import schedule_has_technical_work

_DRAIN_STALE_AFTER = timedelta(minutes=15)
_PHASE2_TERMINAL_OK = frozenset({"completed_processing", "verified_ready"})
_PHASE2_ACTIVE = frozenset({"queued", "running", "partial"})


def _phase2_now() -> datetime:
    """Injectable UTC clock for lease / drain freshness (tests)."""
    return datetime.now(UTC)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _drain_outcome_is_noop(last_outcome: object) -> bool | None:
    """True when the last drain stop did not advance symbol collection.

    Matches First-Pass drain stop reasons: ``idle_no_work``, ``deferred``, and
    any ``skipped_*`` preflight/skip outcome. Batch/progress stops
    (``max_batches_reached``, ``job_completed``, ``failed_batch``, …) are False.
    """
    if not isinstance(last_outcome, str) or not last_outcome.strip():
        return None
    outcome = last_outcome.strip().lower()
    if outcome in {"idle_no_work", "deferred"} or outcome.startswith("skipped_"):
        return True
    return False


def classify_first_pass_execution(
    run: ResearchRun,
    *,
    job: dict[str, Any] | None = None,
    symbol_counts: dict[str, Any] | None = None,
    symbol_counts_available: bool = True,
    last_data_progress_at: str | None = None,
) -> dict[str, Any]:
    """Return an additive execution block for Start / current-run responses.

    ``symbol_counts_available=False`` means the authoritative symbol-count read
    failed: leave processed/pending null rather than inventing 0/N from missing
    job aggregates.
    """
    available, drain_reason = first_pass_drain_availability(run.user_id)
    job_status = ""
    lease_active = False
    lease_expires_at = None
    processed = None
    pending = None
    universe_size = None
    job_updated_at = None
    if job is not None:
        job_status = str(job.get("status") or job.get("job_status") or "")
        lease_expires = job.get("reserve_expires_at") or job.get("reserveExpiresAt")
        parsed_lease = _parse_iso(lease_expires) if lease_expires else None
        if parsed_lease is not None:
            lease_expires_at = parsed_lease.isoformat()
            lease_active = parsed_lease > datetime.now(UTC)
        raw_job_updated = job.get("updated_at") or job.get("updatedAt")
        if isinstance(raw_job_updated, str):
            job_updated_at = raw_job_updated
        elif raw_job_updated is not None:
            parsed_updated = _parse_iso(raw_job_updated)
            if parsed_updated is not None:
                job_updated_at = parsed_updated.isoformat()
        universe_size = job.get("universe_size") or job.get("universeSize")
        if universe_size is None:
            stock_ids = job.get("stock_ids") or job.get("stockIds") or []
            if isinstance(stock_ids, list):
                universe_size = len(stock_ids)
        try:
            universe_size_i = int(universe_size) if universe_size is not None else None
        except (TypeError, ValueError):
            universe_size_i = None
        universe_size = universe_size_i
        if symbol_counts_available:
            counts = symbol_counts
            if counts is None:
                # Prefer explicit aggregates over cursor (cursor is a watermark).
                counts = {
                    "ok": job.get("okCount") or job.get("ok_count"),
                    "partial": job.get("partialCount") or job.get("partial_count"),
                    "failed": job.get("failedCount") or job.get("failed_count"),
                    "total": job.get("symbolTotal") or job.get("symbol_total"),
                }
            if universe_size_i is not None:
                try:
                    ok = int(counts.get("ok") or 0) if isinstance(counts, dict) else 0
                    partial = (
                        int(counts.get("partial") or 0) if isinstance(counts, dict) else 0
                    )
                    failed = (
                        int(counts.get("failed") or 0) if isinstance(counts, dict) else 0
                    )
                    processed = ok + partial + failed
                    pending = max(0, universe_size_i - processed)
                except (TypeError, ValueError):
                    processed = None
                    pending = None
        # else: count read failed — keep processed/pending null (no fabricated 0/N).

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
    last_outcome = drain_meta.get("lastOutcome")
    # Real drain stop reasons: idle/deferred/skipped_* are not collection progress.
    noop_tick = _drain_outcome_is_noop(last_outcome)

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
        # Only a live batch lease proves active collection — not run.status=running.
        state = "actively_collecting"
    elif job_status == "deferred" or run_status == "waiting_for_next_window":
        state = "waiting_for_retry"
    elif run.first_pass_job_id and not available:
        state = "executor_unavailable"
    elif (
        run.first_pass_job_id
        and schedule_enabled
        and available
        and not lease_active
        and not drain_fresh
        and isinstance(processed, int)
        and processed > 0
        and isinstance(pending, int)
        and pending > 0
    ):
        # Mid-job, armed, but no recent tick and no lease — stalled pending operator check.
        state = "stalled"
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
    elif state == "stalled":
        blocker = "first_pass_drain_stalled"
        next_action = "wait_or_contact_operator"
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
        "drainLastOutcome": last_outcome if isinstance(last_outcome, str) else None,
        "drainNoopTick": bool(noop_tick) if last_outcome is not None else None,
        # jobUpdatedAt = any Job row mutation (lease renew, admin, status).
        "jobUpdatedAt": job_updated_at,
        # lastDataProgressAt = MAX(symbol.updated_at) for ok/partial/failed only.
        "lastDataProgressAt": last_data_progress_at
        if isinstance(last_data_progress_at, str) and last_data_progress_at.strip()
        else None,
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
            "A fresh drain tick without a live lease is not active collecting.",
            "Backend cannot observe CronJob suspend; verify operator preflight.",
        ],
    }


def classify_phase2_execution(
    run: ResearchRun,
    *,
    scan: dict[str, Any] | None = None,
    scan_state: Any | None = None,
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
    phase2_meta = {}
    if isinstance(run.dimensions, dict):
        raw = run.dimensions.get("phase2Drain")
        if isinstance(raw, dict):
            phase2_meta = raw
    phase2_enabled = True if "enabled" not in phase2_meta else bool(phase2_meta.get("enabled"))

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
            "analyzedCount": None,
            "insufficientHistoryCount": None,
            "failedCount": None,
            "pendingCount": None,
            "candidateSelection": (run.gates or {}).get("candidateSelection"),
            "phase2Enabled": phase2_enabled,
            "notes": [
                "Candidate approval alone does not start technical analysis.",
                "Press Start technical analysis to attach a run-owned scan.",
            ],
        }

    # Do not early-return on phase2Enabled=False: pausing future scheduling must
    # not hide scan progress or claim an in-flight lease has stopped (#361/#362).

    drain_meta = {}
    if isinstance(run.dimensions, dict):
        raw = run.dimensions.get("firstPassDrain")
        if isinstance(raw, dict):
            drain_meta = raw
    last_tick_at = phase2_meta.get("lastTickAt") or drain_meta.get("lastTickAt")
    last_tick = _parse_iso(last_tick_at)
    now = _phase2_now()
    drain_fresh = (
        last_tick is not None and now - last_tick <= _DRAIN_STALE_AFTER
    )
    last_outcome = str(phase2_meta.get("lastOutcome") or "")

    scan_status = ""
    lease_active = False
    lease_expires_at = None
    remaining = None
    universe_size = None
    processed = None
    analyzed = None
    insufficient = None
    failed_count = None
    pending_count = None
    has_technical_work = None
    if scan is not None:
        scan_status = str(scan.get("status") or "")
        lease_expires = scan.get("reserve_expires_at") or scan.get("reserveExpiresAt")
        parsed_lease = _parse_iso(lease_expires) if lease_expires else None
        if parsed_lease is not None:
            lease_expires_at = parsed_lease.isoformat()
            lease_active = parsed_lease > now
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

    if scan_state is not None:
        try:
            has_technical_work = bool(schedule_has_technical_work(scan_state))
        except Exception:  # noqa: BLE001
            has_technical_work = None
        stock_ids = getattr(scan_state, "stock_ids", ()) or ()
        records = getattr(scan_state, "records", {}) or {}
        analyzed = 0
        insufficient = 0
        failed_count = 0
        pending_count = 0
        for sid in stock_ids:
            rec = records.get(sid)
            if rec is None:
                pending_count += 1
                continue
            reason = str(getattr(rec, "rejection_reason", "") or "")
            readiness = str(getattr(rec, "readiness", "") or "")
            # Mutually exclusive buckets — analyzed = evaluated indicators only.
            if reason == HISTORY_BUDGET_EXHAUSTED_REASON or readiness == "source_incomplete":
                insufficient += 1
            elif readiness == "rejected" or reason:
                failed_count += 1
            else:
                analyzed += 1
        if not scan_status:
            scan_status = str(getattr(scan_state, "status", "") or "")

    coverage_meta = phase2_meta.get("coverage") if isinstance(phase2_meta.get("coverage"), dict) else {}
    if analyzed is None and coverage_meta:
        try:
            analyzed = int(coverage_meta.get("analyzed")) if coverage_meta.get("analyzed") is not None else None
            insufficient = (
                int(coverage_meta.get("insufficientHistory"))
                if coverage_meta.get("insufficientHistory") is not None
                else None
            )
            if coverage_meta.get("failed") is not None:
                failed_count = int(coverage_meta.get("failed"))
            if coverage_meta.get("pending") is not None:
                pending_count = int(coverage_meta.get("pending"))
        except (TypeError, ValueError):
            pass

    run_status = str(run.status or "")
    phase = str(run.phase or "")

    terminal_status = scan_status in _PHASE2_TERMINAL_OK
    complete = False
    if terminal_status and has_technical_work is False:
        complete = True
    elif terminal_status and has_technical_work is None and phase in {
        "awaiting_analyst_report_decision",
        "complete_evidence_ready",
    }:
        complete = True
    elif phase in {"awaiting_analyst_report_decision", "complete_evidence_ready"} and (
        has_technical_work is False or has_technical_work is None
    ):
        complete = True

    if run_status == "cancelled" or scan_status == "cancelled":
        state = "failed"
        blocker = "cancelled"
        next_action = "inspect_errors_or_retry"
    elif run_status == "failed" or scan_status == "failed":
        state = "failed"
        blocker = "phase2_failed"
        next_action = "inspect_errors_or_retry"
    elif complete:
        # Authoritative completion wins over a stale budget lastOutcome (#361/#362).
        state = "completed"
        blocker = None
        next_action = "review_evidence"
    elif last_outcome == "phase2_budget_cap_reached" or (
        scan_status == "budget_exhausted" and has_technical_work is not False
    ):
        state = "waiting_for_budget_window"
        blocker = (
            "phase2_budget_cap_reached"
            if last_outcome == "phase2_budget_cap_reached"
            else "budget_exhausted"
        )
        next_action = (
            "wait_for_next_trading_day"
            if last_outcome == "phase2_budget_cap_reached"
            else "resume_budget_or_wait"
        )
    elif last_outcome == "phase2_no_progress":
        no_progress_failed = 0
        try:
            no_progress_failed = int((coverage_meta or {}).get("failed") or 0)
        except (TypeError, ValueError):
            no_progress_failed = 0
        if failed_count is not None:
            no_progress_failed = max(no_progress_failed, int(failed_count))
        if no_progress_failed > 0 and (remaining is None or remaining <= 0):
            state = "failed"
            blocker = "phase2_no_progress"
            next_action = "inspect_errors_or_retry"
        else:
            state = "waiting_for_retry"
            blocker = "phase2_no_progress"
            next_action = "wait_for_retry_window"
    elif not available:
        state = "executor_unavailable"
        blocker = drain_reason or "first_pass_drain_unavailable"
        next_action = "wait_for_executor"
    elif lease_active and scan_status in _PHASE2_ACTIVE:
        # Only a live worker lease proves analysis is currently executing.
        state = "actively_analyzing"
        blocker = None
        next_action = "poll_progress"
    elif (
        available
        and drain_fresh
        and scan_status in _PHASE2_ACTIVE
        and (
            (remaining is not None and remaining > 0)
            or has_technical_work is True
            or (processed is not None and int(processed) > 0)
        )
    ):
        # Recent drain tick without a live lease → between batches, not active.
        state = "between_batches"
        blocker = None
        next_action = "waiting_for_next_batch"
    elif available and last_tick is None:
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

    # Scheduling pause is orthogonal to active processing / terminal outcomes.
    if scan_id and not phase2_enabled:
        if state == "actively_analyzing":
            # Lease still held — pause only blocks future drain claims.
            blocker = "phase2_scheduling_paused"
            next_action = "poll_progress"
        elif state not in {"completed", "failed", "waiting_for_budget_window"}:
            state = "paused"
            blocker = "phase2_paused"
            next_action = "resume_phase2_drain"

    return {
        "state": state,
        "authorized": bool(approved and scan_id and available and phase2_enabled),
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
        # analyzedCount = evaluated indicator rows only (excludes failed/insufficient/pending).
        "analyzedCount": analyzed,
        "insufficientHistoryCount": insufficient,
        "failedCount": failed_count,
        "pendingCount": pending_count,
        "hasTechnicalWork": has_technical_work,
        "candidateSelection": (run.gates or {}).get("candidateSelection"),
        "phase2Enabled": phase2_enabled,
        "notes": [
            "Attaching a technical scan is not proof that analysis is running.",
            "Phase-2 drain reuses the First-Pass drain CronJob token/owner/flag.",
            "Terminal scan status alone is not complete while history work remains.",
            "phase2Enabled=false pauses future scheduling; an active lease may still finish.",
        ],
    }

