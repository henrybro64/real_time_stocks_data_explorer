"""Durable opt-in First-Pass / Phase-2 drain for managed research runs (#349/#361).

Phase-1: drains only the authoritative ``research_run.first_pass_job_id`` when
``schedule_enabled`` is true. Reuses leased first-pass ``run_batch``.

Phase-2: when candidate selection is approved and a run-owned
``technical_scan_id`` is already attached, drains ONE bounded technical batch
via ``tw_research_scan_service.run_batch``. Does not require ``schedule_enabled``
(Phase-1 disarm clears it at the candidate gate). Never auto-creates technical
scans, never writes Notion, never auto-starts Phase-2 without an attached scan,
and never drains orphan/foreign jobs or scans.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.tw_research.constants import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    HISTORY_BUDGET_EXHAUSTED_REASON,
    PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
    SCAN_BATCH_SIZE,
)
from app.services.tw_research.first_pass_job import FirstPassJobError, first_pass_job_service
from app.services.tw_research.research_run import (
    ResearchRun,
    ResearchRunConflict,
    ResearchRunStoreUnavailable,
    first_pass_drain_availability,
    research_run_store,
)
from app.services.tw_research.scan import (
    ScanState,
    TwResearchConflictError,
    tw_research_scan_service,
)
from app.services.tw_research.scheduler import schedule_has_technical_work

logger = logging.getLogger(__name__)
_ISO_Z_UTC_SUFFIX = "+00:00"
_TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass
class FirstPassDrainResult:
    outcome: str
    user_id: str
    research_run_id: str | None = None
    job_id: str | None = None
    technical_scan_id: str | None = None
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
            "technicalScanId": self.technical_scan_id,
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
    "Drains only the research-run owned first-pass job or attached technical scan.",
    "Never resumes orphan/historical pending jobs without schedule_enabled.",
    "Phase-1 stops at awaiting_analyst_candidate_selection; no Notion auto-publish.",
    "Phase-2 drains only an already-attached run-owned technical scan after approval.",
)
_BLOCKED_RUN_STATUSES = frozenset({"failed", "budget_exhausted", "cancelled"})
# Phase-2 may still resume a daily budget window after run status is
# ``budget_exhausted`` (recoverable until failed/cancelled).
_PHASE2_BLOCKED_RUN_STATUSES = frozenset({"failed", "cancelled"})
_TERMINAL_JOB_STATUSES = frozenset(
    {"completed", "partial", "failed", "budget_exhausted"}
)
_PHASE2_TERMINAL_SCAN = frozenset({"completed_processing", "verified_ready"})
_PHASE2_BLOCKED_SCAN = frozenset({"cancelled", "failed"})


def _early_skip(
    *,
    outcome: str,
    owner: str,
    notes: list[str],
    dry_run: bool,
    run: ResearchRun | None = None,
    job_id: str | None = None,
    technical_scan_id: str | None = None,
    schedule_enabled: bool | None = None,
    preflight: dict[str, Any] | None = None,
) -> FirstPassDrainResult:
    """Shared early-exit builder (dry-run attaches availability flags only)."""
    enabled = (
        bool(run.schedule_enabled)
        if schedule_enabled is None and run is not None
        else bool(schedule_enabled)
    )
    scan_id = technical_scan_id
    if scan_id is None and run is not None:
        scan_id = str(run.technical_scan_id or "") or None
    return FirstPassDrainResult(
        outcome=outcome,
        user_id=owner,
        research_run_id=run.research_run_id if run is not None else None,
        job_id=job_id,
        technical_scan_id=scan_id,
        phase=run.phase if run is not None else None,
        status=run.status if run is not None else None,
        schedule_enabled=enabled,
        notes=notes,
        dry_run=dry_run,
        preflight=preflight
        if preflight is not None
        else _dry_run_availability_preflight(dry_run=dry_run, user_id=owner),
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



def _candidate_approved(run: ResearchRun) -> bool:
    return str((run.gates or {}).get("candidateSelection") or "") == "approved"


def _phase1_eligible(run: ResearchRun, *, target_job: str | None) -> bool:
    return bool(
        run.schedule_enabled
        and run.phase == "phase1_first_pass"
        and target_job
        and str(run.status or "") not in _BLOCKED_RUN_STATUSES
    )


def _phase2_meta(run: ResearchRun) -> dict[str, Any]:
    raw = (run.dimensions or {}).get("phase2Drain")
    return dict(raw) if isinstance(raw, dict) else {}


def _phase2_enabled(run: ResearchRun) -> bool:
    """Explicit pause flag; missing key stays armed for legacy attached scans."""
    meta = _phase2_meta(run)
    if "enabled" not in meta:
        return True
    return bool(meta.get("enabled"))


def _lease_still_active(reserve_expires_at: object) -> bool:
    if not isinstance(reserve_expires_at, str) or not reserve_expires_at.strip():
        return False
    try:
        parsed = datetime.fromisoformat(
            reserve_expires_at.replace("Z", _ISO_Z_UTC_SUFFIX)
        )
    except ValueError:
        return False
    return parsed > datetime.now(UTC)


def _phase2_now() -> datetime:
    """Injectable Asia/Taipei clock for Phase-2 daily window caps (tests)."""
    return datetime.now(_TAIPEI)


def _phase2_calendar_day(*, now: datetime | None = None) -> str:
    """CURRENT Asia/Taipei calendar date (not scan/run trading_day)."""
    current = now if now is not None else _phase2_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=_TAIPEI)
    else:
        current = current.astimezone(_TAIPEI)
    return current.date().isoformat()


def _phase2_windows_for_day(run: ResearchRun, calendar_day: str | None) -> int:
    """Telemetry counter for one Asia/Taipei calendar day."""
    meta = _phase2_meta(run)
    by_day = meta.get("windowsByTradingDay")
    if not isinstance(by_day, dict):
        return 0
    day = str(calendar_day or "") or "_unknown"
    try:
        return int(by_day.get(day) or 0)
    except (TypeError, ValueError):
        return 0


def _phase2_scan_windows_today(
    run: ResearchRun,
    state: ScanState,
    *,
    calendar_day: str | None = None,
) -> int | None:
    """Authoritative windows opened today from scan ``budget_windows_opened``.

    Returns ``None`` when the scan field is missing so callers fall back to
    telemetry. Without a per-day baseline yet, today's scan-derived count is 0.
    """
    if not hasattr(state, "budget_windows_opened"):
        return None
    raw = getattr(state, "budget_windows_opened", None)
    if raw is None:
        return None
    try:
        opened = int(raw)
    except (TypeError, ValueError):
        return None
    day = calendar_day or _phase2_calendar_day()
    meta = _phase2_meta(run)
    baselines = meta.get("windowBaselineByDay")
    if not isinstance(baselines, dict) or day not in baselines:
        return 0
    try:
        baseline = int(baselines[day])
    except (TypeError, ValueError):
        return 0
    return max(0, opened - baseline)


def _phase2_windows_today(
    run: ResearchRun,
    state: ScanState | None = None,
    *,
    calendar_day: str | None = None,
) -> int:
    """Windows opened on the CURRENT Asia/Taipei day for the daily cap.

    Prefer scan-derived ``budget_windows_opened − baseline``; fall back to
    telemetry when the scan field is missing. When both exist, take the max so
    a telemetry persist failure cannot undercount below the scan value.
    """
    day = calendar_day or _phase2_calendar_day()
    telemetry = _phase2_windows_for_day(run, day)
    if state is None:
        return telemetry
    scan_windows = _phase2_scan_windows_today(run, state, calendar_day=day)
    if scan_windows is None:
        return telemetry
    return max(scan_windows, telemetry)


def _phase2_scan_is_complete(state: ScanState) -> bool:
    """Same completion gate as the legacy scheduler (#330/#361)."""
    status = str(getattr(state, "status", "") or "")
    if status not in _PHASE2_TERMINAL_SCAN:
        return False
    return not schedule_has_technical_work(state)


def _phase2_needs_budget_resume(state: ScanState) -> bool:
    status = str(getattr(state, "status", "") or "")
    if status == "budget_exhausted":
        return True
    if schedule_has_technical_work(state) and status in _PHASE2_TERMINAL_SCAN:
        return True
    return False


def _coverage_counts(state: ScanState) -> dict[str, int]:
    """Mutually exclusive Phase-2 symbol buckets for drain coverage (#361/#362).

    ``analyzed`` is evaluated indicator work only — never failed, insufficient,
    or pending rows (avoids double-counting in success-rate style ratios).
    """
    analyzed = 0
    insufficient = 0
    deferred = 0
    failed = 0
    pending = 0
    for stock_id in getattr(state, "stock_ids", ()) or ():
        record = (getattr(state, "records", {}) or {}).get(stock_id)
        if record is None:
            pending += 1
            continue
        reason = str(getattr(record, "rejection_reason", "") or "")
        readiness = str(getattr(record, "readiness", "") or "")
        if reason == HISTORY_BUDGET_EXHAUSTED_REASON or readiness == "source_incomplete":
            insufficient += 1
        elif readiness == "rejected" or reason:
            failed += 1
        else:
            analyzed += 1
        if str(getattr(state, "status", "") or "") == "deferred":
            deferred += 1
    return {
        "analyzed": analyzed,
        "insufficientHistory": insufficient,
        "deferred": deferred,
        "failed": failed,
        "pending": pending,
    }


def _build_phase2_step(run: ResearchRun, *, batch_size: int) -> dict[str, Any]:
    """Read-only Phase-2 step the drain would take (#362/#361). No leases / writes."""
    scan_id = str(run.technical_scan_id or "") or None
    step: dict[str, Any] = {
        "action": "skip",
        "technicalScanId": scan_id,
        "remainingCount": None,
        "scanStatus": None,
        "reason": "phase2_not_eligible",
        "batchSize": max(1, min(int(batch_size), SCAN_BATCH_SIZE)),
        "windowsOpenedToday": None,
        "budgetCap": PHASE2_MAX_BUDGET_WINDOWS_PER_DAY,
        "coverage": None,
    }
    if str(run.status or "") in _PHASE2_BLOCKED_RUN_STATUSES:
        step["reason"] = f"run_status_{run.status}"
        return step
    if not _phase2_enabled(run):
        step["reason"] = "phase2_paused"
        return step
    if not _candidate_approved(run):
        step["reason"] = "not_approved"
        return step
    if not scan_id:
        step["reason"] = "no_technical_scan"
        return step
    try:
        state = tw_research_scan_service.get_scan(scan_id)
    except Exception:  # noqa: BLE001 — preflight stays best-effort
        logger.warning("phase2_preflight scan_lookup_failed scan_id=%s", scan_id)
        step["reason"] = "scan_lookup_failed"
        return step
    if state is None:
        step["reason"] = "scan_missing"
        return step
    snap = state.snapshot()
    step["scanStatus"] = str(snap.get("status") or "") or None
    step["remainingCount"] = int(snap.get("remainingCount") or 0)
    step["coverage"] = _coverage_counts(state)
    calendar_day = _phase2_calendar_day()
    windows_today = _phase2_windows_today(run, state, calendar_day=calendar_day)
    step["windowsOpenedToday"] = windows_today
    step["calendarDay"] = calendar_day
    if str(state.user_id or "") != str(run.user_id or ""):
        step["reason"] = "foreign_scan"
        return step
    status = str(state.status or "")
    if status in _PHASE2_BLOCKED_SCAN:
        step["reason"] = f"scan_{status}"
        return step
    if _phase2_scan_is_complete(state):
        step["reason"] = "already_complete"
        step["action"] = "skip"
        return step
    if _phase2_needs_budget_resume(state):
        if windows_today >= PHASE2_MAX_BUDGET_WINDOWS_PER_DAY:
            step["action"] = "skip"
            step["reason"] = "budget_cap_reached"
            return step
        step["action"] = "resume_budget_and_run"
        step["reason"] = "phase2_eligible_resume"
        return step
    if schedule_has_technical_work(state) or status in {
        "queued",
        "running",
        "partial",
    }:
        step["action"] = "run_technical_batch"
        step["reason"] = "phase2_eligible"
        return step
    # Terminal-looking but not complete — treat as needing resume if work remains.
    if schedule_has_technical_work(state):
        if windows_today >= PHASE2_MAX_BUDGET_WINDOWS_PER_DAY:
            step["reason"] = "budget_cap_reached"
            return step
        step["action"] = "resume_budget_and_run"
        step["reason"] = "phase2_eligible_resume"
        return step
    step["reason"] = "already_complete"
    return step


def _phase2_preflight_payload(
    run: ResearchRun,
    *,
    job_id: str | None,
    batch_size: int,
) -> dict[str, Any]:
    """Dry-run preflight with Phase-2 step; reuses Phase-1 job snapshot when present."""
    if job_id:
        payload = _build_dry_run_preflight(run, job_id=job_id, batch_size=batch_size)
    else:
        payload = {
            "availability": _availability_flags(user_id=run.user_id),
            "researchRunId": run.research_run_id,
            "jobId": None,
            "scheduleEnabled": bool(run.schedule_enabled),
            "batchSize": int(batch_size),
            "lastTickAt": None,
        }
        if isinstance(run.dimensions, dict):
            meta = run.dimensions.get("firstPassDrain")
            if isinstance(meta, dict) and isinstance(meta.get("lastTickAt"), str):
                payload["lastTickAt"] = meta.get("lastTickAt")
    payload["phase2Step"] = _build_phase2_step(run, batch_size=batch_size)
    payload["technicalScanId"] = str(run.technical_scan_id or "") or None
    payload["candidateSelection"] = (run.gates or {}).get("candidateSelection")
    payload["phase2Enabled"] = _phase2_enabled(run)
    return payload


def _record_drain_tick(
    owner: str,
    *,
    stop_reason: str,
    notes: list[str],
    calendar_day: str | None = None,
    trading_day: str | None = None,
    windows_opened_delta: int = 0,
    coverage: dict[str, Any] | None = None,
) -> None:
    day = calendar_day or trading_day or _phase2_calendar_day()
    try:
        research_run_store.record_first_pass_drain_tick(
            owner,
            last_tick_at=datetime.now(UTC).isoformat(),
            last_outcome=stop_reason,
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"tick_telemetry_failed:{type(exc).__name__}")
    try:
        research_run_store.record_phase2_drain_tick(
            owner,
            last_tick_at=datetime.now(UTC).isoformat(),
            last_outcome=stop_reason,
            calendar_day=day,
            windows_opened_delta=int(windows_opened_delta or 0),
            coverage=coverage,
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"phase2_tick_telemetry_failed:{type(exc).__name__}")


def _run_one_technical_batch(
    *,
    scan_id: str,
    batch_size: int,
    time_budget_seconds: float,
):
    timeout = max(5, min(int(float(time_budget_seconds)), DEFAULT_RUN_TIMEOUT_SECONDS))
    capped_batch = max(1, min(int(batch_size), SCAN_BATCH_SIZE))
    return tw_research_scan_service.run_batch(
        str(scan_id),
        batch_size=capped_batch,
        timeout_seconds=timeout,
    )


def _run_phase2_technical_batch(
    *,
    owner: str,
    run: ResearchRun,
    notes: list[str],
    batch_size: int,
    time_budget_seconds: float,
) -> FirstPassDrainResult:
    """Run at most one resume + one leased technical batch for the run-owned scan."""
    step = _build_phase2_step(run, batch_size=batch_size)
    scan_id = step.get("technicalScanId")
    reason = str(step.get("reason") or "")
    action = str(step.get("action") or "skip")

    def _base(**extra: Any) -> dict[str, Any]:
        return {
            "user_id": owner,
            "research_run_id": run.research_run_id,
            "technical_scan_id": str(scan_id) if scan_id else None,
            "phase": run.phase,
            "status": run.status,
            "schedule_enabled": bool(run.schedule_enabled),
            "dry_run": False,
            **extra,
        }

    if str(run.status or "") == "cancelled" or reason.startswith("run_status_cancelled"):
        return FirstPassDrainResult(
            outcome="skipped_run_status",
            notes=notes + [f"Research run status is {run.status}; Phase-2 refused."],
            **_base(),
        )
    if reason == "phase2_paused":
        return FirstPassDrainResult(
            outcome="skipped_phase2_paused",
            notes=notes + ["Phase-2 drain paused (phase2Drain.enabled=false)."],
            **_base(),
        )
    if reason == "not_approved":
        return FirstPassDrainResult(
            outcome="skipped_phase2_not_approved",
            notes=notes + ["candidateSelection gate is not approved."],
            **_base(),
        )
    if reason == "no_technical_scan":
        return FirstPassDrainResult(
            outcome="skipped_phase2_no_scan",
            notes=notes + ["No technical_scan_id attached to research run."],
            **_base(technical_scan_id=None),
        )
    if reason == "scan_missing":
        return FirstPassDrainResult(
            outcome="skipped_phase2_scan_missing",
            notes=notes + [f"Technical scan {scan_id} not found."],
            **_base(),
        )
    if reason == "foreign_scan":
        return FirstPassDrainResult(
            outcome="skipped_phase2_foreign_scan",
            notes=notes + ["Refused foreign technical scan not owned by run user."],
            **_base(),
        )
    if reason.startswith("scan_"):
        return FirstPassDrainResult(
            outcome="skipped_phase2_scan_blocked",
            notes=notes + [f"Scan status blocked: {step.get('scanStatus')}"],
            **_base(),
        )
    if reason == "budget_cap_reached":
        coverage = step.get("coverage") if isinstance(step.get("coverage"), dict) else None
        _record_drain_tick(
            owner,
            stop_reason="phase2_budget_cap_reached",
            notes=notes,
            calendar_day=str(step.get("calendarDay") or "") or _phase2_calendar_day(),
            windows_opened_delta=0,
            coverage=coverage,
        )
        return FirstPassDrainResult(
            outcome="phase2_budget_cap_reached",
            waiting_for_retry=True,
            notes=notes
            + [
                "Phase-2 budget window cap reached for this Asia/Taipei day; "
                "continues next calendar day.",
                f"windowsOpenedToday={step.get('windowsOpenedToday')}",
            ],
            **_base(),
        )
    if reason == "already_complete":
        coverage = step.get("coverage") if isinstance(step.get("coverage"), dict) else {}
        _record_drain_tick(
            owner,
            stop_reason="phase2_already_complete",
            notes=notes,
            coverage=coverage,
        )
        try:
            research_run_store.reconcile(owner)
        except (ResearchRunStoreUnavailable, ResearchRunConflict):
            pass
        return FirstPassDrainResult(
            outcome="phase2_already_complete",
            already_complete=True,
            notes=notes
            + [
                "Technical scan complete for scheduler work predicate.",
                f"analyzed={coverage.get('analyzed')}",
                f"insufficientHistory={coverage.get('insufficientHistory')}",
            ],
            **_base(),
        )

    if action not in {"run_technical_batch", "resume_budget_and_run"}:
        return FirstPassDrainResult(
            outcome="skipped_phase2_not_ready",
            notes=notes + [f"Phase-2 not eligible: {reason}"],
            **_base(),
        )

    assert scan_id is not None
    before = tw_research_scan_service.get_scan(str(scan_id))
    if before is None:
        return FirstPassDrainResult(
            outcome="skipped_phase2_scan_missing",
            notes=notes + [f"Technical scan {scan_id} not found."],
            **_base(),
        )
    calendar_day = _phase2_calendar_day()
    cursor_before = int(getattr(before, "cursor", 0) or 0)
    windows_delta = 0
    resumed_this_tick = False

    def _maybe_resume(state: ScanState, *, force: bool = False) -> ScanState:
        nonlocal windows_delta, resumed_this_tick, before, run
        if resumed_this_tick and not force:
            return state
        if not (_phase2_needs_budget_resume(state) or force):
            return state
        windows_today = _phase2_windows_today(
            run, state, calendar_day=calendar_day
        )
        if windows_today >= PHASE2_MAX_BUDGET_WINDOWS_PER_DAY:
            raise TwResearchConflictError(
                "Phase-2 daily budget window cap reached.",
                scan_id=str(scan_id),
                operation="resume_budget_window",
                code="phase2_budget_cap_reached",
            )
        # Persist per-day baseline from scan counter at first resume of the day.
        try:
            opened_raw = getattr(state, "budget_windows_opened", None)
            if opened_raw is not None:
                opened_before = int(opened_raw)
                refreshed = research_run_store.ensure_phase2_window_baseline(
                    owner,
                    calendar_day=calendar_day,
                    budget_windows_opened=opened_before,
                )
                if refreshed is not None:
                    run = refreshed
        except (TypeError, ValueError, ResearchRunStoreUnavailable, ResearchRunConflict):
            pass
        state = tw_research_scan_service.resume_budget_window(str(scan_id))
        windows_delta += 1
        resumed_this_tick = True
        notes.append("budget_window_resumed")
        before = state
        return state

    if action == "resume_budget_and_run":
        try:
            before = _maybe_resume(before, force=True)
            cursor_before = int(getattr(before, "cursor", 0) or 0)
        except TwResearchConflictError as exc:
            code = getattr(exc, "code", None) or "conflict"
            if code in {"phase2_budget_cap_reached", "budget_cap_reached"}:
                _record_drain_tick(
                    owner,
                    stop_reason="phase2_budget_cap_reached",
                    notes=notes,
                    calendar_day=calendar_day,
                    windows_opened_delta=windows_delta,
                    coverage=_coverage_counts(before),
                )
                return FirstPassDrainResult(
                    outcome="phase2_budget_cap_reached",
                    waiting_for_retry=True,
                    notes=notes + [f"{code}:{exc}"],
                    **_base(),
                )
            return FirstPassDrainResult(
                outcome="failed_phase2_batch",
                notes=notes + [f"{code}:{exc}"],
                **_base(),
            )

    try:
        result_state = _run_one_technical_batch(
            scan_id=str(scan_id),
            batch_size=batch_size,
            time_budget_seconds=time_budget_seconds,
        )
    except KeyError as exc:
        return FirstPassDrainResult(
            outcome="failed_phase2_scan_not_found",
            notes=notes + [str(exc)],
            **_base(),
        )
    except TwResearchConflictError as exc:
        code = getattr(exc, "code", None) or "conflict"
        if code == "budget_resume_required" and not resumed_this_tick:
            try:
                before = _maybe_resume(before, force=True)
                cursor_before = int(getattr(before, "cursor", 0) or 0)
                result_state = _run_one_technical_batch(
                    scan_id=str(scan_id),
                    batch_size=batch_size,
                    time_budget_seconds=time_budget_seconds,
                )
            except TwResearchConflictError as resume_exc:
                rcode = getattr(resume_exc, "code", None) or "conflict"
                if rcode in {"phase2_budget_cap_reached", "budget_cap_reached"}:
                    _record_drain_tick(
                        owner,
                        stop_reason="phase2_budget_cap_reached",
                        notes=notes,
                        calendar_day=calendar_day,
                        windows_opened_delta=windows_delta,
                        coverage=_coverage_counts(before),
                    )
                    return FirstPassDrainResult(
                        outcome="phase2_budget_cap_reached",
                        waiting_for_retry=True,
                        notes=notes + [f"{rcode}:{resume_exc}"],
                        **_base(),
                    )
                return FirstPassDrainResult(
                    outcome="failed_phase2_batch",
                    notes=notes + [f"{rcode}:{resume_exc}"],
                    **_base(),
                )
            except Exception as resume_exc:  # noqa: BLE001
                logger.exception("phase2_resume_then_batch_failed scan_id=%s", scan_id)
                return FirstPassDrainResult(
                    outcome="failed_phase2_batch",
                    notes=notes + [f"{type(resume_exc).__name__}:{resume_exc}"],
                    **_base(),
                )
        elif code == "budget_resume_required":
            # Already resumed once this tick — do not loop.
            _record_drain_tick(
                owner,
                stop_reason="phase2_waiting_for_budget",
                notes=notes,
                calendar_day=calendar_day,
                windows_opened_delta=windows_delta,
                coverage=_coverage_counts(before),
            )
            return FirstPassDrainResult(
                outcome="phase2_waiting_for_budget",
                waiting_for_retry=True,
                notes=notes + [f"{code}:{exc}", "resume_already_attempted_this_tick"],
                **_base(),
            )
        elif code == "technical_scan_cancelled":
            return FirstPassDrainResult(
                outcome="skipped_phase2_scan_blocked",
                notes=notes + [f"{code}:{exc}"],
                **_base(),
            )
        else:
            return FirstPassDrainResult(
                outcome="failed_phase2_batch",
                notes=notes + [f"{code}:{exc}"],
                **_base(),
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("phase2_technical_batch_failed scan_id=%s", scan_id)
        return FirstPassDrainResult(
            outcome="failed_phase2_batch",
            notes=notes + [f"{type(exc).__name__}:{exc}"],
            **_base(),
        )

    after_snap = result_state.snapshot()
    cursor_after = int(getattr(result_state, "cursor", 0) or after_snap.get("cursor") or 0)
    status_after = str(getattr(result_state, "status", "") or after_snap.get("status") or "")
    symbols = max(0, cursor_after - cursor_before)
    coverage = _coverage_counts(result_state)
    lease_held = symbols == 0 and _lease_still_active(
        getattr(result_state, "reserve_expires_at", None)
    )
    already_complete = _phase2_scan_is_complete(result_state)

    if lease_held:
        stop_reason = "phase2_lease_held"
    elif already_complete:
        stop_reason = "phase2_already_complete"
    elif symbols == 0:
        stop_reason = "phase2_no_progress"
    elif status_after == "budget_exhausted" and schedule_has_technical_work(result_state):
        # Batch hit budget again; next tick may resume (cap permitting).
        stop_reason = "phase2_batch_ran"
    else:
        stop_reason = "phase2_batch_ran"

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

    _record_drain_tick(
        owner,
        stop_reason=stop_reason,
        notes=notes,
        calendar_day=calendar_day,
        windows_opened_delta=windows_delta,
        coverage=coverage,
    )
    try:
        reconciled = research_run_store.get(owner) or reconciled
    except ResearchRunStoreUnavailable:
        pass

    batches_run = 1 if symbols > 0 or stop_reason == "phase2_batch_ran" else 0
    if stop_reason in {"phase2_lease_held", "phase2_no_progress"}:
        batches_run = 0
    if already_complete and symbols == 0:
        batches_run = 0

    extra_notes = [
        f"stop={stop_reason}",
        f"scanStatus={status_after}",
        f"windowsOpenedDelta={windows_delta}",
        f"analyzed={coverage.get('analyzed')}",
        f"insufficientHistory={coverage.get('insufficientHistory')}",
        f"deferred={coverage.get('deferred')}",
        f"failed={coverage.get('failed')}",
    ]
    return FirstPassDrainResult(
        outcome=stop_reason,
        user_id=owner,
        research_run_id=(reconciled.research_run_id if reconciled else run.research_run_id),
        technical_scan_id=str(scan_id),
        phase=(reconciled.phase if reconciled else run.phase),
        status=(reconciled.status if reconciled else run.status),
        schedule_enabled=bool(
            reconciled.schedule_enabled if reconciled else run.schedule_enabled
        ),
        batches_run=batches_run,
        symbols_processed=symbols,
        waiting_for_retry=stop_reason
        in {"phase2_waiting_for_budget", "phase2_budget_cap_reached", "phase2_no_progress"},
        lease_held=lease_held,
        already_complete=already_complete,
        notes=notes + extra_notes,
        dry_run=False,
    )


def _dry_run_outcome_for_run(
    run: ResearchRun,
    *,
    target_job: str | None,
    phase2_step: dict[str, Any] | None = None,
) -> str:
    if _phase1_eligible(run, target_job=target_job):
        return "dry_run_ready"
    step = phase2_step or {}
    # Recoverable Phase-2 budget exhaustion can still resume — prefer that over
    # treating run.status=budget_exhausted as a hard block.
    if step.get("action") in {"run_technical_batch", "resume_budget_and_run"}:
        return "dry_run_phase2_ready"
    if str(run.status or "") in _BLOCKED_RUN_STATUSES:
        return "dry_run_run_status_blocked"
    if _candidate_approved(run) and run.technical_scan_id:
        return "dry_run_phase2_ready"
    if run.phase == "phase1_first_pass" and not run.schedule_enabled:
        return "dry_run_not_authorized"
    if run.phase != "phase1_first_pass":
        return "dry_run_past_phase1_gate"
    return "dry_run_not_authorized"


def _live_drain_guard(
    run: ResearchRun,
    *,
    owner: str,
    target_job: str,
    notes: list[str],
) -> FirstPassDrainResult | None:
    """Refuse Phase-1 work when schedule/phase/status blocks it."""
    if str(run.status or "") in _BLOCKED_RUN_STATUSES:
        return _early_skip(
            outcome="skipped_run_status",
            owner=owner,
            run=run,
            job_id=target_job,
            notes=notes + [f"Research run status is {run.status}; drain refused."],
            dry_run=False,
        )
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
            + [f"Phase is {run.phase}; Phase-1 drain stops at analyst candidate selection."],
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


def _run_drain_batches(  # NOSONAR(python:S3776) — batch loop control flow; #362 wires provider summary
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
    provider_batch_summary: dict[str, Any] | None = None

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
        summary = result.get("providerBatchSummary")
        if isinstance(summary, dict):
            provider_batch_summary = summary
        batches_run += int(flags.get("batches_delta") or 0)
        symbols_processed += int(flags.get("symbols_delta") or 0)
        lease_held = lease_held or bool(flags.get("lease_held"))
        waiting_for_retry = waiting_for_retry or bool(flags.get("waiting_for_retry"))
        already_complete = already_complete or bool(flags.get("already_complete"))
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
        provider_batch_summary=provider_batch_summary,
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
    provider_batch_summary: dict[str, Any] | None = None,
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
            provider_batch_summary=provider_batch_summary,
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
    """Run up to ``max_batches`` leased first-pass batches, or one Phase-2 technical batch.

    Refuses work when:
    - global drain flag is false
    - research run missing / store unavailable
    - Phase-1: ``schedule_enabled`` is false, or job is not authoritative
    - Phase-2: candidates not approved, no attached scan, foreign/cancelled scan
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
    target_job: str | None = None
    # Resolve authoritative job when Phase-1 may apply or caller passed job_id.
    if run.first_pass_job_id or requested:
        target_job, refused = _resolve_authoritative_job(
            run, owner=owner, requested=requested, notes=notes, dry_run=dry_run
        )
        if refused is not None:
            # Non-authoritative explicit job_id always refuses; missing job only
            # blocks when Phase-1 would otherwise run.
            if requested or _phase1_eligible(run, target_job=run.first_pass_job_id):
                # If Phase-2 can still run and refusal is only missing job without
                # requested id, fall through — but skipped_no_job has no requested.
                if refused.outcome == "skipped_non_authoritative_job":
                    return refused
                if refused.outcome == "skipped_no_job" and requested:
                    return refused
            if refused.outcome == "skipped_non_authoritative_job":
                return refused
            if refused.outcome == "skipped_no_job" and not (
                _candidate_approved(run) or run.technical_scan_id
            ):
                return refused
        # refused is skipped_no_job while Phase-2 may still apply → continue

    phase2_step = _build_phase2_step(run, batch_size=batch_size)
    phase1_ok = _phase1_eligible(run, target_job=target_job)

    if dry_run:
        preflight = _phase2_preflight_payload(
            run, job_id=target_job or run.first_pass_job_id, batch_size=batch_size
        )
        return FirstPassDrainResult(
            outcome=_dry_run_outcome_for_run(
                run, target_job=target_job, phase2_step=phase2_step
            ),
            user_id=owner,
            research_run_id=run.research_run_id,
            job_id=target_job or run.first_pass_job_id,
            technical_scan_id=run.technical_scan_id,
            phase=run.phase,
            status=run.status,
            schedule_enabled=bool(run.schedule_enabled),
            notes=notes + ["Dry run — no batches claimed; no writes."],
            dry_run=True,
            preflight=preflight,
        )

    if phase1_ok:
        assert target_job is not None
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

    # Phase-2 path: approved + attached scan (or honest skip outcomes).
    if (
        _candidate_approved(run)
        or run.technical_scan_id
        or str(run.status or "") == "cancelled"
        or not _phase2_enabled(run)
    ):
        if phase2_step.get("action") in {
            "run_technical_batch",
            "resume_budget_and_run",
        } or phase2_step.get("reason") in {
            "not_approved",
            "no_technical_scan",
            "scan_missing",
            "foreign_scan",
            "waiting_for_budget_window",
            "already_complete",
            "phase2_paused",
            "budget_cap_reached",
        } or str(phase2_step.get("reason") or "").startswith("scan_") or str(
            phase2_step.get("reason") or ""
        ).startswith("run_status_"):
            return _run_phase2_technical_batch(
                owner=owner,
                run=run,
                notes=notes,
                batch_size=batch_size,
                time_budget_seconds=time_budget_seconds,
            )

    if target_job:
        blocked = _live_drain_guard(run, owner=owner, target_job=target_job, notes=notes)
        if blocked is not None:
            return blocked
    if str(run.status or "") in _BLOCKED_RUN_STATUSES:
        return _early_skip(
            outcome="skipped_run_status",
            owner=owner,
            run=run,
            job_id=target_job or run.first_pass_job_id,
            notes=notes + [f"Research run status is {run.status}; drain refused."],
            dry_run=False,
        )
    if run.phase != "phase1_first_pass":
        return _early_skip(
            outcome="skipped_past_phase1_gate",
            owner=owner,
            run=run,
            job_id=target_job or run.first_pass_job_id,
            notes=notes
            + [
                f"Phase is {run.phase}; Phase-1 drain stops at analyst gate "
                "(Phase-2 requires approved candidates + attached technical scan)."
            ],
            dry_run=False,
        )
    return _early_skip(
        outcome="skipped_not_authorized",
        owner=owner,
        run=run,
        job_id=target_job or run.first_pass_job_id,
        schedule_enabled=bool(run.schedule_enabled),
        notes=notes + ["schedule_enabled=false — Start Research sets this; pause clears it."],
        dry_run=False,
    )
