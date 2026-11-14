"""Durable opt-in First-Pass job drain for managed research runs (#349).

Drains only the authoritative ``research_run.first_pass_job_id`` when
``schedule_enabled`` is true. Reuses existing leased ``run_batch`` machinery.
Never advances analyst gates, never starts Phase-2, never writes Notion, and
never drains historical orphan jobs that are not run-owned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.services.tw_research.first_pass_job import FirstPassJobError, first_pass_job_service
from app.services.tw_research.research_run import (
    ResearchRunConflict,
    ResearchRunStoreUnavailable,
    research_run_store,
)


@dataclass
class FirstPassDrainResult:
    outcome: str
    user_id: str
    research_run_id: str | None = None
    job_id: str | None = None
    phase: str | None = None
    status: str | None = None
    schedule_enabled: bool = False
    batches_run: int = 0
    symbols_processed: int = 0
    waiting_for_retry: bool = False
    lease_held: bool = False
    already_complete: bool = False
    dry_run: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "userId": self.user_id,
            "researchRunId": self.research_run_id,
            "jobId": self.job_id,
            "phase": self.phase,
            "status": self.status,
            "scheduleEnabled": self.schedule_enabled,
            "batchesRun": self.batches_run,
            "symbolsProcessed": self.symbols_processed,
            "waitingForRetry": self.waiting_for_retry,
            "leaseHeld": self.lease_held,
            "alreadyComplete": self.already_complete,
            "dryRun": self.dry_run,
            "notes": list(self.notes),
            "stopsAtAnalystGate": True,
            "notionWrites": False,
            "phase2AutoStart": False,
        }


def drain_authorized_first_pass(
    *,
    user_id: str,
    enabled: bool,
    dry_run: bool = False,
    max_batches: int = 1,
    batch_size: int = 5,
    time_budget_seconds: float = 60,
    job_id: str | None = None,
) -> FirstPassDrainResult:
    """Run up to ``max_batches`` leased first-pass batches for one authorized run.

    Refuses work when:
    - global drain flag is false
    - research run missing / store unavailable
    - ``schedule_enabled`` is false (not explicitly authorized)
    - job is not the run's authoritative ``first_pass_job_id``
    - phase is past Phase-1 (already gated / Phase-2)
    """
    owner = str(user_id or "").strip()
    notes: list[str] = [
        "Drains only the research-run owned first-pass job.",
        "Never resumes orphan/historical pending jobs without schedule_enabled.",
        "Stops at awaiting_analyst_candidate_selection; no Notion / Phase-2.",
    ]
    if not owner:
        return FirstPassDrainResult(
            outcome="skipped_missing_user",
            user_id="",
            notes=notes + ["TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID is empty."],
            dry_run=dry_run,
        )
    if not enabled:
        return FirstPassDrainResult(
            outcome="skipped_disabled",
            user_id=owner,
            notes=notes + ["tw_research_first_pass_drain_enabled=false."],
            dry_run=dry_run,
        )

    try:
        run = research_run_store.get(owner)
    except ResearchRunStoreUnavailable as exc:
        return FirstPassDrainResult(
            outcome="failed_store_unavailable",
            user_id=owner,
            notes=notes + [str(exc)],
            dry_run=dry_run,
        )
    if run is None:
        return FirstPassDrainResult(
            outcome="skipped_no_research_run",
            user_id=owner,
            notes=notes + ["No durable research_run for owner."],
            dry_run=dry_run,
        )

    authoritative = str(run.first_pass_job_id or "") or None
    requested = str(job_id).strip() if job_id else None
    # Never accept a caller-supplied jobId unless it equals a non-null authoritative
    # job. Without an attached job, any requested id would bypass orphan protection.
    if requested and not authoritative:
        return FirstPassDrainResult(
            outcome="skipped_non_authoritative_job",
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=requested,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes
            + [
                f"Refused job {requested}; research run has no authoritative first_pass_job_id.",
                "Historical overlapping jobs are not auto-drained (#349).",
            ],
            dry_run=dry_run,
        )
    if requested and authoritative and requested != authoritative:
        return FirstPassDrainResult(
            outcome="skipped_non_authoritative_job",
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=requested,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes
            + [
                f"Refused job {requested}; authoritative job is {authoritative}.",
                "Historical overlapping jobs are not auto-drained (#349).",
            ],
            dry_run=dry_run,
        )

    target_job = authoritative
    if not target_job:
        return FirstPassDrainResult(
            outcome="skipped_no_job",
            user_id=owner,
            research_run_id=run.research_run_id,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes + ["Research run has no first_pass_job_id yet."],
            dry_run=dry_run,
        )

    if not run.schedule_enabled:
        return FirstPassDrainResult(
            outcome="skipped_not_authorized",
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=target_job,
            phase=run.phase,
            status=run.status,
            schedule_enabled=False,
            notes=notes
            + [
                "schedule_enabled=false — Start Research sets this; pause clears it.",
            ],
            dry_run=dry_run,
        )

    if run.phase != "phase1_first_pass":
        return FirstPassDrainResult(
            outcome="skipped_past_phase1_gate",
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=target_job,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes
            + [
                f"Phase is {run.phase}; drain stops at analyst candidate selection.",
            ],
            dry_run=dry_run,
        )

    # Do not keep draining failed / exhausted runs even if still in phase1.
    if str(run.status or "") in {"failed", "budget_exhausted", "cancelled"}:
        return FirstPassDrainResult(
            outcome="skipped_run_status",
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=target_job,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes + [f"Research run status is {run.status}; drain refused."],
            dry_run=dry_run,
        )

    if dry_run:
        return FirstPassDrainResult(
            outcome="dry_run_ready",
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=target_job,
            phase=run.phase,
            status=run.status,
            schedule_enabled=True,
            notes=notes + ["Dry run — no batches claimed."],
            dry_run=True,
        )

    batches_run = 0
    symbols_processed = 0
    waiting_for_retry = False
    lease_held = False
    already_complete = False
    stop_reason = "max_batches_reached"
    last_job: dict[str, Any] | None = None

    for _ in range(max(1, int(max_batches))):
        # Re-read authorization between batches so pause/status changes take effect
        # without waiting for the next CronJob tick.
        try:
            live = research_run_store.get(owner)
        except ResearchRunStoreUnavailable as exc:
            return FirstPassDrainResult(
                outcome="failed_store_unavailable",
                user_id=owner,
                research_run_id=run.research_run_id,
                job_id=target_job,
                phase=run.phase,
                status=run.status,
                schedule_enabled=True,
                batches_run=batches_run,
                symbols_processed=symbols_processed,
                notes=notes + [str(exc)],
                dry_run=False,
            )
        if live is None or not live.schedule_enabled:
            stop_reason = "paused"
            break
        if live.phase != "phase1_first_pass":
            stop_reason = "past_phase1_gate"
            break
        if str(live.status or "") in {"failed", "budget_exhausted", "cancelled"}:
            stop_reason = f"run_{live.status}"
            break
        if str(live.first_pass_job_id or "") != target_job:
            stop_reason = "authoritative_job_changed"
            break

        try:
            result = first_pass_job_service.run_batch(
                target_job,
                user_id=owner,
                batch_size=max(1, min(int(batch_size), 25)),
                time_budget_seconds=float(time_budget_seconds),
            )
        except KeyError as exc:
            return FirstPassDrainResult(
                outcome="failed_job_not_found",
                user_id=owner,
                research_run_id=run.research_run_id,
                job_id=target_job,
                phase=run.phase,
                status=run.status,
                schedule_enabled=True,
                batches_run=batches_run,
                symbols_processed=symbols_processed,
                notes=notes + [str(exc)],
                dry_run=False,
            )
        except FirstPassJobError as exc:
            return FirstPassDrainResult(
                outcome="failed_batch",
                user_id=owner,
                research_run_id=run.research_run_id,
                job_id=target_job,
                phase=run.phase,
                status=run.status,
                schedule_enabled=True,
                batches_run=batches_run,
                symbols_processed=symbols_processed,
                notes=notes + [f"{exc.operation}:{exc}"],
                dry_run=False,
            )

        last_job = result.get("job") if isinstance(result.get("job"), dict) else last_job
        if result.get("jobFailed"):
            stop_reason = "job_failed"
            break
        if result.get("leaseLost"):
            stop_reason = "lease_lost"
            break
        if result.get("leaseHeld"):
            lease_held = True
            stop_reason = "lease_held"
            break
        if result.get("waitingForRetry"):
            waiting_for_retry = True
            stop_reason = "waiting_for_retry"
            break
        if result.get("alreadyComplete"):
            already_complete = True
            stop_reason = "already_complete"
            break

        batches_run += 1
        symbols_processed += int(result.get("processed") or 0)
        job_status = str((result.get("job") or {}).get("status") or "")
        if job_status in {"completed", "partial", "failed", "budget_exhausted"}:
            already_complete = job_status in {"completed", "partial"}
            stop_reason = f"job_{job_status}"
            break
        if job_status == "deferred":
            waiting_for_retry = True
            stop_reason = "deferred"
            break
        if int(result.get("processed") or 0) == 0 and not result.get("batchIds"):
            stop_reason = "idle_no_work"
            break

    # Reconcile moves completed jobs to awaiting_analyst_candidate_selection.
    try:
        reconciled = research_run_store.reconcile(owner)
    except ResearchRunStoreUnavailable as exc:
        notes.append(f"reconcile_failed:{exc}")
        reconciled = run
    except ResearchRunConflict as exc:
        notes.append(f"reconcile_conflict:{exc}")
        try:
            reconciled = research_run_store.get(owner) or run
        except ResearchRunStoreUnavailable:
            reconciled = run

    # Lazy-complete pending deletions after drain work (#355).
    try:
        from app.repositories.tw_research_repository import tw_research_repository

        if tw_research_repository.is_available():
            tw_research_repository.try_complete_pending_first_pass_deletions(
                owner, limit=3
            )
    except Exception:  # noqa: BLE001 — drain must not fail on GC
        notes.append("pending_deletion_gc_failed")

    # Best-effort tick telemetry — must not fail the drain tick.
    try:
        research_run_store.record_first_pass_drain_tick(
            owner,
            last_tick_at=datetime.now(UTC).isoformat(),
            last_outcome=stop_reason,
        )
        try:
            reconciled = research_run_store.get(owner) or reconciled
        except ResearchRunStoreUnavailable:
            pass
    except Exception as exc:  # noqa: BLE001
        notes.append(f"tick_telemetry_failed:{type(exc).__name__}")

    return FirstPassDrainResult(
        outcome=stop_reason,
        user_id=owner,
        research_run_id=(reconciled.research_run_id if reconciled else run.research_run_id),
        job_id=target_job,
        phase=(reconciled.phase if reconciled else run.phase),
        status=(reconciled.status if reconciled else run.status),
        schedule_enabled=bool(
            reconciled.schedule_enabled if reconciled else run.schedule_enabled
        ),
        batches_run=batches_run,
        symbols_processed=symbols_processed,
        waiting_for_retry=waiting_for_retry,
        lease_held=lease_held,
        already_complete=already_complete,
        notes=notes
        + [
            f"stop={stop_reason}",
            f"jobStatus={(last_job or {}).get('status')}",
        ],
        dry_run=False,
    )
