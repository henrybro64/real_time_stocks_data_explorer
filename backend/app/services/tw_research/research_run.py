"""Manual full-research orchestration with explicit analyst gates (#341).

Sequence:
1. User starts Phase-1 first-pass → stop at ``awaiting_analyst_candidate_selection``
2. After candidate set is saved, user starts Phase-2 monthly-first technical
   → stop at ``awaiting_analyst_report_decision``
3. External Agent may publish only after ChatGPT approves a report (out of repo)

PostgreSQL is the authoritative ledger (fail closed when unavailable).
Never auto-publishes Notion. Never enables CronJob. Recoverable pauses map to
``waiting_for_next_window``; hard-cap exhaustion stays ``budget_exhausted``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal, Protocol
from uuid import uuid4

from app.services.tw_research.constants import (
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


class ResearchRunStoreUnavailable(Exception):
    """Raised when the durable research-run store is unavailable (fail closed)."""


class ResearchRunGateError(Exception):
    """Raised when a manual gate is not satisfied."""


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
            "notes": list(self.notes),
            "schedule_enabled": bool(self.schedule_enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
            notes=list(payload.get("notes") or []),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            schedule_enabled=bool(payload.get("schedule_enabled")),
        )


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

    def start_phase1(
        self,
        user_id: str,
        *,
        trading_day: str | None = None,
        first_pass_job_id: str | None = None,
        notes: list[str] | None = None,
    ) -> ResearchRun:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            run = ResearchRun(
                research_run_id=f"rr_{uuid4().hex[:20]}",
                user_id=user_id,
                phase="phase1_first_pass",
                status="running",
                trading_day=trading_day,
                first_pass_job_id=first_pass_job_id,
                gates={
                    "candidateSelection": "pending",
                    "reportDecision": "blocked_until_phase2",
                    "notionPublication": "external_only_never_auto",
                },
                dimensions={
                    "fundamentals": "in_progress",
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
            )
            return self._persist_locked(run)

    def mark_phase1_gated(
        self,
        user_id: str,
        *,
        first_pass_job_id: str | None = None,
        first_pass_snapshot_id: str | None = None,
        job_status: str | None = None,
    ) -> ResearchRun:
        with self._lock:
            run = self._require_locked(user_id)
            if first_pass_job_id:
                run.first_pass_job_id = first_pass_job_id
            if first_pass_snapshot_id:
                run.first_pass_snapshot_id = first_pass_snapshot_id
            mapped = map_recoverable_status(job_status or "gated")
            if mapped == WAITING_FOR_NEXT_WINDOW:
                run.status = WAITING_FOR_NEXT_WINDOW
                run.phase = "phase1_first_pass"
                run.dimensions["fundamentals"] = WAITING_FOR_NEXT_WINDOW
                run.notes.append(
                    "Recoverable pause mapped to waiting_for_next_window; "
                    "hard caps remain enforced."
                )
            elif job_status == "budget_exhausted":
                run.status = "budget_exhausted"
                run.dimensions["fundamentals"] = "budget_exhausted"
            else:
                # completed / partial / gated → analyst gate (partial still reviewable)
                run.phase = "awaiting_analyst_candidate_selection"
                run.status = "gated"
                run.dimensions["fundamentals"] = (
                    "partial" if job_status == "partial" else "complete_or_partial"
                )
                run.gates["candidateSelection"] = "awaiting_analyst"
            run.updated_at = datetime.now(UTC).isoformat()
            return self._persist_locked(run)

    def attach_candidate_set(
        self,
        user_id: str,
        *,
        candidate_set_id: str,
        candidate_set_version: int,
    ) -> ResearchRun:
        with self._lock:
            run = self._require_locked(user_id)
            run.candidate_set_id = candidate_set_id
            run.candidate_set_version = candidate_set_version
            run.gates["candidateSelection"] = "approved"
            run.notes.append(
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
            if run.gates.get("candidateSelection") != "approved":
                raise ResearchRunGateError("candidate_selection_gate_not_approved")
            run.phase = "phase2_technical"
            run.status = "running"
            run.technical_scan_id = technical_scan_id
            run.dimensions["monthlyTechnical"] = "in_progress"
            run.dimensions["dailyTechnical"] = "pending_monthly_gate"
            run.notes.extend(
                list(notes or [])
                + [
                    "Phase-2 monthly-first technical started from approved candidates only.",
                    "Stops for analyst report decision; no Notion write.",
                ]
            )
            run.updated_at = datetime.now(UTC).isoformat()
            return self._persist_locked(run)

    def mark_phase2_gated(
        self,
        user_id: str,
        *,
        scan_status: str | None = None,
    ) -> ResearchRun:
        with self._lock:
            run = self._require_locked(user_id)
            mapped = map_recoverable_status(scan_status or "gated")
            if mapped == WAITING_FOR_NEXT_WINDOW:
                run.status = WAITING_FOR_NEXT_WINDOW
                run.dimensions["monthlyTechnical"] = WAITING_FOR_NEXT_WINDOW
            elif scan_status == "budget_exhausted":
                run.status = "budget_exhausted"
                run.dimensions["monthlyTechnical"] = "budget_exhausted"
            else:
                # completed / verified_ready / partial / gated → report decision gate
                run.phase = "awaiting_analyst_report_decision"
                run.status = "gated" if scan_status != "verified_ready" else "evidence_ready"
                run.dimensions["monthlyTechnical"] = (
                    "partial" if scan_status == "partial" else "complete_or_partial"
                )
                run.dimensions["dailyTechnical"] = "complete_or_not_run_by_design"
                run.gates["reportDecision"] = "awaiting_analyst"
                run.notes.append(
                    "Evidence ready for ChatGPT report judgement; "
                    "Stock Ticker does not author or publish the Notion report."
                )
            run.updated_at = datetime.now(UTC).isoformat()
            return self._persist_locked(run)

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

    def _require_locked(self, user_id: str) -> ResearchRun:
        if self._test_memory is not None:
            run = self._test_memory.get(user_id)
            if run is None:
                raise KeyError("research_run_not_found")
            return run
        row = self._repo().get_research_run(user_id)
        if row is None:
            raise KeyError("research_run_not_found")
        return ResearchRun.from_persist_payload(row)

    def _persist_locked(self, run: ResearchRun) -> ResearchRun:
        if self._test_memory is not None:
            self._test_memory[run.user_id] = run
            return run
        saved = self._repo().upsert_research_run(run.as_persist_payload())
        return ResearchRun.from_persist_payload(saved)


research_run_store = ResearchRunStore()
