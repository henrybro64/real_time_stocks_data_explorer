"""Manual full-research orchestration with explicit analyst gates (#341).

Sequence:
1. User starts Phase-1 first-pass → stop at ``awaiting_analyst_candidate_selection``
2. After candidate set is saved, user starts Phase-2 monthly-first technical
   → stop at ``awaiting_analyst_report_decision``
3. External Agent may publish only after ChatGPT approves a report (out of repo)

PostgreSQL is the authoritative ledger (fail closed when unavailable).
Never auto-publishes Notion. Never enables CronJob. Recoverable pauses map to
``waiting_for_next_window``; hard-cap exhaustion stays ``budget_exhausted``.

Phase/status transitions are **server-derived** via ``reconcile()`` from durable
first-pass job and technical-scan rows — clients cannot assert status.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal, Protocol
from uuid import uuid4

from app.services.tw_research.constants import (
    RESEARCH_RUN_JOB_CLAIM_LEASE_SECONDS,
    RESEARCH_RUN_PHASE2_CLAIM_LEASE_SECONDS,
    RESEARCH_RUN_VERSION,
    WAITING_FOR_NEXT_WINDOW,
)
from app.services.tw_research.evaluation import map_recoverable_status

ResearchRunPhase = Literal[
    "phase1_first_pass",
    "awaiting_analyst_candidate_selection",
    "phase2_technical",
    "awaiting_analyst_report_decision",
    "complete_evidence_ready",
]
ResearchRunStatus = Literal[
    "queued",
    "running",
    "waiting_for_next_window",
    "budget_exhausted",
    "failed",
    "gated",
    "evidence_ready",
]

_NOTES_CAP = 50
_ACTIVE_PHASES = frozenset({"phase1_first_pass", "phase2_technical"})
_TERMINAL_OR_GATED_PHASES = frozenset(
    {
        "awaiting_analyst_candidate_selection",
        "awaiting_analyst_report_decision",
        "complete_evidence_ready",
    }
)
_JOB_TERMINAL = frozenset({"completed", "partial", "failed", "budget_exhausted"})
_SCAN_TERMINAL = frozenset(
    {
        "completed_processing",
        "verified_ready",
        "failed",
        "budget_exhausted",
    }
)


class ResearchRunStoreUnavailable(Exception):
    """Raised when the durable research-run store is unavailable (fail closed)."""


class ResearchRunGateError(Exception):
    """Raised when a manual gate is not satisfied."""


class ResearchRunConflict(Exception):
    """Raised when an optimistic concurrency write loses (HTTP 409)."""

    def __init__(self, message: str = "research_run_revision_conflict") -> None:
        super().__init__(message)
        self.reason = "revision_conflict"


class _ResearchRunRepository(Protocol):
    def is_available(self) -> bool: ...

    def get_research_run(self, user_id: str) -> dict[str, Any] | None: ...

    def upsert_research_run(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def delete_research_run(self, user_id: str) -> None: ...


@dataclass
class ResearchRun:
    research_run_id: str
    user_id: str
    version: str = RESEARCH_RUN_VERSION
    phase: ResearchRunPhase = "phase1_first_pass"
    status: str = "queued"
    trading_day: str | None = None
    first_pass_job_id: str | None = None
    first_pass_snapshot_id: str | None = None
    candidate_set_id: str | None = None
    candidate_set_version: int | None = None
    technical_scan_id: str | None = None
    gates: dict[str, Any] = field(default_factory=dict)
    dimensions: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    schedule_enabled: bool = False
    revision: int = 0
    job_claim_token: str | None = None
    job_claim_expires_at: str | None = None
    pending_first_pass_job_id: str | None = None
    phase2_claim_token: str | None = None
    phase2_claim_expires_at: str | None = None
    pending_technical_scan_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["researchRunId"] = payload.pop("research_run_id")
        payload["userId"] = payload.pop("user_id")
        payload["firstPassJobId"] = payload.pop("first_pass_job_id")
        payload["firstPassSnapshotId"] = payload.pop("first_pass_snapshot_id")
        payload["candidateSetId"] = payload.pop("candidate_set_id")
        payload["candidateSetVersion"] = payload.pop("candidate_set_version")
        payload["technicalScanId"] = payload.pop("technical_scan_id")
        payload["tradingDay"] = payload.pop("trading_day")
        payload["createdAt"] = payload.pop("created_at")
        payload["updatedAt"] = payload.pop("updated_at")
        payload["scheduleEnabled"] = payload.pop("schedule_enabled")
        payload["jobClaimToken"] = payload.pop("job_claim_token")
        payload["jobClaimExpiresAt"] = payload.pop("job_claim_expires_at")
        payload["pendingFirstPassJobId"] = payload.pop("pending_first_pass_job_id")
        payload["phase2ClaimToken"] = payload.pop("phase2_claim_token")
        payload["phase2ClaimExpiresAt"] = payload.pop("phase2_claim_expires_at")
        payload["pendingTechnicalScanId"] = payload.pop("pending_technical_scan_id")
        payload["notNotionPublisher"] = True
        payload["cronjobRemainsSuspended"] = True
        payload["durable"] = True
        return payload

    def as_persist_payload(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "research_run_id": self.research_run_id,
            "version": self.version,
            "phase": self.phase,
            "status": self.status,
            "trading_day": self.trading_day,
            "first_pass_job_id": self.first_pass_job_id,
            "first_pass_snapshot_id": self.first_pass_snapshot_id,
            "candidate_set_id": self.candidate_set_id,
            "candidate_set_version": self.candidate_set_version,
            "technical_scan_id": self.technical_scan_id,
            "gates": dict(self.gates),
            "dimensions": dict(self.dimensions),
            "notes": list(self.notes)[-_NOTES_CAP:],
            "schedule_enabled": bool(self.schedule_enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": int(self.revision),
            "job_claim_token": self.job_claim_token,
            "job_claim_expires_at": self.job_claim_expires_at,
            "pending_first_pass_job_id": self.pending_first_pass_job_id,
            "phase2_claim_token": self.phase2_claim_token,
            "phase2_claim_expires_at": self.phase2_claim_expires_at,
            "pending_technical_scan_id": self.pending_technical_scan_id,
        }

    @classmethod
    def from_persist_payload(cls, payload: dict[str, Any]) -> ResearchRun:
        return cls(
            research_run_id=str(payload["research_run_id"]),
            user_id=str(payload["user_id"]),
            version=str(payload.get("version") or RESEARCH_RUN_VERSION),
            phase=payload.get("phase") or "phase1_first_pass",  # type: ignore[arg-type]
            status=str(payload.get("status") or "queued"),
            trading_day=payload.get("trading_day"),
            first_pass_job_id=payload.get("first_pass_job_id"),
            first_pass_snapshot_id=payload.get("first_pass_snapshot_id"),
            candidate_set_id=payload.get("candidate_set_id"),
            candidate_set_version=(
                int(payload["candidate_set_version"])
                if payload.get("candidate_set_version") is not None
                else None
            ),
            technical_scan_id=payload.get("technical_scan_id"),
            gates=dict(payload.get("gates") or {}),
            dimensions=dict(payload.get("dimensions") or {}),
            notes=list(payload.get("notes") or [])[-_NOTES_CAP:],
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            schedule_enabled=bool(payload.get("schedule_enabled")),
            revision=int(payload.get("revision") or 0),
            job_claim_token=payload.get("job_claim_token"),
            job_claim_expires_at=(
                str(payload["job_claim_expires_at"])
                if payload.get("job_claim_expires_at") is not None
                else None
            ),
            pending_first_pass_job_id=payload.get("pending_first_pass_job_id"),
            phase2_claim_token=payload.get("phase2_claim_token"),
            phase2_claim_expires_at=(
                str(payload["phase2_claim_expires_at"])
                if payload.get("phase2_claim_expires_at") is not None
                else None
            ),
            pending_technical_scan_id=payload.get("pending_technical_scan_id"),
        )

    def append_note(self, note: str) -> None:
        self.notes = [*self.notes, note][-_NOTES_CAP:]


class ResearchRunStore:
    """Durable orchestration ledger. PostgreSQL is source of truth.

    Unit tests may call ``clear_for_tests()`` to enable an isolated in-memory
    backend. Production never enables that path.
    """

    def __init__(self, repository: _ResearchRunRepository | None = None) -> None:
        self._repository = repository
        self._lock = Lock()
        self._test_memory: dict[str, ResearchRun] | None = None

    def get(self, user_id: str) -> ResearchRun | None:
        with self._lock:
            if self._test_memory is not None:
                return self._test_memory.get(user_id)
            row = self._repo().get_research_run(user_id)
            if row is None:
                return None
            return ResearchRun.from_persist_payload(row)

    def reconcile(self, user_id: str) -> ResearchRun | None:
        """Derive phase/status from durable first-pass job + technical scan rows."""
        with self._lock:
            run = self._load_locked(user_id)
            if run is None:
                return None
            changed = self._reconcile_locked(run)
            if changed:
                return self._persist_locked(run)
            return run

    def begin_phase1(
        self,
        user_id: str,
        *,
        trading_day: str | None = None,
        notes: list[str] | None = None,
    ) -> ResearchRun:
        """Create or return the active run **before** the first-pass job exists.

        Idempotent: an active phase1/phase2 run is returned unchanged (HTTP 200).
        """
        now = datetime.now(UTC).isoformat()
        with self._lock:
            existing = self._load_locked(user_id)
            if existing is not None:
                changed = self._reconcile_locked(existing)
                if changed:
                    existing = self._persist_locked(existing)
                if self._is_active_nonterminal(existing):
                    return existing
            run = ResearchRun(
                research_run_id=f"rr_{uuid4().hex[:20]}",
                user_id=user_id,
                phase="phase1_first_pass",
                status="queued",
                trading_day=trading_day,
                first_pass_job_id=None,
                gates={
                    "candidateSelection": "pending",
                    "reportDecision": "blocked_until_phase2",
                    "notionPublication": "external_only_never_auto",
                },
                dimensions={
                    "fundamentals": "queued",
                    "monthlyTechnical": "not_started",
                    "dailyTechnical": "not_started",
                    "chipEnrichment": "not_started",
                    "snapshot": "not_started",
                    "externalPublication": "not_started",
                },
                notes=list(notes or [])
                + [
                    "Manual Phase-1 start. Stops for analyst candidate selection.",
                    "Does not bypass gates or publish Notion.",
                    "CronJob remains suspend:true.",
                ],
                created_at=now,
                updated_at=now,
                revision=0,
            )
            return self._persist_locked(run)

    def attach_first_pass_job(
        self,
        user_id: str,
        *,
        first_pass_job_id: str,
        notes: list[str] | None = None,
    ) -> ResearchRun:
        with self._lock:
            run = self._require_locked(user_id)
            if run.first_pass_job_id and run.first_pass_job_id != first_pass_job_id:
                # Idempotent start already returned an active run with a job.
                return run
            run.first_pass_job_id = first_pass_job_id
            run.status = "running"
            run.phase = "phase1_first_pass"
            run.dimensions["fundamentals"] = "in_progress"
            for note in notes or []:
                run.append_note(note)
            run.updated_at = datetime.now(UTC).isoformat()
            return self._persist_locked(run)

    def start_phase1(
        self,
        user_id: str,
        *,
        trading_day: str | None = None,
        first_pass_job_id: str | None = None,
        notes: list[str] | None = None,
    ) -> ResearchRun:
        """Backward-compatible helper used by older tests — prefer begin+attach."""
        run = self.begin_phase1(user_id, trading_day=trading_day, notes=notes)
        if first_pass_job_id and (
            not run.first_pass_job_id or run.first_pass_job_id == first_pass_job_id
        ):
            if run.phase == "phase1_first_pass" and run.status in {"queued", "running"}:
                return self.attach_first_pass_job(
                    user_id,
                    first_pass_job_id=first_pass_job_id,
                    notes=None,
                )
        return run

    def mark_phase1_gated(
        self,
        user_id: str,
        *,
        first_pass_job_id: str | None = None,
        first_pass_snapshot_id: str | None = None,
        job_status: str | None = None,
    ) -> ResearchRun:
        """Compatibility shim — reconcile ignores client-asserted status."""
        del first_pass_job_id, first_pass_snapshot_id, job_status
        run = self.reconcile(user_id)
        if run is None:
            raise KeyError("research_run_not_found")
        return run

    def attach_candidate_set(
        self,
        user_id: str,
        *,
        candidate_set_id: str,
        candidate_set_version: int,
        first_pass_snapshot_id: str | None = None,
    ) -> ResearchRun:
        with self._lock:
            run = self._require_locked(user_id)
            self._reconcile_locked(run)
            if run.phase != "awaiting_analyst_candidate_selection":
                raise ResearchRunGateError(
                    "candidate_attach_requires_awaiting_analyst_candidate_selection"
                )
            if (
                first_pass_snapshot_id
                and run.first_pass_snapshot_id
                and str(first_pass_snapshot_id) != str(run.first_pass_snapshot_id)
            ):
                raise ResearchRunGateError("candidate_set_first_pass_snapshot_mismatch")
            run.candidate_set_id = candidate_set_id
            run.candidate_set_version = candidate_set_version
            run.gates["candidateSelection"] = "approved"
            run.append_note(
                f"Candidate set {candidate_set_id} v{candidate_set_version} approved; "
                "Phase-2 technical may start manually."
            )
            run.updated_at = datetime.now(UTC).isoformat()
            return self._persist_locked(run)

    def start_phase2(
        self,
        user_id: str,
        *,
        technical_scan_id: str,
        notes: list[str] | None = None,
    ) -> ResearchRun:
        with self._lock:
            run = self._require_locked(user_id)
            self._reconcile_locked(run)
            if run.gates.get("candidateSelection") != "approved":
                raise ResearchRunGateError("candidate_selection_gate_not_approved")
            # Idempotent: already in phase2 with same (or any) technical scan.
            if run.phase == "phase2_technical" and run.technical_scan_id:
                return run
            if run.phase == "awaiting_analyst_report_decision" and run.technical_scan_id:
                return run
            run.phase = "phase2_technical"
            run.status = "running"
            run.technical_scan_id = technical_scan_id
            run.dimensions["monthlyTechnical"] = "in_progress"
            run.dimensions["dailyTechnical"] = "pending_monthly_gate"
            for note in list(notes or []) + [
                "Phase-2 monthly-first technical started from approved candidates only.",
                "Stops for analyst report decision; no Notion write.",
            ]:
                run.append_note(note)
            run.updated_at = datetime.now(UTC).isoformat()
            return self._persist_locked(run)

    def mark_phase2_gated(
        self,
        user_id: str,
        *,
        scan_status: str | None = None,
    ) -> ResearchRun:
        """Compatibility shim — reconcile ignores client-asserted status."""
        del scan_status
        run = self.reconcile(user_id)
        if run is None:
            raise KeyError("research_run_not_found")
        return run

    def claim_first_pass_job_slot(
        self,
        user_id: str,
        *,
        claim_token: str,
        lease_seconds: int = RESEARCH_RUN_JOB_CLAIM_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """DB-backed claim for creating the authoritative first-pass job."""
        if self._test_memory is not None:
            with self._lock:
                return self._claim_job_slot_memory(
                    user_id, claim_token=claim_token, lease_seconds=lease_seconds
                )
        try:
            return self._repo().claim_research_run_job_slot(
                user_id, claim_token=claim_token, lease_seconds=lease_seconds
            )
        except AttributeError as exc:
            raise ResearchRunStoreUnavailable(
                "research_run_job_claim_unsupported"
            ) from exc

    def record_pending_first_pass_job(
        self,
        user_id: str,
        *,
        claim_token: str,
        job_id: str,
    ) -> dict[str, Any]:
        if self._test_memory is not None:
            with self._lock:
                run = self._require_locked(user_id)
                if run.first_pass_job_id:
                    return {
                        "outcome": "already_attached",
                        "run": run.as_persist_payload(),
                        "first_pass_job_id": run.first_pass_job_id,
                        "orphan_job_id": job_id
                        if run.first_pass_job_id != job_id
                        else None,
                    }
                if run.job_claim_token != claim_token:
                    return {
                        "outcome": "claim_lost",
                        "run": run.as_persist_payload(),
                        "orphan_job_id": job_id,
                    }
                run.pending_first_pass_job_id = job_id
                run.updated_at = datetime.now(UTC).isoformat()
                saved = self._persist_locked(run)
                return {
                    "outcome": "recorded",
                    "run": saved.as_persist_payload(),
                    "pending_first_pass_job_id": job_id,
                }
        return self._repo().record_pending_first_pass_job(
            user_id, claim_token=claim_token, job_id=job_id
        )

    def attach_first_pass_job_under_claim(
        self,
        user_id: str,
        *,
        claim_token: str,
        job_id: str,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        if self._test_memory is not None:
            with self._lock:
                run = self._require_locked(user_id)
                if run.first_pass_job_id:
                    return {
                        "outcome": "already_attached",
                        "run": run.as_persist_payload(),
                        "first_pass_job_id": run.first_pass_job_id,
                        "orphan_job_id": job_id
                        if run.first_pass_job_id != job_id
                        else None,
                    }
                if run.job_claim_token != claim_token:
                    return {
                        "outcome": "claim_lost",
                        "run": run.as_persist_payload(),
                        "orphan_job_id": job_id,
                    }
                run.first_pass_job_id = job_id
                run.pending_first_pass_job_id = None
                run.job_claim_token = None
                run.job_claim_expires_at = None
                run.status = "running"
                run.phase = "phase1_first_pass"
                run.dimensions["fundamentals"] = "in_progress"
                for note in notes or []:
                    run.append_note(note)
                run.updated_at = datetime.now(UTC).isoformat()
                saved = self._persist_locked(run)
                return {
                    "outcome": "attached",
                    "run": saved.as_persist_payload(),
                    "first_pass_job_id": job_id,
                }
        return self._repo().attach_first_pass_job_atomic(
            user_id, claim_token=claim_token, job_id=job_id, notes=notes
        )

    def mark_orphaned_first_pass_job(
        self,
        job_id: str,
        *,
        user_id: str,
        authoritative_job_id: str | None,
        reason: str,
    ) -> None:
        if self._test_memory is not None:
            # Unit-test path: annotate via optional repository hook.
            repo = self._repository
            marker = getattr(repo, "mark_first_pass_job_orphaned", None) if repo else None
            if callable(marker):
                marker(
                    job_id,
                    user_id=user_id,
                    authoritative_job_id=authoritative_job_id,
                    reason=reason,
                )
            return
        self._repo().mark_first_pass_job_orphaned(
            job_id,
            user_id=user_id,
            authoritative_job_id=authoritative_job_id,
            reason=reason,
        )

    def claim_phase2_scan_slot(
        self,
        user_id: str,
        *,
        claim_token: str,
        lease_seconds: int = RESEARCH_RUN_PHASE2_CLAIM_LEASE_SECONDS,
    ) -> dict[str, Any]:
        if self._test_memory is not None:
            with self._lock:
                return self._claim_phase2_slot_memory(
                    user_id, claim_token=claim_token, lease_seconds=lease_seconds
                )
        return self._repo().claim_research_run_phase2_slot(
            user_id, claim_token=claim_token, lease_seconds=lease_seconds
        )

    def record_pending_technical_scan(
        self,
        user_id: str,
        *,
        claim_token: str,
        scan_id: str,
    ) -> dict[str, Any]:
        if self._test_memory is not None:
            with self._lock:
                run = self._require_locked(user_id)
                if run.technical_scan_id:
                    return {
                        "outcome": "already_attached",
                        "run": run.as_persist_payload(),
                        "technical_scan_id": run.technical_scan_id,
                    }
                if run.phase2_claim_token != claim_token:
                    return {"outcome": "claim_lost", "run": run.as_persist_payload()}
                run.pending_technical_scan_id = scan_id
                run.updated_at = datetime.now(UTC).isoformat()
                saved = self._persist_locked(run)
                return {
                    "outcome": "recorded",
                    "run": saved.as_persist_payload(),
                    "pending_technical_scan_id": scan_id,
                }
        return self._repo().record_pending_technical_scan(
            user_id, claim_token=claim_token, scan_id=scan_id
        )

    def attach_technical_scan_under_claim(
        self,
        user_id: str,
        *,
        claim_token: str,
        scan_id: str,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        if self._test_memory is not None:
            with self._lock:
                run = self._require_locked(user_id)
                if run.technical_scan_id:
                    return {
                        "outcome": "already_attached",
                        "run": run.as_persist_payload(),
                        "technical_scan_id": run.technical_scan_id,
                    }
                if run.phase2_claim_token != claim_token:
                    return {"outcome": "claim_lost", "run": run.as_persist_payload()}
                if run.gates.get("candidateSelection") != "approved":
                    return {
                        "outcome": "gate_not_approved",
                        "run": run.as_persist_payload(),
                    }
                run.technical_scan_id = scan_id
                run.pending_technical_scan_id = None
                run.phase2_claim_token = None
                run.phase2_claim_expires_at = None
                run.phase = "phase2_technical"
                run.status = "running"
                run.dimensions["monthlyTechnical"] = "in_progress"
                run.dimensions["dailyTechnical"] = "pending_monthly_gate"
                for note in list(notes or []) + [
                    "Phase-2 monthly-first technical started from approved candidates only.",
                    "Stops for analyst report decision; no Notion write.",
                ]:
                    run.append_note(note)
                run.updated_at = datetime.now(UTC).isoformat()
                saved = self._persist_locked(run)
                return {
                    "outcome": "attached",
                    "run": saved.as_persist_payload(),
                    "technical_scan_id": scan_id,
                }
        return self._repo().attach_technical_scan_atomic(
            user_id, claim_token=claim_token, scan_id=scan_id, notes=notes
        )

    def clear_for_tests(self) -> None:
        """Enable isolated in-memory backend for unit tests (never for production)."""
        with self._lock:
            self._test_memory = {}

    def disable_test_memory(self) -> None:
        with self._lock:
            self._test_memory = None

    def _repo(self) -> _ResearchRunRepository:
        if self._repository is not None:
            repo = self._repository
        else:
            from app.repositories.tw_research_repository import tw_research_repository

            repo = tw_research_repository
        if not repo.is_available():
            raise ResearchRunStoreUnavailable("research_run_store_unavailable")
        return repo

    def _load_locked(self, user_id: str) -> ResearchRun | None:
        if self._test_memory is not None:
            return self._test_memory.get(user_id)
        row = self._repo().get_research_run(user_id)
        if row is None:
            return None
        return ResearchRun.from_persist_payload(row)

    def _require_locked(self, user_id: str) -> ResearchRun:
        run = self._load_locked(user_id)
        if run is None:
            raise KeyError("research_run_not_found")
        return run

    def _persist_locked(self, run: ResearchRun) -> ResearchRun:
        run.notes = list(run.notes)[-_NOTES_CAP:]
        if self._test_memory is not None:
            # In-memory OCC: reject stale revision writes from another store view.
            current = self._test_memory.get(run.user_id)
            if current is not None and current.research_run_id == run.research_run_id:
                if int(current.revision) != int(run.revision):
                    raise ResearchRunConflict()
            run.revision = int(run.revision) + 1
            self._test_memory[run.user_id] = run
            return run
        try:
            saved = self._repo().upsert_research_run(run.as_persist_payload())
        except ResearchRunConflict:
            raise
        return ResearchRun.from_persist_payload(saved)

    def _is_active_nonterminal(self, run: ResearchRun) -> bool:
        if run.phase not in _ACTIVE_PHASES:
            return False
        if run.status in {"failed", "budget_exhausted", "evidence_ready", "gated"}:
            # gated with active phase shouldn't happen after reconcile, but treat as terminal-ish
            if run.phase in _TERMINAL_OR_GATED_PHASES:
                return False
            if run.status in {"failed", "budget_exhausted"}:
                return False
        if run.phase == "phase1_first_pass":
            return run.status in {
                "queued",
                "running",
                WAITING_FOR_NEXT_WINDOW,
            } or (run.first_pass_job_id is not None and run.status not in _JOB_TERMINAL)
        if run.phase == "phase2_technical":
            return run.status in {
                "queued",
                "running",
                WAITING_FOR_NEXT_WINDOW,
            } or (
                run.technical_scan_id is not None
                and run.status not in _SCAN_TERMINAL | {"gated", "evidence_ready"}
            )
        return False

    def _reconcile_locked(self, run: ResearchRun) -> bool:
        before = (
            run.phase,
            run.status,
            run.first_pass_snapshot_id,
            run.technical_scan_id,
            tuple(run.notes[-3:]),
        )
        # Prefer phase2 when a technical scan is attached.
        if run.technical_scan_id:
            self._reconcile_from_scan_locked(run)
        elif run.first_pass_job_id:
            self._reconcile_from_job_locked(run)
        after = (
            run.phase,
            run.status,
            run.first_pass_snapshot_id,
            run.technical_scan_id,
            tuple(run.notes[-3:]),
        )
        if before != after:
            run.updated_at = datetime.now(UTC).isoformat()
            return True
        return False

    def _reconcile_from_job_locked(self, run: ResearchRun) -> None:
        job = self._load_first_pass_job(run.user_id, run.first_pass_job_id)
        if job is None:
            return
        status = str(job.get("status") or job.get("job_status") or "")
        snapshot_id = (
            job.get("active_snapshot_id")
            or job.get("activeSnapshotId")
            or job.get("first_pass_snapshot_id")
        )
        if status in {"running", "pending", "queued"}:
            run.phase = "phase1_first_pass"
            run.status = "running"
            run.dimensions["fundamentals"] = "in_progress"
            return
        if status == "deferred" or map_recoverable_status(status) == WAITING_FOR_NEXT_WINDOW:
            if status != "budget_exhausted":
                run.phase = "phase1_first_pass"
                run.status = WAITING_FOR_NEXT_WINDOW
                run.dimensions["fundamentals"] = WAITING_FOR_NEXT_WINDOW
                return
        if status == "budget_exhausted":
            run.phase = "phase1_first_pass"
            run.status = "budget_exhausted"
            run.dimensions["fundamentals"] = "budget_exhausted"
            return
        if status == "failed":
            run.phase = "phase1_first_pass"
            run.status = "failed"
            run.dimensions["fundamentals"] = "failed"
            return
        if status in {"completed", "partial"} and snapshot_id:
            run.phase = "awaiting_analyst_candidate_selection"
            run.status = "gated"
            run.first_pass_snapshot_id = str(snapshot_id)
            run.dimensions["fundamentals"] = (
                "partial" if status == "partial" else "complete_or_partial"
            )
            run.gates["candidateSelection"] = "awaiting_analyst"
            return
        if status in {"completed", "partial"}:
            run.phase = "phase1_first_pass"
            run.status = "running"
            run.dimensions["fundamentals"] = "in_progress"

    def _reconcile_from_scan_locked(self, run: ResearchRun) -> None:
        scan = self._load_scan(run.user_id, run.technical_scan_id)
        if scan is None:
            return
        status = str(scan.get("status") or "")
        if status in {"queued", "running"}:
            run.phase = "phase2_technical"
            run.status = "running"
            run.dimensions["monthlyTechnical"] = "in_progress"
            return
        mapped = map_recoverable_status(status)
        if status == "deferred" or mapped == WAITING_FOR_NEXT_WINDOW:
            if status != "budget_exhausted" and status != "failed":
                # Recoverable partial pauses → waiting; hard budget stays exhausted.
                if status == "partial":
                    # Spec: partial(recoverable) → waiting_for_next_window
                    run.phase = "phase2_technical"
                    run.status = WAITING_FOR_NEXT_WINDOW
                    run.dimensions["monthlyTechnical"] = WAITING_FOR_NEXT_WINDOW
                    return
                run.phase = "phase2_technical"
                run.status = WAITING_FOR_NEXT_WINDOW
                run.dimensions["monthlyTechnical"] = WAITING_FOR_NEXT_WINDOW
                return
        if status == "budget_exhausted":
            run.phase = "phase2_technical"
            run.status = "budget_exhausted"
            run.dimensions["monthlyTechnical"] = "budget_exhausted"
            return
        if status == "failed":
            run.phase = "phase2_technical"
            run.status = "failed"
            run.dimensions["monthlyTechnical"] = "failed"
            return
        if status in {"completed_processing", "verified_ready"}:
            run.phase = "awaiting_analyst_report_decision"
            run.status = "evidence_ready" if status == "verified_ready" else "gated"
            run.dimensions["monthlyTechnical"] = "complete_or_partial"
            run.dimensions["dailyTechnical"] = "complete_or_not_run_by_design"
            run.gates["reportDecision"] = "awaiting_analyst"
            return

    def _claim_job_slot_memory(
        self,
        user_id: str,
        *,
        claim_token: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        run = self._require_locked(user_id)
        if run.first_pass_job_id:
            return {
                "outcome": "already_attached",
                "run": run.as_persist_payload(),
                "first_pass_job_id": run.first_pass_job_id,
            }
        now = datetime.now(UTC)
        expires = None
        if run.job_claim_expires_at:
            try:
                expires = datetime.fromisoformat(
                    str(run.job_claim_expires_at).replace("Z", "+00:00")
                )
            except ValueError:
                expires = None
        held_by_other = (
            run.job_claim_token
            and run.job_claim_token != claim_token
            and expires is not None
            and expires > now
        )
        if held_by_other:
            return {
                "outcome": "claim_held",
                "run": run.as_persist_payload(),
                "first_pass_job_id": None,
            }
        from datetime import timedelta

        run.job_claim_token = claim_token
        run.job_claim_expires_at = (now + timedelta(seconds=int(lease_seconds))).isoformat()
        run.updated_at = now.isoformat()
        saved = self._persist_locked(run)
        return {
            "outcome": "claimed",
            "run": saved.as_persist_payload(),
            "pending_first_pass_job_id": saved.pending_first_pass_job_id,
            "first_pass_job_id": None,
        }

    def _claim_phase2_slot_memory(
        self,
        user_id: str,
        *,
        claim_token: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        run = self._require_locked(user_id)
        if run.gates.get("candidateSelection") != "approved":
            return {"outcome": "gate_not_approved", "run": run.as_persist_payload()}
        if run.technical_scan_id:
            return {
                "outcome": "already_attached",
                "run": run.as_persist_payload(),
                "technical_scan_id": run.technical_scan_id,
            }
        now = datetime.now(UTC)
        expires = None
        if run.phase2_claim_expires_at:
            try:
                expires = datetime.fromisoformat(
                    str(run.phase2_claim_expires_at).replace("Z", "+00:00")
                )
            except ValueError:
                expires = None
        held_by_other = (
            run.phase2_claim_token
            and run.phase2_claim_token != claim_token
            and expires is not None
            and expires > now
        )
        if held_by_other:
            return {"outcome": "claim_held", "run": run.as_persist_payload()}
        from datetime import timedelta

        run.phase2_claim_token = claim_token
        run.phase2_claim_expires_at = (
            now + timedelta(seconds=int(lease_seconds))
        ).isoformat()
        run.updated_at = now.isoformat()
        saved = self._persist_locked(run)
        return {
            "outcome": "claimed",
            "run": saved.as_persist_payload(),
            "pending_technical_scan_id": saved.pending_technical_scan_id,
        }

    def _load_first_pass_job(
        self, user_id: str, job_id: str | None
    ) -> dict[str, Any] | None:
        if not job_id:
            return None
        if self._test_memory is not None:
            # Unit tests may inject jobs via repository override.
            repo = self._repository
            getter = getattr(repo, "get_first_pass_job", None) if repo else None
            if callable(getter):
                return getter(job_id, user_id=user_id)
            return None
        from app.repositories.tw_research_repository import tw_research_repository

        return tw_research_repository.get_first_pass_job(job_id, user_id=user_id)

    def _load_scan(self, user_id: str, scan_id: str | None) -> dict[str, Any] | None:
        if not scan_id:
            return None
        if self._test_memory is not None:
            repo = self._repository
            getter = getattr(repo, "get_scan", None) if repo else None
            if callable(getter):
                row = getter(scan_id)
                if row is None:
                    return None
                if row.get("user_id") not in {None, user_id}:
                    return None
                return row
            return None
        from app.repositories.tw_research_repository import tw_research_repository

        row = tw_research_repository.get_scan(scan_id)
        if row is None:
            return None
        if row.get("user_id") not in {None, user_id}:
            return None
        return row


research_run_store = ResearchRunStore()
