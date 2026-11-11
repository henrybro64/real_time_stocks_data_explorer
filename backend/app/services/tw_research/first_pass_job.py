"""Resumable full-universe first-pass collection jobs (#335).

Reuses the scan-style lease/cursor pattern: create a job with a frozen
deduplicated universe, claim bounded batches, persist per-symbol evidence,
then finalize an immutable snapshot for the analyst.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.constants import FIRST_PASS_VERSION
from app.services.tw_research.first_pass import (
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
        return "failed"
    if _symbol_has_retryable_field_failure(record):
        # Field-level transient faults (valuation timeout, HTTP 429, etc.) must
        # enter the durable retry path even when catalog identity succeeded.
        return "failed"
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
        exhausted = attempts >= _FIRST_PASS_MAX_ATTEMPTS or failure_class == "permanent"
        from datetime import UTC, datetime, timedelta

        payload["attemptCount"] = attempts
        payload["failureClass"] = failure_class
        payload["retryExhausted"] = exhausted
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
        payload["failureClass"] = None
        payload["retryExhausted"] = False
        payload["nextRetryAt"] = None
        payload.pop("preservedOkFields", None)
    return payload


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
    ) -> dict[str, Any]:
        if not self._repo.is_available():
            raise FirstPassJobError(
                "first_pass_job_store_unavailable",
                operation="create_first_pass_job",
            )

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

        job_id = f"fpj_{uuid.uuid4().hex[:24]}"
        digest = _universe_digest(ids, universe_version)
        return self._repo.create_first_pass_job(
            {
                "job_id": job_id,
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
    ) -> dict[str, Any]:
        if not self._repo.is_available():
            raise FirstPassJobError("first_pass_job_store_unavailable")
        job = self.get_job(job_id, user_id=user_id)
        claim = self._repo.claim_first_pass_batch(job_id, batch_size=batch_size)
        if claim is None:
            raise KeyError("first_pass_job_not_found")
        if claim.get("lease_held"):
            return {
                "job": claim["job"],
                "batchIds": [],
                "leaseHeld": True,
                "alreadyComplete": False,
                "processed": 0,
            }
        if claim.get("waiting_for_retry"):
            return {
                "job": claim["job"],
                "batchIds": [],
                "leaseHeld": False,
                "alreadyComplete": False,
                "waitingForRetry": True,
                "processed": 0,
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
        try:
            result = collect_first_pass_batch(
                batch_ids,
                universe_version=job["universe_version"],
                include_nonofficial_margins=include_nonofficial_margins,
                previous_fields_by_stock=previous_fields_by_stock,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — classify infra vs unexpected
            if is_infrastructure_failure(exc):
                raise FirstPassJobError(
                    "first_pass_infrastructure_unavailable",
                    operation="run_first_pass_batch",
                ) from exc
            raise
        processed = 0
        lease_rejected = 0
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
            else:
                lease_rejected += 1

        if lease_rejected and processed == 0 and result.records:
            raise FirstPassJobError(
                "first_pass_lease_generation_mismatch",
                operation="persist_first_pass_symbol",
            )

        try:
            completed = self._repo.complete_first_pass_batch(
                job_id,
                lease_generation=generation,
                processed_count=processed,
            )
        except Exception as exc:  # noqa: BLE001
            if is_infrastructure_failure(exc):
                raise FirstPassJobError(
                    "first_pass_infrastructure_unavailable",
                    operation="complete_first_pass_batch",
                ) from exc
            raise
        job_out = (completed or {}).get("job") or claim["job"]
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


first_pass_job_service = FirstPassJobService()
