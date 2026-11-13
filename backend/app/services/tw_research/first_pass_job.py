"""Resumable full-universe first-pass collection jobs (#335).

Reuses the scan-style lease/cursor pattern: create a job with a frozen
deduplicated universe, claim bounded batches, persist per-symbol evidence,
then finalize an immutable snapshot for the analyst.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections import Counter
from typing import Any, Callable

from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.constants import FIRST_PASS_VERSION
from app.services.tw_research.first_pass import (
    DEFAULT_SYMBOL_DEADLINE_SECONDS,
    FirstPassBatchResult,
    FirstPassSymbolRecord,
    classify_field_reason,
    collect_first_pass_batch,
    dedupe_stock_ids,
    dominant_transient_field_error,
    field_reason_is_retryable,
    is_infrastructure_failure,
    retry_after_seconds_from_reason,
)
from app.services.tw_research.first_pass_durable import FirstPassDurableUnavailable
from app.services.tw_research.fundamentals_watchlist import (
    ResearchMembershipUnavailable,
    research_membership_store,
)
from app.services.tw_research.universe import UniverseSelector, resolve_universe


logger = logging.getLogger(__name__)

# Heartbeat renews the batch lease independently of per-symbol progress so one
# slow symbol (e.g. TDCC Friday loop) cannot outlive BATCH_LEASE_SECONDS.
DEFAULT_LEASE_HEARTBEAT_SECONDS = 60.0
_HEARTBEAT_SLEEP_CHUNK_SECONDS = 0.25
_HEARTBEAT_JOIN_TIMEOUT_SECONDS = 5.0


class FirstPassJobError(RuntimeError):
    def __init__(self, message: str, *, operation: str = "first_pass_job") -> None:
        super().__init__(message)
        self.operation = operation


def _universe_digest(stock_ids: list[str], universe_version: str) -> str:
    raw = json.dumps(
        {"universeVersion": universe_version, "stockIds": stock_ids},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Transient collection failures may be retried with durable backoff (#335).
_FIRST_PASS_MAX_ATTEMPTS = 4
_FIRST_PASS_RETRY_BASE_SECONDS = 30
_FIRST_PASS_RETRY_CAP_SECONDS = 900
_PERMANENT_SYMBOL_ERRORS = frozenset(
    {
        "not_in_catalog",
        "unsupported",
        "permanently_unavailable",
    }
)


def _classify_symbol_failure(error: str | None) -> str:
    text = str(error or "").strip().lower()
    if not text:
        return "unknown"
    if text in _PERMANENT_SYMBOL_ERRORS or text.startswith("unsupported"):
        return "permanent"
    field_class = classify_field_reason(error)
    if field_class == "programming_error":
        return "programming_error"
    if field_class == "permanent":
        return "permanent"
    if field_class in {"rate_limited", "timeout", "transient", "provider_validation", "stale"}:
        return field_class
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "network" in text or "connection" in text or "temporarily" in text:
        return "transient"
    return "transient"


def _programming_error_field_key(record: FirstPassSymbolRecord) -> str | None:
    for key, field in record.fields.items():
        if field.status in {"unavailable", "partial"} and classify_field_reason(
            field.reason
        ) == "programming_error":
            return key
    return None


def _symbol_has_retryable_field_failure(record: FirstPassSymbolRecord) -> bool:
    if dominant_transient_field_error(record.fields):
        return True
    for field in record.fields.values():
        if field.status in {"unavailable", "partial"} and field_reason_is_retryable(
            field.reason
        ):
            return True
    return False


def _symbol_status(record: FirstPassSymbolRecord) -> str:
    if record.error:
        failure_class = _classify_symbol_failure(record.error)
        if failure_class == "permanent":
            # Keep usable incomplete evidence rather than endless retries.
            return "partial"
        if failure_class == "programming_error":
            # Symbol-level programming errors never auto-retry.
            return "failed"
        return "failed"
    if _symbol_has_retryable_field_failure(record):
        # Field-level transient faults (valuation timeout, HTTP 429, etc.) must
        # enter the durable retry path even when catalog identity succeeded.
        return "failed"
    if _programming_error_field_key(record) is not None:
        # Field-level programming_error keeps peer evidence as reviewable partial.
        return "partial"
    completeness = record.completeness or {}
    if completeness.get("fieldsUnavailable", 0) and not completeness.get("fieldsOk", 0):
        return "partial"
    pe = record.fields.get("official_pe")
    if pe is not None and pe.status == "unavailable":
        # Permanent PE gaps (adapter/missing publication) stay reviewable partials.
        if field_reason_is_retryable(pe.reason):
            return "failed"
        return "partial"
    if record.name_status == "unavailable":
        return "partial"
    return "ok"


def _merge_preserved_ok_fields(
    previous_fields: dict[str, Any] | None,
    current_fields: dict[str, Any],
) -> dict[str, Any]:
    """Preserve already-valid evidence across retries; never fabricate values."""
    merged = dict(current_fields)
    for key, prior in dict(previous_fields or {}).items():
        if not isinstance(prior, dict):
            continue
        if str(prior.get("status") or "") != "ok":
            continue
        current = merged.get(key)
        if not isinstance(current, dict):
            merged[key] = prior
            continue
        current_status = str(current.get("status") or "")
        if current_status == "ok":
            continue
        if field_reason_is_retryable(current.get("reason")):
            # Keep prior OK rather than replacing with a transient blip.
            merged[key] = prior
    return merged


def _enrich_symbol_payload(
    record: FirstPassSymbolRecord,
    *,
    status: str,
    previous_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach durable retry metadata without fabricating financial fields."""
    payload = record.as_dict()
    prior = dict(previous_payload or {})
    payload["fields"] = _merge_preserved_ok_fields(
        prior.get("fields") if isinstance(prior.get("fields"), dict) else None,
        dict(payload.get("fields") or {}),
    )
    # Recompute dominant transient after merge (may clear retry need).
    transient = dominant_transient_field_error(payload["fields"])
    if status == "failed" and not payload.get("error"):
        payload["error"] = transient or record.error or "transient_field_failure"
    prior_attempts = int(prior.get("attemptCount") or 0)
    if status == "failed":
        attempts = prior_attempts + 1
        failure_class = _classify_symbol_failure(
            str(payload.get("error") or record.error or transient or "")
        )
        delay = min(
            _FIRST_PASS_RETRY_CAP_SECONDS,
            _FIRST_PASS_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)),
        )
        hinted = retry_after_seconds_from_reason(str(payload.get("error") or ""))
        if hinted is not None:
            delay = max(delay, hinted)
        # Deterministic jitter from stock id (stable across workers).
        jitter = int(hashlib.sha256(record.stock_id.encode("utf-8")).hexdigest()[:4], 16) % 11
        exhausted = (
            attempts >= _FIRST_PASS_MAX_ATTEMPTS
            or failure_class == "permanent"
            or failure_class == "programming_error"
        )
        from datetime import UTC, datetime, timedelta

        payload["attemptCount"] = attempts
        payload["failureClass"] = failure_class
        payload["retryExhausted"] = exhausted
        # programming_error never schedules next_retry_at.
        payload["nextRetryAt"] = None if exhausted else (
            datetime.now(UTC) + timedelta(seconds=delay + jitter)
        ).isoformat()
        payload["preservedOkFields"] = sorted(
            key
            for key, value in payload["fields"].items()
            if isinstance(value, dict) and value.get("status") == "ok"
        )
    else:
        payload["attemptCount"] = max(prior_attempts, 1)
        prog_key = _programming_error_field_key(record)
        if status == "partial" and prog_key is not None:
            payload["failureClass"] = "programming_error"
        else:
            payload["failureClass"] = None
        payload["retryExhausted"] = False
        payload["nextRetryAt"] = None
        payload.pop("preservedOkFields", None)
    return payload


def _lease_heartbeat_loop(
    *,
    renew: Callable[[], bool],
    interval_seconds: float,
    stop_event: threading.Event,
    lease_lost_event: threading.Event,
    sleep: Callable[[float], None] | None,
    job_id: str,
) -> None:
    """Renew the batch lease every ``interval_seconds`` until stopped.

    Waits in small chunks so ``stop_event`` ends the thread promptly. A False
    renew means the lease is gone (expired / generation changed): flag it and
    exit so the collector stops and nothing is persisted. Infra errors are
    logged and retried on the next tick (per-symbol renew still fails closed).
    """
    while not stop_event.is_set():
        remaining = interval_seconds
        while remaining > 0 and not stop_event.is_set():
            chunk = min(remaining, _HEARTBEAT_SLEEP_CHUNK_SECONDS)
            if sleep is None:
                stop_event.wait(chunk)
            else:
                sleep(chunk)
            remaining -= chunk
        if stop_event.is_set():
            return
        try:
            renewed = bool(renew())
        except Exception as exc:  # noqa: BLE001 — never kill the heartbeat on one failure
            logger.warning(
                "first_pass_lease_heartbeat_error job_id=%s exc_class=%s",
                job_id,
                type(exc).__name__,
            )
            continue
        if not renewed:
            lease_lost_event.set()
            return


class FirstPassJobService:
    def __init__(self, *, repository: Any | None = None) -> None:
        self._repo = repository or tw_research_repository

    def create_job(
        self,
        *,
        user_id: str,
        stock_ids: list[str] | None = None,
        use_membership: bool = True,
        selector: UniverseSelector | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._repo.is_available():
            raise FirstPassJobError(
                "first_pass_job_store_unavailable",
                operation="create_first_pass_job",
            )

        # Idempotent resume: durable association key already points at a job row.
        if job_id:
            existing = self._repo.get_first_pass_job(job_id, user_id=user_id)
            if existing is not None:
                return existing

        membership_version = 0
        notes: list[str] = []
        if stock_ids:
            ids = dedupe_stock_ids(stock_ids)
            universe_version = f"explicit:{len(ids)}"
            selector_payload = {"mode": "stock_ids", "stockIds": ids}
        elif use_membership:
            try:
                membership = research_membership_store.get(user_id)
            except ResearchMembershipUnavailable as exc:
                raise FirstPassJobError(str(exc), operation="create_first_pass_job") from exc
            ids = list(membership.resolved_stock_ids)
            universe_version = membership.universe_version
            membership_version = membership.version
            selector_payload = {
                "mode": "membership",
                "themeKeys": list(membership.theme_keys),
                "extraStockIds": list(membership.extra_stock_ids),
            }
            notes.extend(membership.notes)
        else:
            resolved = resolve_universe(selector or UniverseSelector(mode="starter_preset"))
            ids = list(resolved.stock_ids)
            universe_version = resolved.universe_version
            selector_payload = {
                "mode": resolved.mode,
                "themeKeys": list(resolved.theme_keys_applied),
                "stockIds": list(resolved.stock_ids),
            }
            notes.extend(resolved.notes)

        if not ids:
            raise FirstPassJobError("empty_universe", operation="create_first_pass_job")

        resolved_job_id = job_id or f"fpj_{uuid.uuid4().hex[:24]}"
        digest = _universe_digest(ids, universe_version)
        return self._repo.create_first_pass_job(
            {
                "job_id": resolved_job_id,
                "user_id": user_id,
                "universe_version": universe_version,
                "universe_digest": digest,
                "membership_version": membership_version,
                "first_pass_version": FIRST_PASS_VERSION,
                "stock_ids": ids,
                "selector": selector_payload,
                "status": "pending",
                "quality_status": "incomplete",
                "notes": notes
                + [
                    "Frozen universe for this job; later membership edits do not mutate it.",
                    "Processing completion is independent of fundamental completeness.",
                ],
            }
        )

    def get_job(self, job_id: str, *, user_id: str) -> dict[str, Any]:
        if not self._repo.is_available():
            raise FirstPassJobError("first_pass_job_store_unavailable")
        job = self._repo.get_first_pass_job(job_id, user_id=user_id)
        if job is None:
            raise KeyError("first_pass_job_not_found")
        return job

    def list_jobs(self, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self._repo.is_available():
            raise FirstPassJobError("first_pass_job_store_unavailable")
        return self._repo.list_first_pass_jobs(user_id, limit=max(1, min(limit, 50)))

    def progress(self, job_id: str, *, user_id: str) -> dict[str, Any]:
        job = self.get_job(job_id, user_id=user_id)
        counts = self._repo.count_first_pass_symbols(job_id)
        universe_size = int(job["universe_size"])
        processed = int(counts["total"])
        pending = max(0, universe_size - processed)
        return {
            "jobId": job["job_id"],
            "status": job["status"],
            "qualityStatus": job["quality_status"],
            "universeSize": universe_size,
            "cursor": job["cursor"],
            "processedCount": processed,
            "pendingCount": pending,
            "okCount": counts["ok"],
            "partialCount": counts["partial"],
            "failedCount": counts["failed"],
            "deferredCount": counts["deferred"],
            "activeSnapshotId": job["active_snapshot_id"],
            "universeDigest": job["universe_digest"],
            "notes": [
                "status=completed means all symbols were attempted, not that every field is filled.",
                "qualityStatus remains incomplete until an immutable snapshot is finalized.",
            ],
        }

    def run_batch(
        self,
        job_id: str,
        *,
        user_id: str,
        batch_size: int = 10,
        include_nonofficial_margins: bool = False,
        collectors: dict[str, Any] | None = None,
        time_budget_seconds: float | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        lease_heartbeat_seconds: float | None = DEFAULT_LEASE_HEARTBEAT_SECONDS,
        lease_heartbeat_sleep: Callable[[float], None] | None = None,
        symbol_deadline_seconds: float | None = DEFAULT_SYMBOL_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        if not self._repo.is_available():
            raise FirstPassJobError("first_pass_job_store_unavailable")
        job = self.get_job(job_id, user_id=user_id)
        claim = self._repo.claim_first_pass_batch(job_id, batch_size=batch_size)
        if claim is None:
            raise KeyError("first_pass_job_not_found")
        empty = {
            "durationMs": 0,
            "budgetStopped": False,
            "releasedUnstarted": 0,
            "batchCounts": {"ok": 0, "partial": 0, "failed": 0},
            "leaseLost": False,
            "jobFailed": False,
            "lastError": None,
        }
        if claim.get("terminal_failed"):
            job_out = claim["job"]
            return {
                "job": job_out,
                "batchIds": [],
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": False,
                "processed": 0,
                "jobFailed": True,
                "lastError": _last_error_from_job(job_out),
                "durationMs": 0,
                "budgetStopped": False,
                "releasedUnstarted": 0,
                "batchCounts": {"ok": 0, "partial": 0, "failed": 0},
                "leaseLost": False,
            }
        if claim.get("lease_held"):
            return {
                "job": claim["job"],
                "batchIds": [],
                "leaseHeld": True,
                "alreadyComplete": False,
                "processed": 0,
                **empty,
            }
        if claim.get("waiting_for_retry"):
            return {
                "job": claim["job"],
                "batchIds": [],
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": True,
                "processed": 0,
                **empty,
            }
        if claim.get("already_complete"):
            finalized = self._maybe_finalize(job_id, user_id=user_id)
            return {
                "job": finalized or claim["job"],
                "batchIds": [],
                "leaseHeld": False,
                "alreadyComplete": True,
                "waitingForRetry": False,
                "processed": 0,
                **empty,
            }

        batch_ids = list(claim["batch_ids"])
        generation = int(claim["lease_generation"])
        kwargs = dict(collectors or {})
        previous_fields_by_stock: dict[str, dict[str, Any]] = {}
        previous_payloads: dict[str, dict[str, Any]] = {}
        for stock_id in batch_ids:
            previous = self._repo.get_first_pass_symbol(job_id, stock_id)
            if previous is None:
                continue
            payload = dict(previous.get("payload") or {})
            previous_payloads[stock_id] = payload
            fields = payload.get("fields")
            if isinstance(fields, dict):
                prior = dict(fields)
                # Carry the full relative-price evidence alongside provenance fields
                # so retries do not collapse to an abbreviated summary.
                relative_basis = payload.get("relativePriceBasis")
                if isinstance(relative_basis, dict):
                    prior["relativePriceBasis"] = relative_basis
                previous_fields_by_stock[stock_id] = prior
        clock = monotonic_clock or time.monotonic

        def _renew_lease() -> bool:
            return bool(
                self._repo.renew_first_pass_lease(
                    job_id,
                    lease_generation=generation,
                )
            )

        stop_event = threading.Event()
        lease_lost_event = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        if lease_heartbeat_seconds is not None and float(lease_heartbeat_seconds) > 0:
            heartbeat_thread = threading.Thread(
                target=_lease_heartbeat_loop,
                kwargs={
                    "renew": _renew_lease,
                    "interval_seconds": float(lease_heartbeat_seconds),
                    "stop_event": stop_event,
                    "lease_lost_event": lease_lost_event,
                    "sleep": lease_heartbeat_sleep,
                    "job_id": job_id,
                },
                name=f"first-pass-lease-heartbeat-{job_id}",
                daemon=True,
            )
            heartbeat_thread.start()

        try:
            result = collect_first_pass_batch(
                batch_ids,
                universe_version=job["universe_version"],
                include_nonofficial_margins=include_nonofficial_margins,
                previous_fields_by_stock=previous_fields_by_stock,
                time_budget_seconds=time_budget_seconds,
                monotonic_clock=clock,
                job_id=job_id,
                lease_renew=_renew_lease,
                lease_lost_check=lease_lost_event.is_set,
                symbol_deadline_seconds=symbol_deadline_seconds,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — classify infra vs unexpected
            if is_infrastructure_failure(exc):
                raise FirstPassJobError(
                    "first_pass_infrastructure_unavailable",
                    operation="run_first_pass_batch",
                ) from exc
            raise
        finally:
            stop_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=_HEARTBEAT_JOIN_TIMEOUT_SECONDS)
        if result.lease_lost or lease_lost_event.is_set():
            # Lease lost mid-batch — do not persist any symbols from this claim.
            return {
                "job": claim["job"],
                "batchIds": batch_ids,
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": False,
                "processed": 0,
                "durationMs": int(result.duration_ms),
                "budgetStopped": bool(result.budget_stopped),
                "releasedUnstarted": len(result.released_unstarted),
                "batchCounts": {"ok": 0, "partial": 0, "failed": 0},
                "leaseLost": True,
                "jobFailed": False,
                "lastError": None,
            }
        processed = 0
        lease_rejected = 0
        batch_counts = {"ok": 0, "partial": 0, "failed": 0}
        persisted_payloads: list[tuple[FirstPassSymbolRecord, str, dict[str, Any]]] = []
        for record in result.records:
            status = _symbol_status(record)
            previous_payload = previous_payloads.get(record.stock_id)
            if previous_payload is None:
                previous = self._repo.get_first_pass_symbol(job_id, record.stock_id)
                previous_payload = (previous or {}).get("payload")
            payload = _enrich_symbol_payload(
                record,
                status=status,
                previous_payload=previous_payload,
            )
            # After merging preserved OK fields, a formerly-failed symbol may no
            # longer have retryable field faults — reclassify before persist.
            if status == "failed":
                remaining = dominant_transient_field_error(payload.get("fields") or {})
                top_error = str(payload.get("error") or record.error or "")
                top_class = _classify_symbol_failure(top_error)
                if remaining is not None:
                    payload["error"] = remaining
                elif top_error and top_class == "permanent":
                    status = "partial"
                    payload["failureClass"] = "permanent"
                    payload["retryExhausted"] = True
                    payload["nextRetryAt"] = None
                elif top_error and top_class == "programming_error":
                    payload["error"] = top_error
                    payload["failureClass"] = "programming_error"
                    payload["retryExhausted"] = True
                    payload["nextRetryAt"] = None
                elif top_error and top_class != "permanent":
                    # Keep symbol-level transient failures (may have empty fields).
                    payload["error"] = top_error
                else:
                    # Transient field faults were restored/cleared by merge.
                    payload["error"] = None
                    pe = (payload.get("fields") or {}).get("official_pe") or {}
                    if (
                        isinstance(pe, dict)
                        and pe.get("status") == "unavailable"
                        and not field_reason_is_retryable(pe.get("reason"))
                    ):
                        status = "partial"
                    elif record.name_status == "unavailable":
                        status = "partial"
                    else:
                        status = "ok"
                    payload["failureClass"] = None
                    payload["retryExhausted"] = False
                    payload["nextRetryAt"] = None
            try:
                ok = self._repo.upsert_first_pass_symbol(
                    job_id=job_id,
                    stock_id=record.stock_id,
                    status=status,
                    payload=payload,
                    lease_generation=generation,
                )
            except Exception as exc:  # noqa: BLE001
                if is_infrastructure_failure(exc):
                    raise FirstPassJobError(
                        "first_pass_infrastructure_unavailable",
                        operation="persist_first_pass_symbol",
                    ) from exc
                raise
            if ok:
                processed += 1
                if status in batch_counts:
                    batch_counts[status] += 1
                persisted_payloads.append((record, status, payload))
            else:
                lease_rejected += 1

        if lease_rejected and processed == 0 and result.records:
            raise FirstPassJobError(
                "first_pass_lease_generation_mismatch",
                operation="persist_first_pass_symbol",
            )

        # Unstarted claimed ids are released by complete_first_pass_batch resetting
        # the reserve to the first missing index — attempts stay unchanged.
        released_unstarted = list(result.released_unstarted)
        systemic_error = _systemic_programming_error(
            persisted_payloads, finished_count=processed
        )

        try:
            completed = self._repo.complete_first_pass_batch(
                job_id,
                lease_generation=generation,
                processed_count=processed,
                terminal_error=systemic_error,
            )
        except Exception as exc:  # noqa: BLE001
            if is_infrastructure_failure(exc):
                raise FirstPassJobError(
                    "first_pass_infrastructure_unavailable",
                    operation="complete_first_pass_batch",
                ) from exc
            raise
        if completed is None:
            # Generation changed (or job vanished) before the locked complete:
            # another claimer owns the job now. Never report jobFailed from a
            # stale view — the systemic error is not applied.
            return {
                "job": claim["job"],
                "batchIds": batch_ids,
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": False,
                "processed": processed,
                "durationMs": int(result.duration_ms),
                "budgetStopped": bool(result.budget_stopped),
                "releasedUnstarted": len(released_unstarted),
                "batchCounts": batch_counts,
                "leaseLost": True,
                "jobFailed": False,
                "lastError": None,
            }
        job_out = completed["job"]

        if systemic_error is not None:
            # Failed atomically inside complete_first_pass_batch (same txn).
            return {
                "job": job_out,
                "batchIds": batch_ids,
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": False,
                "processed": processed,
                "durationMs": int(result.duration_ms),
                "budgetStopped": bool(result.budget_stopped),
                "releasedUnstarted": len(released_unstarted),
                "batchCounts": batch_counts,
                "systemicProgrammingError": systemic_error,
                "jobFailed": job_out.get("status") == "failed",
                "lastError": systemic_error,
                "leaseLost": False,
            }

        # Finalize only when every symbol is terminal (ok/partial/exhausted), not
        # while transient failures remain in backoff.
        if job_out.get("status") in {"completed", "partial"}:
            finalized = self._maybe_finalize(job_id, user_id=user_id)
            if finalized is not None:
                job_out = finalized
        return {
            "job": job_out,
            "batchIds": batch_ids,
            "leaseHeld": False,
            "alreadyComplete": False,
            "waitingForRetry": job_out.get("status") == "deferred",
            "processed": processed,
            "durationMs": int(result.duration_ms),
            "budgetStopped": bool(result.budget_stopped),
            "releasedUnstarted": len(released_unstarted),
            "batchCounts": batch_counts,
            "leaseLost": False,
            "jobFailed": False,
            "lastError": None,
        }

    def list_symbols(
        self,
        job_id: str,
        *,
        user_id: str,
        status: str | None = None,
        q: str | None = None,
        completeness: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        self.get_job(job_id, user_id=user_id)
        rows = self._repo.list_first_pass_symbols(
            job_id,
            status=status,
            q=q,
            completeness=completeness,
            offset=offset,
            limit=limit,
        )
        total = self._repo.count_first_pass_symbols_matching(
            job_id,
            status=status,
            q=q,
            completeness=completeness,
        )
        return {
            "jobId": job_id,
            "offset": offset,
            "limit": limit,
            "total": total,
            "statusFilter": status,
            "q": q,
            "completenessFilter": completeness,
            "records": [
                {
                    "stockId": row["stock_id"],
                    "stockName": (row["payload"] or {}).get("stockName"),
                    "nameStatus": (row["payload"] or {}).get("nameStatus"),
                    "displayLabel": _display_label(row["payload"] or {}, row["stock_id"]),
                    "status": row["status"],
                    "sectorThemeKeys": list(
                        (row["payload"] or {}).get("sectorThemeKeys") or []
                    ),
                    "updatedAt": row["updated_at"],
                    "evidence": row["payload"],
                }
                for row in rows
            ],
        }

    def _maybe_finalize(self, job_id: str, *, user_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id, user_id=user_id)
        if job.get("status") in {"failed", "cancelled"}:
            return job
        if job.get("active_snapshot_id"):
            return job
        payloads = self._repo.list_first_pass_snapshot_payloads(job_id)
        if len(payloads) < int(job["universe_size"]):
            return job
        records = [
            FirstPassSymbolRecord(
                stock_id=str(item.get("stockId") or ""),
                stock_name=item.get("stockName"),
                name_status=item.get("nameStatus") or "unavailable",
                name_source=str(item.get("nameSource") or "catalog"),
                exchange=item.get("exchange"),
                market=item.get("market"),
                sector_theme_keys=list(item.get("sectorThemeKeys") or []),
                first_pass_version=str(item.get("firstPassVersion") or FIRST_PASS_VERSION),
                relative_price_basis=item.get("relativePriceBasis"),
                completeness=dict(item.get("completeness") or {}),
                error=item.get("error"),
            )
            for item in payloads
        ]
        # Rebuild fields is expensive; store payloads as-is in snapshot.
        batch = FirstPassBatchResult(
            first_pass_version=FIRST_PASS_VERSION,
            universe_version=job["universe_version"],
            requested_count=len(job["stock_ids"]),
            collected_count=len(payloads),
            deduped_count=len(job["stock_ids"]),
            records=records,
            coverage_summary={
                "symbolsRequestedAfterDedupe": len(job["stock_ids"]),
                "symbolsPersisted": len(payloads),
                "note": "Snapshot includes every frozen-universe symbol attempted.",
            },
            notes=[
                "Immutable first-pass dataset for analyst review.",
                "Not a Notion report list.",
            ],
        )
        # Attach raw payloads so export has full evidence including fields.
        payload = batch.as_dict()
        payload["records"] = payloads
        payload["universeDigest"] = job["universe_digest"]
        payload["jobId"] = job_id
        try:
            from app.services.tw_research.first_pass_durable import first_pass_snapshot_id

            digest = hashlib.sha256(
                json.dumps(payloads, sort_keys=True, separators=(",", ":"), default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
            snapshot_id = first_pass_snapshot_id(user_id=user_id, content_digest=digest)
            saved = self._repo.save_first_pass_snapshot(
                snapshot_id=snapshot_id,
                user_id=user_id,
                universe_version=job["universe_version"],
                first_pass_version=FIRST_PASS_VERSION,
                content_digest=digest,
                payload=payload,
                stock_ids=list(job["stock_ids"]),
            )
        except Exception as exc:  # noqa: BLE001
            raise FirstPassDurableUnavailable(str(exc)) from exc

        quality = "complete_attempted"
        return self._repo.set_first_pass_job_snapshot(
            job_id,
            snapshot_id=saved["snapshot_id"],
            quality_status=quality,
        )


def _display_label(payload: dict[str, Any], stock_id: str) -> str:
    """Traditional Chinese name + canonical id, or explicit unavailable marker."""
    short_id = stock_id.split(":", 1)[-1] if stock_id else stock_id
    name = str(payload.get("stockName") or "").strip()
    name_status = str(payload.get("nameStatus") or "")
    if name and name_status != "unavailable":
        return f"{name} ({short_id})"
    return f"名稱不可用 ({short_id})"


def _last_error_from_job(job: dict[str, Any] | None) -> str | None:
    if not isinstance(job, dict):
        return None
    explicit = job.get("last_error") or job.get("lastError")
    if explicit:
        return str(explicit)
    for note in reversed(list(job.get("notes") or [])):
        text = str(note or "")
        if text.startswith("error:"):
            return text[len("error:") :]
        if text.startswith("systemic_programming_error:"):
            return text
    return None


def _systemic_programming_error(
    persisted: list[tuple[FirstPassSymbolRecord, str, dict[str, Any]]],
    *,
    finished_count: int,
) -> str | None:
    """Return systemic error key when ≥3 symbols and ≥50% share (field, ExcClass)."""
    if finished_count <= 0:
        return None
    counter: Counter[tuple[str, str]] = Counter()
    for record, _status, payload in persisted:
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        for key, value in fields.items():
            if not isinstance(value, dict):
                continue
            reason = str(value.get("reason") or "")
            if classify_field_reason(reason) != "programming_error":
                continue
            # programming_error:TypeError → ExcClass TypeError
            exc_class = "Error"
            if ":" in reason:
                exc_class = reason.rsplit(":", 1)[-1].strip() or "Error"
            counter[(key, exc_class)] += 1
        top_error = str(payload.get("error") or record.error or "")
        if classify_field_reason(top_error) == "programming_error":
            exc_class = (
                top_error.rsplit(":", 1)[-1].strip() if ":" in top_error else "Error"
            )
            counter[("_symbol_", exc_class)] += 1
    if not counter:
        return None
    (field, exc_class), count = counter.most_common(1)[0]
    if count >= 3 and count * 2 >= finished_count:
        label = field if field != "_symbol_" else "symbol"
        return f"systemic_programming_error:{label}:{exc_class}"
    return None


first_pass_job_service = FirstPassJobService()
