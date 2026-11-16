"""Durable opt-in First-Pass job drain for managed research runs (#349).

Drains only the authoritative ``research_run.first_pass_job_id`` when
``schedule_enabled`` is true. Reuses existing leased ``run_batch`` machinery.
Never advances analyst gates, never starts Phase-2, never writes Notion, and
never drains historical orphan jobs that are not run-owned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.services.tw_research.first_pass_job import FirstPassJobError, first_pass_job_service
from app.services.tw_research.research_run import (
    ResearchRun,
    ResearchRunConflict,
    ResearchRunStoreUnavailable,
    first_pass_drain_availability,
    research_run_store,
)

logger = logging.getLogger(__name__)
_ISO_Z_UTC_SUFFIX = "+00:00"


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
    # Read-only preflight fields (#362). Never include secret values.
    preflight: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
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
        if self.preflight is not None:
            payload["preflight"] = dict(self.preflight)
        return payload


def _availability_flags(*, user_id: str) -> dict[str, Any]:
    """Booleans only — never return token/owner values."""
    enabled = bool(settings.tw_research_first_pass_drain_enabled)
    owner_configured = bool(str(settings.tw_research_first_pass_drain_user_id or "").strip())
    token_configured = bool(str(settings.tw_research_first_pass_drain_token or "").strip())
    available, reason = first_pass_drain_availability(user_id)
    return {
        "drainEnabledConfigured": enabled,
        "drainOwnerConfigured": owner_configured,
        "drainTokenConfigured": token_configured,
        "available": bool(available),
        "reason": reason,
    }


def _dry_run_availability_preflight(*, dry_run: bool, user_id: str) -> dict[str, Any] | None:
    """Attach availability flags (booleans + reason only) on dry-run early exits."""
    if not dry_run:
        return None
    return {"availability": _availability_flags(user_id=user_id)}


def _build_dry_run_preflight(
    run: ResearchRun,
    *,
    job_id: str,
    batch_size: int,
) -> dict[str, Any]:
    """Read-only job/run snapshot for dry-run. No leases, no provider calls."""
    flags = _availability_flags(user_id=run.user_id)
    job_status = None
    universe_size = None
    ok_count = partial_count = failed_count = pending_count = None
    lease_active = False
    last_tick_at = None
    preflight_errors: list[str] = []
    if isinstance(run.dimensions, dict):
        meta = run.dimensions.get("firstPassDrain")
        if isinstance(meta, dict) and isinstance(meta.get("lastTickAt"), str):
            last_tick_at = meta.get("lastTickAt")
    try:
        job = first_pass_job_service.get_job(job_id, user_id=run.user_id)
        job_status = str(job.get("status") or "") or None
        universe_size = job.get("universe_size") or job.get("universeSize")
        if universe_size is None:
            ids = job.get("stock_ids") or job.get("stockIds") or []
            if isinstance(ids, list):
                universe_size = len(ids)
        lease_until = job.get("reserve_expires_at") or job.get("reserveExpiresAt")
        if isinstance(lease_until, str) and lease_until.strip():
            try:
                parsed = datetime.fromisoformat(
                    lease_until.replace("Z", _ISO_Z_UTC_SUFFIX)
                )
                lease_active = parsed > datetime.now(UTC)
            except ValueError:
                lease_active = False
    except Exception:  # noqa: BLE001 — preflight stays best-effort
        logger.warning("dry_run_preflight job_lookup_failed job_id=%s", job_id)
        preflight_errors.append("job_lookup_failed")

    try:
        from app.repositories.tw_research_repository import tw_research_repository

        if tw_research_repository.is_available():
            counts = tw_research_repository.count_first_pass_symbols(job_id)
            if isinstance(counts, dict):
                ok_count = int(counts.get("ok") or 0)
                partial_count = int(counts.get("partial") or 0)
                failed_count = int(counts.get("failed") or 0)
                total = int(counts.get("total") or 0)
                if universe_size is None and total:
                    universe_size = total
                if universe_size is not None:
                    pending_count = max(
                        0, int(universe_size) - ok_count - partial_count - failed_count
                    )
    except Exception:  # noqa: BLE001 — preflight stays best-effort
        logger.warning("dry_run_preflight counts_unavailable job_id=%s", job_id)
        preflight_errors.append("counts_unavailable")

    payload: dict[str, Any] = {
        "availability": flags,
        "researchRunId": run.research_run_id,
        "jobId": job_id,
        "scheduleEnabled": bool(run.schedule_enabled),
        "jobStatus": job_status,
        "universeSize": universe_size,
        "okCount": ok_count,
        "partialCount": partial_count,
        "failedCount": failed_count,
        "pendingCount": pending_count,
        "leaseActive": lease_active,
        "batchSize": int(batch_size),
        "lastTickAt": last_tick_at,
        "estimatedProviderCallsNextBatch": None,
        "estimatedProviderCallsReason": "provider_call_estimate_not_instrumented_per_batch",
    }
    if preflight_errors:
        payload["preflightErrors"] = preflight_errors
    return payload


_BASE_DRAIN_NOTES = (
    "Drains only the research-run owned first-pass job.",
    "Never resumes orphan/historical pending jobs without schedule_enabled.",
    "Stops at awaiting_analyst_candidate_selection; no Notion / Phase-2.",
)
_BLOCKED_RUN_STATUSES = frozenset({"failed", "budget_exhausted", "cancelled"})
_TERMINAL_JOB_STATUSES = frozenset(
    {"completed", "partial", "failed", "budget_exhausted"}
)


def _early_skip(
    *,
    outcome: str,
    owner: str,
    notes: list[str],
    dry_run: bool,
    run: ResearchRun | None = None,
    job_id: str | None = None,
    schedule_enabled: bool | None = None,
) -> FirstPassDrainResult:
    """Shared early-exit builder (dry-run attaches availability flags only)."""
    enabled = (
        bool(run.schedule_enabled)
        if schedule_enabled is None and run is not None
        else bool(schedule_enabled)
    )
    return FirstPassDrainResult(
        outcome=outcome,
        user_id=owner,
        research_run_id=run.research_run_id if run is not None else None,
        job_id=job_id,
        phase=run.phase if run is not None else None,
        status=run.status if run is not None else None,
        schedule_enabled=enabled,
        notes=notes,
        dry_run=dry_run,
        preflight=_dry_run_availability_preflight(dry_run=dry_run, user_id=owner),
    )


def _resolve_authoritative_job(
    run: ResearchRun,
    *,
    owner: str,
    requested: str | None,
    notes: list[str],
    dry_run: bool,
) -> tuple[str | None, FirstPassDrainResult | None]:
    """Return (target_job_id, None) or (None, skip_result)."""
    authoritative = str(run.first_pass_job_id or "") or None
    if requested and not authoritative:
        return None, _early_skip(
            outcome="skipped_non_authoritative_job",
            owner=owner,
            run=run,
            job_id=requested,
            notes=notes
            + [
                f"Refused job {requested}; research run has no authoritative first_pass_job_id.",
                "Historical overlapping jobs are not auto-drained (#349).",
            ],
            dry_run=dry_run,
        )
    if requested and authoritative and requested != authoritative:
        return None, _early_skip(
            outcome="skipped_non_authoritative_job",
            owner=owner,
            run=run,
            job_id=requested,
            notes=notes
            + [
                f"Refused job {requested}; authoritative job is {authoritative}.",
                "Historical overlapping jobs are not auto-drained (#349).",
            ],
            dry_run=dry_run,
        )
    if not authoritative:
        return None, _early_skip(
            outcome="skipped_no_job",
            owner=owner,
            run=run,
            notes=notes + ["Research run has no first_pass_job_id yet."],
            dry_run=dry_run,
        )
    return authoritative, None


def _dry_run_outcome_for_run(run: ResearchRun) -> str:
    if not run.schedule_enabled:
        return "dry_run_not_authorized"
    if run.phase != "phase1_first_pass":
        return "dry_run_past_phase1_gate"
    if str(run.status or "") in _BLOCKED_RUN_STATUSES:
        return "dry_run_run_status_blocked"
    return "dry_run_ready"


def _live_drain_guard(
    run: ResearchRun,
    *,
    owner: str,
    target_job: str,
    notes: list[str],
) -> FirstPassDrainResult | None:
    if not run.schedule_enabled:
        return _early_skip(
            outcome="skipped_not_authorized",
            owner=owner,
            run=run,
            job_id=target_job,
            schedule_enabled=False,
            notes=notes
            + ["schedule_enabled=false — Start Research sets this; pause clears it."],
            dry_run=False,
        )
    if run.phase != "phase1_first_pass":
        return _early_skip(
            outcome="skipped_past_phase1_gate",
            owner=owner,
            run=run,
            job_id=target_job,
            notes=notes
            + [f"Phase is {run.phase}; drain stops at analyst candidate selection."],
            dry_run=False,
        )
    if str(run.status or "") in _BLOCKED_RUN_STATUSES:
        return _early_skip(
            outcome="skipped_run_status",
            owner=owner,
            run=run,
            job_id=target_job,
            notes=notes + [f"Research run status is {run.status}; drain refused."],
            dry_run=False,
        )
    return None


def _batch_loop_stop_reason(live: ResearchRun | None, *, target_job: str) -> str | None:
    if live is None or not live.schedule_enabled:
        return "paused"
    if live.phase != "phase1_first_pass":
        return "past_phase1_gate"
    if str(live.status or "") in _BLOCKED_RUN_STATUSES:
        return f"run_{live.status}"
    if str(live.first_pass_job_id or "") != target_job:
        return "authoritative_job_changed"
    return None


_EARLY_BATCH_FLAGS: tuple[tuple[str, str, dict[str, bool]], ...] = (
    ("jobFailed", "job_failed", {}),
    ("leaseLost", "lease_lost", {}),
    ("leaseHeld", "lease_held", {"lease_held": True}),
    ("waitingForRetry", "waiting_for_retry", {"waiting_for_retry": True}),
    ("alreadyComplete", "already_complete", {"already_complete": True}),
)


def _apply_batch_result(
    result: dict[str, Any],
    *,
    last_job: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (updated_last_job, flags) where flags may include stop/progress."""
    job = result.get("job") if isinstance(result.get("job"), dict) else last_job
    for key, stop, extra in _EARLY_BATCH_FLAGS:
        if result.get(key):
            return job, {"stop": stop, **extra}

    processed = int(result.get("processed") or 0)
    flags: dict[str, Any] = {"batches_delta": 1, "symbols_delta": processed}
    job_status = str((result.get("job") or {}).get("status") or "")
    if job_status in _TERMINAL_JOB_STATUSES:
        flags["stop"] = f"job_{job_status}"
        flags["already_complete"] = job_status in {"completed", "partial"}
    elif job_status == "deferred":
        flags["stop"] = "deferred"
        flags["waiting_for_retry"] = True
    elif processed == 0 and not result.get("batchIds"):
        flags["stop"] = "idle_no_work"
    return job, flags


def _run_drain_batches(
    *,
    owner: str,
    run: ResearchRun,
    target_job: str,
    notes: list[str],
    max_batches: int,
    batch_size: int,
    time_budget_seconds: float,
) -> FirstPassDrainResult:
    batches_run = 0
    symbols_processed = 0
    waiting_for_retry = False
    lease_held = False
    already_complete = False
    stop_reason = "max_batches_reached"
    last_job: dict[str, Any] | None = None

    for _ in range(max(1, int(max_batches))):
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
        early_stop = _batch_loop_stop_reason(live, target_job=target_job)
        if early_stop is not None:
            stop_reason = early_stop
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

        last_job, flags = _apply_batch_result(result, last_job=last_job)
        batches_run += int(flags.get("batches_delta") or 0)
        symbols_processed += int(flags.get("symbols_delta") or 0)
        if flags.get("lease_held"):
            lease_held = True
        if flags.get("waiting_for_retry"):
            waiting_for_retry = True
        if flags.get("already_complete"):
            already_complete = True
        if flags.get("stop"):
            stop_reason = str(flags["stop"])
            break

    return _finalize_drain_tick(
        owner=owner,
        run=run,
        target_job=target_job,
        notes=notes,
        stop_reason=stop_reason,
        batches_run=batches_run,
        symbols_processed=symbols_processed,
        waiting_for_retry=waiting_for_retry,
        lease_held=lease_held,
        already_complete=already_complete,
        last_job=last_job,
    )


def _finalize_drain_tick(
    *,
    owner: str,
    run: ResearchRun,
    target_job: str,
    notes: list[str],
    stop_reason: str,
    batches_run: int,
    symbols_processed: int,
    waiting_for_retry: bool,
    lease_held: bool,
    already_complete: bool,
    last_job: dict[str, Any] | None,
) -> FirstPassDrainResult:
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

    try:
        from app.repositories.tw_research_repository import tw_research_repository

        if tw_research_repository.is_available():
            tw_research_repository.try_complete_pending_first_pass_deletions(
                owner, limit=3
            )
    except Exception:  # noqa: BLE001 — drain must not fail on GC
        notes.append("pending_deletion_gc_failed")

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
    notes: list[str] = list(_BASE_DRAIN_NOTES)
    if not owner:
        return _early_skip(
            outcome="skipped_missing_user",
            owner="",
            notes=notes + ["TW_RESEARCH_FIRST_PASS_DRAIN_USER_ID is empty."],
            dry_run=dry_run,
        )
    if not enabled:
        return _early_skip(
            outcome="skipped_disabled",
            owner=owner,
            notes=notes + ["tw_research_first_pass_drain_enabled=false."],
            dry_run=dry_run,
        )

    try:
        run = research_run_store.get(owner)
    except ResearchRunStoreUnavailable as exc:
        return _early_skip(
            outcome="failed_store_unavailable",
            owner=owner,
            notes=notes + [str(exc)],
            dry_run=dry_run,
        )
    if run is None:
        return _early_skip(
            outcome="skipped_no_research_run",
            owner=owner,
            notes=notes + ["No durable research_run for owner."],
            dry_run=dry_run,
        )

    requested = str(job_id).strip() if job_id else None
    target_job, refused = _resolve_authoritative_job(
        run, owner=owner, requested=requested, notes=notes, dry_run=dry_run
    )
    if refused is not None:
        return refused
    assert target_job is not None

    if dry_run:
        return FirstPassDrainResult(
            outcome=_dry_run_outcome_for_run(run),
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=target_job,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes + ["Dry run — no batches claimed; no writes."],
            dry_run=True,
            preflight=_build_dry_run_preflight(
                run, job_id=str(target_job), batch_size=batch_size
            ),
        )

    blocked = _live_drain_guard(run, owner=owner, target_job=target_job, notes=notes)
    if blocked is not None:
        return blocked

    return _run_drain_batches(
        owner=owner,
        run=run,
        target_job=target_job,
        notes=notes,
        max_batches=max_batches,
        batch_size=batch_size,
        time_budget_seconds=time_budget_seconds,
    )
