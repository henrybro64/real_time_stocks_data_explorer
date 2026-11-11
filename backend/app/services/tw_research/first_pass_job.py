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
    collect_first_pass_batch,
    dedupe_stock_ids,
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


def _symbol_status(record: FirstPassSymbolRecord) -> str:
    if record.error:
        return "failed"
    completeness = record.completeness or {}
    if completeness.get("fieldsUnavailable", 0) and not completeness.get("fieldsOk", 0):
        return "partial"
    pe = record.fields.get("official_pe")
    if pe is not None and pe.status == "unavailable":
        return "partial"
    if record.name_status == "unavailable":
        return "partial"
    return "ok"


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
        if claim.get("already_complete"):
            finalized = self._maybe_finalize(job_id, user_id=user_id)
            return {
                "job": finalized or claim["job"],
                "batchIds": [],
                "leaseHeld": False,
                "alreadyComplete": True,
                "processed": 0,
            }

        batch_ids = list(claim["batch_ids"])
        generation = int(claim["lease_generation"])
        kwargs = dict(collectors or {})
        result = collect_first_pass_batch(
            batch_ids,
            universe_version=job["universe_version"],
            include_nonofficial_margins=include_nonofficial_margins,
            **kwargs,
        )
        processed = 0
        for record in result.records:
            status = _symbol_status(record)
            ok = self._repo.upsert_first_pass_symbol(
                job_id=job_id,
                stock_id=record.stock_id,
                status=status,
                payload=record.as_dict(),
                lease_generation=generation,
            )
            if ok:
                processed += 1

        completed = self._repo.complete_first_pass_batch(
            job_id,
            lease_generation=generation,
            processed_count=processed,
        )
        job_out = (completed or {}).get("job") or claim["job"]
        if job_out.get("status") in {"completed", "partial", "deferred"}:
            finalized = self._maybe_finalize(job_id, user_id=user_id)
            if finalized is not None:
                job_out = finalized
        return {
            "job": job_out,
            "batchIds": batch_ids,
            "leaseHeld": False,
            "alreadyComplete": False,
            "processed": processed,
        }

    def list_symbols(
        self,
        job_id: str,
        *,
        user_id: str,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        self.get_job(job_id, user_id=user_id)
        rows = self._repo.list_first_pass_symbols(
            job_id, status=status, offset=offset, limit=limit
        )
        return {
            "jobId": job_id,
            "offset": offset,
            "limit": limit,
            "statusFilter": status,
            "records": [
                {
                    "stockId": row["stock_id"],
                    "status": row["status"],
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
            digest = hashlib.sha256(
                json.dumps(payloads, sort_keys=True, separators=(",", ":"), default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
            snapshot_id = f"fp_{digest[:24]}"
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


first_pass_job_service = FirstPassJobService()
