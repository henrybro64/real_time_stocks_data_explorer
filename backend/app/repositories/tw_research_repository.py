"""PostgreSQL persistence for Taiwan research scans and export snapshots (#318)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    TwResearchCandidateSet,
    TwResearchCoordinationLock,
    TwResearchExportSnapshot,
    TwResearchFirstPassJob,
    TwResearchFirstPassSnapshot,
    TwResearchFirstPassSymbolResult,
    TwResearchHistoryFlight,
    TwResearchMembership,
    TwResearchResearchRun,
    TwResearchScan,
    TwResearchSymbolResult,
)
from app.repositories.session import SessionBoundRepository
from app.services.tw_research.constants import (
    DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
    HISTORY_FLIGHT_LEASE_SECONDS,
    INCOMPLETE_SCHEDULE_STATUSES,
    SCAN_ADMISSION_LOCK_NAME,
    SCHEDULE_TICK_LEASE_SECONDS,
)

# How long a claimed batch may remain unfinished before another worker can reclaim.
BATCH_LEASE_SECONDS = 300


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite/Postgres timestamps for lease comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_utc_iso(value: datetime | str | None) -> datetime | None:
    """Parse ISO timestamps used by research-run persist payloads."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _first_missing_symbol_index(session: Session, scan_id: str, stock_ids: list[str]) -> int:
    """Return the first index in ``stock_ids`` without a persisted symbol result."""
    if not stock_ids:
        return 0
    existing = set(
        session.scalars(
            select(TwResearchSymbolResult.stock_id).where(
                TwResearchSymbolResult.scan_id == scan_id
            )
        ).all()
    )
    for index, stock_id in enumerate(stock_ids):
        if stock_id not in existing:
            return index
    return len(stock_ids)


def _symbol_enrichment_map(session: Session, scan_id: str) -> dict[str, bool]:
    rows = session.execute(
        select(TwResearchSymbolResult).where(TwResearchSymbolResult.scan_id == scan_id)
    ).scalars()
    return {
        row.stock_id: bool((row.payload_json or {}).get("enrichment"))
        for row in rows
    }


def _unenriched_qualified_ids(session: Session, row: TwResearchScan) -> list[str]:
    qualified = list(row.qualified_ids_json or [])
    enriched = _symbol_enrichment_map(session, row.scan_id)
    return [stock_id for stock_id in qualified if not enriched.get(stock_id)]


def _enrichment_completed_cursor(session: Session, row: TwResearchScan) -> int:
    """Index of first qualified id still missing enrichment (completed watermark)."""
    qualified = list(row.qualified_ids_json or [])
    enriched = _symbol_enrichment_map(session, row.scan_id)
    for index, stock_id in enumerate(qualified):
        if not enriched.get(stock_id):
            return index
    return len(qualified)


def _enriched_qualified_count(session: Session, row: TwResearchScan) -> int:
    qualified = list(row.qualified_ids_json or [])
    enriched = _symbol_enrichment_map(session, row.scan_id)
    return sum(1 for stock_id in qualified if enriched.get(stock_id))


def _count_active_batch_leases(
    session: Session,
    *,
    now: datetime,
    exclude_scan_id: str | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(TwResearchScan)
        .where(
            TwResearchScan.reserve_expires_at.is_not(None),
            TwResearchScan.reserve_expires_at > now,
        )
    )
    if exclude_scan_id:
        stmt = stmt.where(TwResearchScan.scan_id != exclude_scan_id)
    total = session.execute(stmt).scalar_one()
    return int(total or 0)


def _ensure_coordination_lock(session: Session, lock_name: str) -> None:
    """Insert the named lock row if missing (idempotent; safe under races)."""
    if session.get(TwResearchCoordinationLock, lock_name) is not None:
        return
    try:
        with session.begin_nested():
            session.add(
                TwResearchCoordinationLock(
                    lock_name=lock_name,
                    holder_token=None,
                    lease_expires_at=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.flush()
    except IntegrityError:
        # Concurrent creator won; row is visible after the savepoint abort.
        session.expire_all()


def _scan_to_dict(row: TwResearchScan) -> dict[str, Any]:
    return {
        "scan_id": row.scan_id,
        "user_id": row.user_id,
        "trading_day": row.trading_day,
        "universe_version": row.universe_version,
        "calculation_version": row.calculation_version,
        "enrichment_version": row.enrichment_version,
        "selector_json": dict(row.selector_json or {}),
        "stock_ids_json": list(row.stock_ids_json or []),
        "status": row.status,
        "quality_status": row.quality_status,
        "cursor": int(row.cursor),
        "reserve_cursor": int(getattr(row, "reserve_cursor", 0) or 0),
        "reserve_expires_at": getattr(row, "reserve_expires_at", None),
        "enrich_cursor": int(row.enrich_cursor),
        "enrich_reserve_ids_json": list(getattr(row, "enrich_reserve_ids_json", None) or []),
        "enrich_reserve_expires_at": getattr(row, "enrich_reserve_expires_at", None),
        "enrich_lease_generation": int(getattr(row, "enrich_lease_generation", 0) or 0),
        "scan_lease_generation": int(getattr(row, "scan_lease_generation", 0) or 0),
        "budget_windows_opened": int(getattr(row, "budget_windows_opened", 0) or 0),
        "target_count": int(row.target_count),
        "upstream_month_fetches": int(row.upstream_month_fetches),
        "max_upstream_month_fetches": int(row.max_upstream_month_fetches),
        "enrichments_run": int(row.enrichments_run),
        "max_enrichments_per_run": int(row.max_enrichments_per_run),
        "day_pass_ids_json": list(row.day_pass_ids_json or []),
        "qualified_ids_json": list(row.qualified_ids_json or []),
        "notes_json": list(row.notes_json or []),
        "active_snapshot_id": row.active_snapshot_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _symbol_to_dict(row: TwResearchSymbolResult) -> dict[str, Any]:
    return {
        "scan_id": row.scan_id,
        "stock_id": row.stock_id,
        "trading_day": row.trading_day,
        "universe_version": row.universe_version,
        "calculation_version": row.calculation_version,
        "stage": row.stage,
        "readiness": row.readiness,
        "payload_json": dict(row.payload_json or {}),
        "updated_at": row.updated_at,
    }


def _snapshot_to_dict(row: TwResearchExportSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "scan_id": row.scan_id,
        "user_id": row.user_id,
        "universe_version": row.universe_version,
        "trading_day": row.trading_day,
        "schema_version": row.schema_version,
        "calculation_version": row.calculation_version,
        "enrichment_version": row.enrichment_version,
        "quality_status": row.quality_status,
        "content_digest": row.content_digest,
        "manifest_json": dict(row.manifest_json or {}),
        "ledger_json": list(row.ledger_json or []),
        "chunk_digests_json": dict(row.chunk_digests_json or {}),
        "finalized_at": row.finalized_at,
        "created_at": row.created_at,
    }


# Claim/complete/rewind own these; blind upsert must never regress them.
_SCAN_PROGRESS_KEYS = frozenset(
    {
        "cursor",
        "reserve_cursor",
        "reserve_expires_at",
        "enrich_cursor",
        "enrich_reserve_ids_json",
        "enrich_reserve_expires_at",
        "enrich_lease_generation",
        "scan_lease_generation",
        "budget_windows_opened",
    }
)
_SCAN_COUNTER_MAX_KEYS = frozenset(
    {
        "upstream_month_fetches",
        "enrichments_run",
        # Resume raises the cap; stale workers must not regress it (#329).
        "max_upstream_month_fetches",
        "max_enrichments_per_run",
    }
)
_SCAN_STATUS_RANK = {
    "queued": 0,
    "running": 1,
    "partial": 2,
    "budget_exhausted": 3,
    "completed_processing": 4,
    "verified_ready": 5,
    "failed": 6,
}


class TwResearchRepository(SessionBoundRepository):
    def upsert_scan(self, payload: dict[str, Any]) -> None:
        """Upsert scan metadata without regressing durable progress (#318).

        Cursor / lease fields are owned by claim/complete/rewind. Counters only
        move forward. Status never moves backward (except sticky ``failed``).
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == payload["scan_id"])
                .with_for_update()
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if row is None:
                insert_payload = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"reserve_cursor", "reserve_expires_at"}
                }
                insert_payload.setdefault("reserve_cursor", int(payload.get("cursor") or 0))
                insert_payload.setdefault("reserve_expires_at", None)
                insert_payload.setdefault("enrich_reserve_ids_json", [])
                insert_payload.setdefault("enrich_reserve_expires_at", None)
                insert_payload.setdefault("enrich_lease_generation", 0)
                insert_payload.setdefault("scan_lease_generation", 0)
                insert_payload.setdefault("budget_windows_opened", 0)
                session.add(
                    TwResearchScan(
                        **insert_payload,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                for key, value in payload.items():
                    if key == "scan_id" or key in _SCAN_PROGRESS_KEYS:
                        continue
                    if key in _SCAN_COUNTER_MAX_KEYS:
                        setattr(row, key, max(int(getattr(row, key) or 0), int(value or 0)))
                        continue
                    if key == "status":
                        current = str(row.status)
                        incoming = str(value)
                        if current == "failed":
                            continue
                        if _SCAN_STATUS_RANK.get(incoming, -1) >= _SCAN_STATUS_RANK.get(
                            current, -1
                        ):
                            row.status = incoming
                        continue
                    if key == "quality_status":
                        # Never demote a finalized scan via a stale worker write.
                        if (
                            str(row.quality_status) == "verified_ready"
                            and str(value) != "verified_ready"
                        ):
                            continue
                        row.quality_status = value
                        continue
                    if key in {"day_pass_ids_json", "qualified_ids_json"}:
                        merged = list(dict.fromkeys([*(getattr(row, key) or []), *(value or [])]))
                        setattr(row, key, merged)
                        continue
                    if key == "notes_json":
                        merged = list(dict.fromkeys([*(getattr(row, key) or []), *(value or [])]))
                        setattr(row, key, merged[-50:])
                        continue
                    setattr(row, key, value)
                row.updated_at = now
            session.commit()

    def list_scans_for_user(self, user_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Newest scans for one owner. One query, no symbol payloads."""
        if not self.is_available():
            return []
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.user_id == user_id)
                .order_by(TwResearchScan.updated_at.desc(), TwResearchScan.scan_id.desc())
                .limit(limit)
            ).scalars().all()
            return [_scan_to_dict(row) for row in rows]

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchScan, scan_id)
            if row is None:
                return None
            return _scan_to_dict(row)

    def claim_scan_batch(
        self,
        scan_id: str,
        *,
        batch_size: int,
        terminal_statuses: set[str],
        lease_seconds: int = BATCH_LEASE_SECONDS,
        max_in_flight: int | None = DEFAULT_MAX_IN_FLIGHT_SCAN_BATCHES,
    ) -> dict[str, Any] | None:
        """Atomically admit + reserve the next ``batch_size`` stock IDs (#318/#329).

        ``cursor`` is the completed watermark (symbols with persisted results).
        Reservations advance ``reserve_cursor`` under a short lease so a crashed
        worker does not permanently skip unfinished symbols.

        Cluster-wide admission is serialized via the ``scan_admission`` coordination
        lock in the same transaction as the claim — never a TOCTOU count-then-claim.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            _ensure_coordination_lock(session, SCAN_ADMISSION_LOCK_NAME)
            session.execute(
                select(TwResearchCoordinationLock)
                .where(TwResearchCoordinationLock.lock_name == SCAN_ADMISSION_LOCK_NAME)
                .with_for_update()
            ).scalar_one()

            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            stock_ids = list(row.stock_ids_json or [])
            lease_until = _as_utc(getattr(row, "reserve_expires_at", None))
            reserve_cursor = int(getattr(row, "reserve_cursor", 0) or 0)
            cursor = int(row.cursor)

            if lease_until is not None and lease_until <= now:
                # Abandoned reservation: rewind completed watermark to first gap.
                cursor = _first_missing_symbol_index(session, scan_id, stock_ids)
                row.cursor = cursor
                row.reserve_cursor = cursor
                row.reserve_expires_at = None
                reserve_cursor = cursor
                lease_until = None

            own_lease_active = (
                lease_until is not None
                and lease_until > now
                and reserve_cursor > cursor
            )
            if own_lease_active:
                session.commit()
                return {
                    "batch_ids": [],
                    "scan": _scan_to_dict(row),
                    "already_complete": False,
                    "lease_held": True,
                    "admission_denied": False,
                    "reserved_from": cursor,
                    "lease_generation": int(
                        getattr(row, "scan_lease_generation", 0) or 0
                    ),
                }

            if max_in_flight is not None:
                in_flight = _count_active_batch_leases(
                    session, now=now, exclude_scan_id=scan_id
                )
                if in_flight >= int(max_in_flight):
                    session.commit()
                    return {
                        "batch_ids": [],
                        "scan": _scan_to_dict(row),
                        "already_complete": False,
                        "lease_held": False,
                        "admission_denied": True,
                        "in_flight": in_flight,
                        "limit": int(max_in_flight),
                        "reserved_from": cursor,
                        "lease_generation": int(
                            getattr(row, "scan_lease_generation", 0) or 0
                        ),
                    }

            cursor = _first_missing_symbol_index(session, scan_id, stock_ids)
            row.cursor = cursor
            if row.status in terminal_statuses and cursor >= len(stock_ids):
                row.reserve_cursor = cursor
                row.reserve_expires_at = None
                session.commit()
                return {
                    "batch_ids": [],
                    "scan": _scan_to_dict(row),
                    "already_complete": True,
                    "lease_held": False,
                    "admission_denied": False,
                    "reserved_from": cursor,
                    "lease_generation": int(
                        getattr(row, "scan_lease_generation", 0) or 0
                    ),
                }
            if row.status == "queued":
                row.status = "running"
            batch_ids = stock_ids[cursor : cursor + batch_size]
            generation = int(getattr(row, "scan_lease_generation", 0) or 0) + 1
            row.scan_lease_generation = generation
            row.reserve_cursor = cursor + len(batch_ids)
            row.reserve_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
            row.updated_at = now
            session.commit()
            return {
                "batch_ids": batch_ids,
                "scan": _scan_to_dict(row),
                "already_complete": False,
                "lease_held": False,
                "admission_denied": False,
                "reserved_from": cursor,
                "lease_generation": generation,
            }

    def complete_scan_batch(
        self,
        scan_id: str,
        *,
        status: str,
        lease_generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Advance the completed watermark from persisted symbol results and clear the lease.

        When ``lease_generation`` is supplied and no longer matches, this is a
        no-op on the lease (another worker already reclaimed).
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            current_generation = int(getattr(row, "scan_lease_generation", 0) or 0)
            stale = (
                lease_generation is not None
                and int(lease_generation) != current_generation
            )
            if stale:
                result = _scan_to_dict(row)
                result["stale_lease"] = True
                session.commit()
                return result
            stock_ids = list(row.stock_ids_json or [])
            completed = _first_missing_symbol_index(session, scan_id, stock_ids)
            row.cursor = completed
            row.reserve_cursor = completed
            row.reserve_expires_at = None
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            result = _scan_to_dict(row)
            result["stale_lease"] = False
            return result

    def rewind_scan_cursor(self, scan_id: str, *, new_cursor: int, status: str) -> None:
        """Clear the lease and set the completed watermark from durable results.

        ``new_cursor`` is advisory (in-memory estimate); the durable cursor is the
        first stock_id without a persisted symbol result so abandoned work retries.
        """
        del new_cursor  # durable reconciliation wins over the in-memory estimate
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return
            stock_ids = list(row.stock_ids_json or [])
            completed = _first_missing_symbol_index(session, scan_id, stock_ids)
            row.cursor = completed
            row.reserve_cursor = completed
            row.reserve_expires_at = None
            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def claim_enrichment_batch(
        self,
        scan_id: str,
        *,
        batch_size: int,
        lease_seconds: int = BATCH_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        """Reserve enrichment work under a recoverable lease (#318).

        Pending IDs are derived from persisted symbol payloads (qualified and still
        missing enrichment). Cursor advances only after ``complete_enrichment_batch``.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            lease_until = _as_utc(getattr(row, "enrich_reserve_expires_at", None))
            reserved = list(getattr(row, "enrich_reserve_ids_json", None) or [])

            if lease_until is not None and lease_until <= now:
                row.enrich_reserve_ids_json = []
                row.enrich_reserve_expires_at = None
                reserved = []
                lease_until = None

            if lease_until is not None and lease_until > now and reserved:
                session.commit()
                return {
                    "batch_ids": [],
                    "remaining_budget": max(
                        0, int(row.max_enrichments_per_run) - int(row.enrichments_run)
                    ),
                    "scan": _scan_to_dict(row),
                    "lease_held": True,
                    "lease_generation": int(getattr(row, "enrich_lease_generation", 0) or 0),
                }

            pending = _unenriched_qualified_ids(session, row)
            remaining_budget = max(
                0, int(row.max_enrichments_per_run) - int(row.enrichments_run)
            )
            batch_ids = pending[: min(batch_size, remaining_budget)]
            generation = int(getattr(row, "enrich_lease_generation", 0) or 0)
            if batch_ids:
                generation += 1
                row.enrich_lease_generation = generation
                row.enrich_reserve_ids_json = list(batch_ids)
                row.enrich_reserve_expires_at = now + timedelta(
                    seconds=max(1, int(lease_seconds))
                )
            else:
                row.enrich_reserve_ids_json = []
                row.enrich_reserve_expires_at = None
            # Watermark tracks how far into qualified_ids we have durable enrichment.
            row.enrich_cursor = _enrichment_completed_cursor(session, row)
            row.updated_at = now
            session.commit()
            return {
                "batch_ids": batch_ids,
                "remaining_budget": remaining_budget,
                "scan": _scan_to_dict(row),
                "lease_held": False,
                "lease_generation": generation,
            }

    def complete_enrichment_batch(
        self,
        scan_id: str,
        *,
        lease_generation: int | None = None,
    ) -> dict[str, Any] | None:
        """Clear enrichment lease and reconcile cursor/budget from durable rows.

        When ``lease_generation`` is supplied and no longer matches, this is a
        no-op on the lease (another worker already reclaimed).
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            current_generation = int(getattr(row, "enrich_lease_generation", 0) or 0)
            stale = (
                lease_generation is not None
                and int(lease_generation) != current_generation
            )
            enriched_count = _enriched_qualified_count(session, row)
            row.enrichments_run = max(int(row.enrichments_run or 0), enriched_count)
            row.enrich_cursor = _enrichment_completed_cursor(session, row)
            if not stale:
                row.enrich_reserve_ids_json = []
                row.enrich_reserve_expires_at = None
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            result = _scan_to_dict(row)
            result["stale_lease"] = stale
            return result

    def upsert_symbol_result(
        self,
        payload: dict[str, Any],
        *,
        enrich_lease_generation: int | None = None,
        scan_lease_generation: int | None = None,
    ) -> dict[str, Any]:
        """Upsert a symbol row with optional lease-generation fencing (#318/#329).

        Returns ``{"applied": bool, "enrichment_rejected": bool,
        "technical_rejected": bool}``.

        Fencing rules
        -------------
        - ``scan_lease_generation`` (technical screening writes from a claimed
          scan batch): mandatory match against the scan row **and** an active
          scan-batch lease (``reserve_expires_at`` still in the future). Rejected
          writes leave the existing row untouched.
        - ``enrich_lease_generation`` (enrichment updates): must match the scan's
          enrich lease generation. On mismatch, enrichment is stripped but other
          fields may still apply when no technical fence was requested.
        - When neither generation is supplied (scan create seed rows, fixtures,
          unfenced maintenance), the write applies without lease checks.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            scan_row = None
            if scan_lease_generation is not None or (
                enrich_lease_generation is not None
                and dict(payload.get("payload_json") or {}).get("enrichment")
            ):
                scan_row = session.execute(
                    select(TwResearchScan)
                    .where(TwResearchScan.scan_id == payload["scan_id"])
                    .with_for_update()
                ).scalar_one_or_none()

            if scan_lease_generation is not None:
                current_generation = (
                    int(getattr(scan_row, "scan_lease_generation", 0) or 0)
                    if scan_row is not None
                    else -1
                )
                lease_until = (
                    _as_utc(getattr(scan_row, "reserve_expires_at", None))
                    if scan_row is not None
                    else None
                )
                lease_active = lease_until is not None and lease_until > now
                if current_generation != int(scan_lease_generation) or not lease_active:
                    session.commit()
                    return {
                        "applied": False,
                        "enrichment_rejected": False,
                        "technical_rejected": True,
                    }

            existing = session.execute(
                select(TwResearchSymbolResult).where(
                    TwResearchSymbolResult.scan_id == payload["scan_id"],
                    TwResearchSymbolResult.stock_id == payload["stock_id"],
                )
            ).scalar_one_or_none()
            incoming_payload = dict(payload.get("payload_json") or {})
            enrichment_rejected = False
            if enrich_lease_generation is not None and incoming_payload.get("enrichment"):
                current_enrich_generation = (
                    int(getattr(scan_row, "enrich_lease_generation", 0) or 0)
                    if scan_row is not None
                    else -1
                )
                if current_enrich_generation != int(enrich_lease_generation):
                    enrichment_rejected = True
                    incoming_payload = {
                        key: value
                        for key, value in incoming_payload.items()
                        if key != "enrichment"
                    }
                    if existing is not None:
                        existing_payload = dict(existing.payload_json or {})
                        if existing_payload.get("enrichment") is not None:
                            incoming_payload["enrichment"] = existing_payload["enrichment"]
                    payload = {**payload, "payload_json": incoming_payload}
            if existing is None:
                session.add(TwResearchSymbolResult(**payload, updated_at=now))
            else:
                existing_payload = dict(existing.payload_json or {})
                # Never let a stale worker wipe a newer enrichment payload.
                if existing_payload.get("enrichment") and not incoming_payload.get(
                    "enrichment"
                ):
                    incoming_payload["enrichment"] = existing_payload["enrichment"]
                    payload = {**payload, "payload_json": incoming_payload}
                for key, value in payload.items():
                    if key in {"scan_id", "stock_id"}:
                        continue
                    setattr(existing, key, value)
                existing.updated_at = now
            session.commit()
            return {
                "applied": True,
                "enrichment_rejected": enrichment_rejected,
                "technical_rejected": False,
            }

    def list_symbol_results(self, scan_id: str) -> list[dict[str, Any]]:
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchSymbolResult)
                .where(TwResearchSymbolResult.scan_id == scan_id)
                .order_by(TwResearchSymbolResult.stock_id)
            ).scalars()
            return [_symbol_to_dict(row) for row in rows]

    def count_symbol_results(self, scan_id: str) -> int:
        """Symbol row count only — no payload JSON (#327 progress polling)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            total = session.execute(
                select(func.count())
                .select_from(TwResearchSymbolResult)
                .where(TwResearchSymbolResult.scan_id == scan_id)
            ).scalar_one()
            return int(total or 0)

    def list_symbol_evaluation_fields(self, scan_id: str) -> list[dict[str, Any]]:
        """Lean readiness/rejection/pass fields for progress counters (#329).

        Avoids hydrating full symbol payloads (enrichment, SMAs, theme lists) on
        the #327 progress poll path. Extracts only the JSON keys evaluation needs.
        """
        session_factory = self._session_factory()
        payload = TwResearchSymbolResult.payload_json
        with session_factory() as session:
            rows = session.execute(
                select(
                    TwResearchSymbolResult.stock_id,
                    TwResearchSymbolResult.readiness,
                    payload["rejection_reason"].as_string().label("rejection_reason"),
                    payload["daily"].label("daily"),
                    payload["monthly"].label("monthly"),
                    payload["coverage"].label("coverage"),
                )
                .where(TwResearchSymbolResult.scan_id == scan_id)
                .order_by(TwResearchSymbolResult.stock_id)
            ).all()
            result: list[dict[str, Any]] = []
            for stock_id, readiness, rejection_reason, daily, monthly, coverage in rows:
                daily_payload = daily if isinstance(daily, dict) else {}
                monthly_payload = monthly if isinstance(monthly, dict) else {}
                # Keep only passStatus — drop bulky indicator snapshots if present.
                result.append(
                    {
                        "stock_id": stock_id,
                        "readiness": readiness,
                        "rejection_reason": rejection_reason,
                        "daily": (
                            {"passStatus": daily_payload.get("passStatus")}
                            if daily_payload
                            else None
                        ),
                        "monthly": (
                            {"passStatus": monthly_payload.get("passStatus")}
                            if monthly_payload
                            else None
                        ),
                        "coverage": coverage if isinstance(coverage, dict) else None,
                    }
                )
            return result

    def delete_symbol_results_by_rejection(
        self,
        scan_id: str,
        *,
        rejection_reason: str,
    ) -> list[str]:
        """Delete retryable symbol rows matching ``rejection_reason`` (#329)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = list(
                session.execute(
                    select(TwResearchSymbolResult).where(
                        TwResearchSymbolResult.scan_id == scan_id
                    )
                ).scalars()
            )
            deleted: list[str] = []
            for row in rows:
                payload = dict(row.payload_json or {})
                if payload.get("rejection_reason") != rejection_reason:
                    continue
                deleted.append(row.stock_id)
                session.delete(row)
            session.commit()
            return deleted

    def count_active_scan_batch_leases(self, *, exclude_scan_id: str | None = None) -> int:
        """Count scans whose batch reservation lease is still live (#329)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            return _count_active_batch_leases(
                session,
                now=datetime.now(timezone.utc),
                exclude_scan_id=exclude_scan_id,
            )

    def resume_budget_window(
        self,
        scan_id: str,
        *,
        additional_month_fetches: int,
        max_cap: int,
        rejection_reason: str,
        allowed_statuses: set[str],
    ) -> dict[str, Any] | None:
        """Atomically open a bounded budget window and clear retryable rows (#329).

        Single transaction: FOR UPDATE scan → reject active lease → delete
        retryable symbol rows → raise max → rewind cursor → bump window counter.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            lease_until = _as_utc(getattr(row, "reserve_expires_at", None))
            reserve_cursor = int(getattr(row, "reserve_cursor", 0) or 0)
            cursor = int(row.cursor)
            if (
                lease_until is not None
                and lease_until > now
                and reserve_cursor > cursor
            ):
                return {
                    "ok": False,
                    "code": "active_batch_lease",
                    "scan": _scan_to_dict(row),
                    "cleared": [],
                }
            if str(row.status) == "failed":
                return {
                    "ok": False,
                    "code": "scan_failed",
                    "scan": _scan_to_dict(row),
                    "cleared": [],
                }
            if str(row.status) not in allowed_statuses:
                return {
                    "ok": False,
                    "code": "resume_not_applicable",
                    "scan": _scan_to_dict(row),
                    "cleared": [],
                }

            symbol_rows = list(
                session.execute(
                    select(TwResearchSymbolResult).where(
                        TwResearchSymbolResult.scan_id == scan_id
                    )
                ).scalars()
            )
            cleared: list[str] = []
            for symbol_row in symbol_rows:
                payload = dict(symbol_row.payload_json or {})
                if payload.get("rejection_reason") != rejection_reason:
                    continue
                cleared.append(symbol_row.stock_id)
                session.delete(symbol_row)

            previous_max = int(row.max_upstream_month_fetches)
            new_max = min(int(max_cap), previous_max + int(additional_month_fetches))
            stock_ids = list(row.stock_ids_json or [])
            # After deletes, first gap is the resume watermark.
            session.flush()
            new_cursor = _first_missing_symbol_index(session, scan_id, stock_ids)
            # Early verified/completed snapshots may still need a reopen when
            # symbols remain unprocessed. Active partial/running scans already
            # claim that work — do not stack duplicate budget windows there.
            reopen_terminal_with_gap = (
                str(row.status) in {"verified_ready", "completed_processing"}
                and new_cursor < len(stock_ids)
            )
            if (
                str(row.status) != "budget_exhausted"
                and not cleared
                and not reopen_terminal_with_gap
            ):
                return {
                    "ok": False,
                    "code": "resume_not_applicable",
                    "scan": _scan_to_dict(row),
                    "cleared": [],
                }
            if new_max <= previous_max and not cleared and not reopen_terminal_with_gap:
                return {
                    "ok": False,
                    "code": "budget_cap_reached",
                    "scan": _scan_to_dict(row),
                    "cleared": [],
                }
            row.max_upstream_month_fetches = new_max
            row.budget_windows_opened = int(
                getattr(row, "budget_windows_opened", 0) or 0
            ) + 1
            row.cursor = new_cursor
            row.reserve_cursor = new_cursor
            row.reserve_expires_at = None
            if new_cursor >= len(stock_ids):
                row.status = "completed_processing"
            else:
                row.status = "partial"
            notes = list(row.notes_json or [])
            notes.append(
                "budget_window_opened:"
                f"+{new_max - previous_max}:max={new_max}:cleared={len(cleared)}"
            )
            row.notes_json = notes[-50:]
            row.updated_at = now
            session.commit()
            return {
                "ok": True,
                "code": "resumed",
                "scan": _scan_to_dict(row),
                "cleared": cleared,
                "previous_max": previous_max,
                "new_max": new_max,
            }

    def resume_enrichment_window(
        self,
        scan_id: str,
        *,
        additional_enrichments: int,
        max_cap: int,
    ) -> dict[str, Any] | None:
        """Atomically open another bounded enrichment window (#330).

        Raises ``max_enrichments_per_run`` by ``additional_enrichments`` up to
        ``max_cap`` when persisted qualified symbols still lack enrichment.
        Rejects while an enrichment lease is held so concurrent workers cannot
        double-grant windows.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchScan)
                .where(TwResearchScan.scan_id == scan_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            if str(row.status) == "failed":
                return {
                    "ok": False,
                    "code": "scan_failed",
                    "scan": _scan_to_dict(row),
                }
            lease_until = _as_utc(getattr(row, "enrich_reserve_expires_at", None))
            reserved = list(getattr(row, "enrich_reserve_ids_json", None) or [])
            if lease_until is not None and lease_until > now and reserved:
                return {
                    "ok": False,
                    "code": "active_enrichment_lease",
                    "scan": _scan_to_dict(row),
                }
            pending = _unenriched_qualified_ids(session, row)
            if not pending:
                return {
                    "ok": False,
                    "code": "enrichment_complete",
                    "scan": _scan_to_dict(row),
                }
            previous_max = int(row.max_enrichments_per_run)
            remaining = max(0, previous_max - int(row.enrichments_run))
            if remaining > 0:
                return {
                    "ok": False,
                    "code": "enrichment_budget_remaining",
                    "scan": _scan_to_dict(row),
                }
            new_max = min(int(max_cap), previous_max + int(additional_enrichments))
            if new_max <= previous_max:
                return {
                    "ok": False,
                    "code": "enrichment_cap_reached",
                    "scan": _scan_to_dict(row),
                }
            row.max_enrichments_per_run = new_max
            notes = list(row.notes_json or [])
            notes.append(
                "enrichment_window_opened:"
                f"+{new_max - previous_max}:max={new_max}:pending={len(pending)}"
            )
            row.notes_json = notes[-50:]
            row.updated_at = now
            session.commit()
            return {
                "ok": True,
                "code": "resumed",
                "scan": _scan_to_dict(row),
                "previous_max": previous_max,
                "new_max": new_max,
                "pending": len(pending),
            }

    def acquire_history_flight(
        self,
        *,
        flight_key: str,
        stock_id: str,
        compatibility_key: str,
        owner_token: str,
        lease_seconds: int = HISTORY_FLIGHT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Claim or observe a cross-pod history backfill lease (#329).

        Returns ``status`` in {acquired, busy, ready}. Transactions are short —
        callers must never hold this lock while performing upstream HTTP.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchHistoryFlight)
                .where(TwResearchHistoryFlight.flight_key == flight_key)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                try:
                    with session.begin_nested():
                        session.add(
                            TwResearchHistoryFlight(
                                flight_key=flight_key,
                                stock_id=stock_id,
                                compatibility_key=compatibility_key,
                                owner_token=owner_token,
                                generation=1,
                                state="held",
                                lease_expires_at=now
                                + timedelta(seconds=max(1, int(lease_seconds))),
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        session.flush()
                except IntegrityError:
                    # Concurrent first insert won; lock and inspect the winner.
                    session.expire_all()
                    row = session.execute(
                        select(TwResearchHistoryFlight)
                        .where(TwResearchHistoryFlight.flight_key == flight_key)
                        .with_for_update()
                    ).scalar_one_or_none()
                    if row is None:
                        raise
                else:
                    session.commit()
                    return {
                        "status": "acquired",
                        "generation": 1,
                        "owner_token": owner_token,
                    }

            lease_until = _as_utc(row.lease_expires_at)
            if row.state == "ready" and (
                lease_until is None or lease_until > now
            ):
                # Ready marker stays briefly so waiters can reuse committed bars.
                session.commit()
                return {
                    "status": "ready",
                    "generation": int(row.generation),
                    "owner_token": row.owner_token,
                }

            held_active = (
                row.state == "held"
                and lease_until is not None
                and lease_until > now
                and row.owner_token != owner_token
            )
            if held_active:
                session.commit()
                return {
                    "status": "busy",
                    "generation": int(row.generation),
                    "owner_token": row.owner_token,
                    "lease_expires_at": lease_until,
                }

            generation = int(row.generation) + 1
            row.generation = generation
            row.owner_token = owner_token
            row.compatibility_key = compatibility_key
            row.stock_id = stock_id
            row.state = "held"
            row.lease_expires_at = now + timedelta(
                seconds=max(1, int(lease_seconds))
            )
            row.updated_at = now
            session.commit()
            return {
                "status": "acquired",
                "generation": generation,
                "owner_token": owner_token,
            }

    def renew_history_flight(
        self,
        *,
        flight_key: str,
        owner_token: str,
        generation: int,
        lease_seconds: int = HISTORY_FLIGHT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Extend a held history-flight lease; stale owners fail closed."""
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchHistoryFlight)
                .where(TwResearchHistoryFlight.flight_key == flight_key)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return {"ok": False, "stale": True}
            if (
                row.state != "held"
                or row.owner_token != owner_token
                or int(row.generation) != int(generation)
            ):
                session.commit()
                return {"ok": False, "stale": True}
            now = datetime.now(timezone.utc)
            row.lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
            row.updated_at = now
            session.commit()
            return {"ok": True, "stale": False, "lease_expires_at": row.lease_expires_at}

    def complete_history_flight(
        self,
        *,
        flight_key: str,
        owner_token: str,
        generation: int,
        success: bool,
        coverage_complete: bool = False,
        ready_ttl_seconds: int = 30,
    ) -> dict[str, Any]:
        """Release a history flight with fencing; stale owners are ignored.

        ``ready`` is set only when the leader reports both fetch success and
        validated store coverage. Partial success clears the hold without a
        misleading ready marker so waiters re-coordinate before more upstream.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchHistoryFlight)
                .where(TwResearchHistoryFlight.flight_key == flight_key)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return {"ok": False, "stale": True}
            if row.owner_token != owner_token or int(row.generation) != int(generation):
                session.commit()
                return {"ok": False, "stale": True}
            now = datetime.now(timezone.utc)
            if success and coverage_complete:
                row.state = "ready"
                row.lease_expires_at = now + timedelta(
                    seconds=max(1, int(ready_ttl_seconds))
                )
            elif success:
                # Fetch ran but required coverage is still incomplete.
                row.state = "incomplete"
                row.lease_expires_at = now
            else:
                row.state = "failed"
                row.lease_expires_at = now
            row.updated_at = now
            session.commit()
            return {"ok": True, "stale": False, "state": row.state}

    def find_compatible_scan(
        self,
        *,
        user_id: str,
        trading_day: str,
        universe_version: str,
        calculation_version: str,
        selector_mode: str,
        target_count: int,
    ) -> dict[str, Any] | None:
        """Return the newest non-failed compatible scan for schedule reuse (#330)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchScan)
                .where(
                    TwResearchScan.user_id == user_id,
                    TwResearchScan.trading_day == trading_day,
                    TwResearchScan.universe_version == universe_version,
                    TwResearchScan.calculation_version == calculation_version,
                    TwResearchScan.target_count == target_count,
                    TwResearchScan.status != "failed",
                )
                .order_by(TwResearchScan.updated_at.desc(), TwResearchScan.scan_id.desc())
                .limit(20)
            ).scalars().all()
            for row in rows:
                selector = dict(row.selector_json or {})
                if selector.get("mode") != selector_mode:
                    continue
                return _scan_to_dict(row)
            return None

    def find_incomplete_backlog_scans(
        self,
        *,
        user_id: str,
        universe_version: str,
        calculation_version: str,
        selector_mode: str,
        target_count: int,
        min_trading_day: str,
        max_trading_day: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Incomplete compatible scans in ``[min_trading_day, max_trading_day]`` (#330).

        Ordered oldest trading day first so yesterday finishes before today starts.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchScan)
                .where(
                    TwResearchScan.user_id == user_id,
                    TwResearchScan.universe_version == universe_version,
                    TwResearchScan.calculation_version == calculation_version,
                    TwResearchScan.target_count == target_count,
                    TwResearchScan.trading_day >= min_trading_day,
                    TwResearchScan.trading_day <= max_trading_day,
                    TwResearchScan.status.in_(tuple(INCOMPLETE_SCHEDULE_STATUSES)),
                )
                .order_by(TwResearchScan.trading_day.asc(), TwResearchScan.updated_at.asc())
                .limit(limit)
            ).scalars().all()
            out: list[dict[str, Any]] = []
            for row in rows:
                selector = dict(row.selector_json or {})
                if selector.get("mode") != selector_mode:
                    continue
                # Include verified_ready rows: enrichment may still be pending after
                # an early immutable snapshot. Callers filter with
                # schedule_has_remaining_work after hydrate.
                out.append(_scan_to_dict(row))
            return out

    def claim_schedule_tick_lock(
        self,
        *,
        lock_name: str,
        owner_token: str,
        lease_seconds: int = SCHEDULE_TICK_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Bounded schedule-tick lock so duplicate CronJob pods do not overlap (#330)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            _ensure_coordination_lock(session, lock_name)
            row = session.execute(
                select(TwResearchCoordinationLock)
                .where(TwResearchCoordinationLock.lock_name == lock_name)
                .with_for_update()
            ).scalar_one()
            lease_until = _as_utc(row.lease_expires_at)
            if (
                lease_until is not None
                and lease_until > now
                and row.holder_token
                and row.holder_token != owner_token
            ):
                session.commit()
                return {
                    "acquired": False,
                    "holder_token": row.holder_token,
                    "lease_expires_at": lease_until,
                }
            row.holder_token = owner_token
            row.lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
            row.updated_at = now
            session.commit()
            return {"acquired": True, "holder_token": owner_token}

    def renew_schedule_tick_lock(
        self,
        *,
        lock_name: str,
        owner_token: str,
        lease_seconds: int = SCHEDULE_TICK_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Keep tick ownership alive across bounded batch windows (#330)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchCoordinationLock)
                .where(TwResearchCoordinationLock.lock_name == lock_name)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None or row.holder_token != owner_token:
                if row is not None:
                    session.commit()
                return {"ok": False, "stale": True}
            now = datetime.now(timezone.utc)
            row.lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
            row.updated_at = now
            session.commit()
            return {"ok": True, "stale": False}

    def release_schedule_tick_lock(
        self,
        *,
        lock_name: str,
        owner_token: str,
    ) -> None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchCoordinationLock)
                .where(TwResearchCoordinationLock.lock_name == lock_name)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return
            if row.holder_token == owner_token:
                row.holder_token = None
                row.lease_expires_at = None
                row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def insert_export_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_factory = self._session_factory()
        with session_factory() as session:
            existing_stmt = select(TwResearchExportSnapshot).where(
                TwResearchExportSnapshot.content_digest == payload["content_digest"]
            )
            if payload.get("user_id") is None:
                existing_stmt = existing_stmt.where(TwResearchExportSnapshot.user_id.is_(None))
            else:
                existing_stmt = existing_stmt.where(
                    TwResearchExportSnapshot.user_id == payload["user_id"]
                )
            existing = session.execute(existing_stmt).scalar_one_or_none()
            if existing is not None:
                return _snapshot_to_dict(existing)
            row = TwResearchExportSnapshot(**payload)
            session.add(row)
            session.commit()
            session.refresh(row)
            return _snapshot_to_dict(row)

    def get_export_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchExportSnapshot, snapshot_id)
            if row is None:
                return None
            return _snapshot_to_dict(row)

    def latest_verified_snapshot(
        self,
        *,
        universe_version: str | None = None,
        trading_day: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            stmt = select(TwResearchExportSnapshot).where(
                TwResearchExportSnapshot.quality_status == "verified_ready"
            )
            if universe_version:
                stmt = stmt.where(TwResearchExportSnapshot.universe_version == universe_version)
            if trading_day:
                stmt = stmt.where(TwResearchExportSnapshot.trading_day == trading_day)
            if user_id:
                stmt = stmt.where(TwResearchExportSnapshot.user_id == user_id)
            stmt = stmt.order_by(TwResearchExportSnapshot.finalized_at.desc())
            row = session.execute(stmt).scalars().first()
            if row is None:
                return None
            return _snapshot_to_dict(row)

    def delete_snapshots_older_than(self, cutoff: datetime) -> int:
        """Delete immutable snapshots older than cutoff (independent of scans)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchExportSnapshot).where(
                    TwResearchExportSnapshot.finalized_at < cutoff
                )
            ).scalars()
            count = 0
            for row in list(rows):
                session.delete(row)
                count += 1
            session.commit()
            return count

    def delete_scans_older_than(self, cutoff: datetime) -> int:
        """Delete scan rows older than cutoff.

        Verified snapshots survive via ON DELETE SET NULL on ``scan_id``.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchScan).where(TwResearchScan.updated_at < cutoff)
            ).scalars()
            count = 0
            for row in list(rows):
                session.delete(row)
                count += 1
            session.commit()
            return count

    # --- Membership / first-pass / candidate sets (#335/#336) ---

    def get_membership(self, user_id: str) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchMembership, user_id)
            if row is None:
                return None
            return _membership_to_dict(row)

    def save_membership(
        self,
        user_id: str,
        *,
        expected_version: int,
        theme_keys: list[str],
        extra_stock_ids: list[str],
        compute_subset_ids: list[str],
        disabled_theme_keys: list[str] | None = None,
        sector_additions: dict[str, list[str]] | None = None,
        sector_removals: dict[str, list[str]] | None = None,
        custom_sectors: list[dict[str, Any]] | None = None,
        base_preset_version: str = "starter",
    ) -> dict[str, Any]:
        """DB-level optimistic concurrency via UPDATE … WHERE version = expected."""
        now = datetime.now(timezone.utc)
        disabled = list(disabled_theme_keys or [])
        additions = dict(sector_additions or {})
        removals = dict(sector_removals or {})
        customs = list(custom_sectors or [])
        session_factory = self._session_factory()
        with session_factory() as session:
            if expected_version == 0:
                row = TwResearchMembership(
                    user_id=user_id,
                    version=1,
                    base_preset_version=base_preset_version,
                    theme_keys_json=list(theme_keys),
                    disabled_theme_keys_json=disabled,
                    sector_additions_json=additions,
                    sector_removals_json=removals,
                    custom_sectors_json=customs,
                    extra_stock_ids_json=list(extra_stock_ids),
                    compute_subset_ids_json=list(compute_subset_ids),
                    updated_at=now,
                )
                try:
                    with session.begin_nested():
                        session.add(row)
                        session.flush()
                except IntegrityError as exc:
                    raise ValueError("membership_version_mismatch") from exc
                session.commit()
                session.refresh(row)
                return _membership_to_dict(row)

            result = session.execute(
                update(TwResearchMembership)
                .where(
                    TwResearchMembership.user_id == user_id,
                    TwResearchMembership.version == expected_version,
                )
                .values(
                    version=expected_version + 1,
                    base_preset_version=base_preset_version,
                    theme_keys_json=list(theme_keys),
                    disabled_theme_keys_json=disabled,
                    sector_additions_json=additions,
                    sector_removals_json=removals,
                    custom_sectors_json=customs,
                    extra_stock_ids_json=list(extra_stock_ids),
                    compute_subset_ids_json=list(compute_subset_ids),
                    updated_at=now,
                )
            )
            if int(result.rowcount or 0) != 1:
                session.rollback()
                raise ValueError("membership_version_mismatch")
            session.commit()
            row = session.get(TwResearchMembership, user_id)
            if row is None:
                raise ValueError("membership_version_mismatch")
            return _membership_to_dict(row)

    def save_first_pass_snapshot(
        self,
        *,
        snapshot_id: str,
        user_id: str,
        universe_version: str,
        first_pass_version: str,
        content_digest: str,
        payload: dict[str, Any],
        stock_ids: list[str],
    ) -> dict[str, Any]:
        """Idempotent by (user_id, content_digest). Returns existing row on digest hit."""
        now = datetime.now(timezone.utc)
        session_factory = self._session_factory()
        with session_factory() as session:
            existing = session.execute(
                select(TwResearchFirstPassSnapshot).where(
                    TwResearchFirstPassSnapshot.user_id == user_id,
                    TwResearchFirstPassSnapshot.content_digest == content_digest,
                )
            ).scalars().first()
            if existing is not None:
                return _first_pass_snapshot_to_dict(existing)
            row = TwResearchFirstPassSnapshot(
                snapshot_id=snapshot_id,
                user_id=user_id,
                universe_version=universe_version,
                first_pass_version=first_pass_version,
                content_digest=content_digest,
                payload_json=payload,
                stock_ids_json=list(stock_ids),
                created_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError:
                # Concurrent same-user digest hit, or rare PK race — never publish
                # a partial row; re-read the owner's digest match.
                existing = session.execute(
                    select(TwResearchFirstPassSnapshot).where(
                        TwResearchFirstPassSnapshot.user_id == user_id,
                        TwResearchFirstPassSnapshot.content_digest == content_digest,
                    )
                ).scalars().first()
                if existing is not None:
                    return _first_pass_snapshot_to_dict(existing)
                by_pk = session.get(TwResearchFirstPassSnapshot, snapshot_id)
                if by_pk is not None and by_pk.user_id == user_id:
                    return _first_pass_snapshot_to_dict(by_pk)
                raise
            session.commit()
            session.refresh(row)
            return _first_pass_snapshot_to_dict(row)

    def get_first_pass_snapshot(
        self, snapshot_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchFirstPassSnapshot, snapshot_id)
            if row is None:
                return None
            if user_id is not None and row.user_id != user_id:
                return None
            return _first_pass_snapshot_to_dict(row)

    def save_candidate_set(
        self,
        *,
        candidate_set_id: str,
        user_id: str,
        expected_version: int,
        first_pass_snapshot_id: str,
        first_pass_digest: str,
        stock_ids: list[str],
        origin: str,
        selection_reasons: dict[str, Any],
        decision_date: str | None,
        notes: list[str],
    ) -> dict[str, Any]:
        """Append a new candidate-set version under row lock + unique(version)."""
        now = datetime.now(timezone.utc)
        session_factory = self._session_factory()
        with session_factory() as session:
            # Lock existing rows for this user so concurrent appends serialize.
            locked = session.execute(
                select(TwResearchCandidateSet)
                .where(TwResearchCandidateSet.user_id == user_id)
                .order_by(TwResearchCandidateSet.version.desc())
                .with_for_update()
            ).scalars().first()
            current_version = int(locked.version) if locked is not None else 0
            if current_version != expected_version:
                raise ValueError("candidate_set_version_mismatch")
            snap = session.get(TwResearchFirstPassSnapshot, first_pass_snapshot_id)
            if snap is None or snap.user_id != user_id:
                raise KeyError("first_pass_snapshot_not_found")
            if snap.content_digest != first_pass_digest:
                raise ValueError("first_pass_digest_mismatch")
            row = TwResearchCandidateSet(
                candidate_set_id=candidate_set_id,
                user_id=user_id,
                version=current_version + 1,
                first_pass_snapshot_id=first_pass_snapshot_id,
                first_pass_digest=first_pass_digest,
                stock_ids_json=list(stock_ids),
                origin=origin,
                selection_reasons_json=dict(selection_reasons or {}),
                decision_date=decision_date,
                notes_json=list(notes),
                created_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError as exc:
                raise ValueError("candidate_set_version_mismatch") from exc
            session.commit()
            session.refresh(row)
            return _candidate_set_to_dict(row)

    def latest_candidate_set(self, user_id: str) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchCandidateSet)
                .where(TwResearchCandidateSet.user_id == user_id)
                .order_by(TwResearchCandidateSet.version.desc())
                .limit(1)
            ).scalars().first()
            if row is None:
                return None
            return _candidate_set_to_dict(row)

    def get_candidate_set(
        self,
        user_id: str,
        *,
        candidate_set_id: str,
        version: int | None = None,
    ) -> dict[str, Any] | None:
        """Load a specific candidate-set version for the owning user."""
        session_factory = self._session_factory()
        with session_factory() as session:
            stmt = select(TwResearchCandidateSet).where(
                TwResearchCandidateSet.user_id == user_id,
                TwResearchCandidateSet.candidate_set_id == candidate_set_id,
            )
            if version is not None:
                stmt = stmt.where(TwResearchCandidateSet.version == int(version))
            else:
                stmt = stmt.order_by(TwResearchCandidateSet.version.desc())
            row = session.execute(stmt.limit(1)).scalars().first()
            if row is None:
                return None
            return _candidate_set_to_dict(row)

    def find_matching_candidate_scan(
        self,
        *,
        user_id: str,
        candidate_set_id: str,
        candidate_set_version: int,
        trading_day: str,
        calculation_version: str,
        daily_override_ids: list[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        """Return an existing non-failed scan matching the candidate technical key."""
        wanted_overrides = sorted(
            {str(item).upper() for item in (daily_override_ids or []) if item}
        )
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchScan)
                .where(
                    TwResearchScan.user_id == user_id,
                    TwResearchScan.trading_day == trading_day,
                    TwResearchScan.calculation_version == calculation_version,
                    TwResearchScan.status.notin_(("failed",)),
                )
                .order_by(TwResearchScan.updated_at.desc(), TwResearchScan.scan_id.desc())
                .limit(limit)
            ).scalars().all()
            for row in rows:
                selector = dict(row.selector_json or {})
                provenance = selector.get("candidateProvenance")
                if not isinstance(provenance, dict):
                    continue
                if str(provenance.get("candidateSetId") or "") != str(candidate_set_id):
                    continue
                if int(provenance.get("candidateSetVersion") or -1) != int(
                    candidate_set_version
                ):
                    continue
                overrides = sorted(
                    {
                        str(item).upper()
                        for item in (provenance.get("dailyOverrideIds") or [])
                        if item
                    }
                )
                if overrides != wanted_overrides:
                    continue
                return _scan_to_dict(row)
            return None

    # --- Manual research-run orchestration (#341) ---

    def get_research_run(self, user_id: str) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchResearchRun, user_id)
            if row is None:
                return None
            return _research_run_to_dict(row)

    def upsert_research_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """OCC upsert: bump revision when ``WHERE revision = expected`` matches."""
        from app.services.tw_research.research_run import ResearchRunConflict

        session_factory = self._session_factory()
        with session_factory() as session:
            user_id = str(payload["user_id"])
            expected_revision = int(payload.get("revision") or 0)
            now = datetime.now(timezone.utc)
            row = session.get(TwResearchResearchRun, user_id)
            if row is None:
                row = TwResearchResearchRun(
                    user_id=user_id,
                    research_run_id=str(payload["research_run_id"]),
                    version=str(payload.get("version") or "research-run-v1"),
                    phase=str(payload["phase"]),
                    status=str(payload["status"]),
                    trading_day=payload.get("trading_day"),
                    first_pass_job_id=payload.get("first_pass_job_id"),
                    first_pass_snapshot_id=payload.get("first_pass_snapshot_id"),
                    candidate_set_id=payload.get("candidate_set_id"),
                    candidate_set_version=payload.get("candidate_set_version"),
                    technical_scan_id=payload.get("technical_scan_id"),
                    gates_json=dict(payload.get("gates") or {}),
                    dimensions_json=dict(payload.get("dimensions") or {}),
                    notes_json=list(payload.get("notes") or [])[-50:],
                    schedule_enabled=bool(payload.get("schedule_enabled")),
                    revision=1,
                    job_claim_token=payload.get("job_claim_token"),
                    job_claim_expires_at=_as_utc_iso(payload.get("job_claim_expires_at")),
                    pending_first_pass_job_id=payload.get("pending_first_pass_job_id"),
                    phase2_claim_token=payload.get("phase2_claim_token"),
                    phase2_claim_expires_at=_as_utc_iso(
                        payload.get("phase2_claim_expires_at")
                    ),
                    pending_technical_scan_id=payload.get("pending_technical_scan_id"),
                    created_at=now,
                    updated_at=now,
                )
                try:
                    with session.begin_nested():
                        session.add(row)
                        session.flush()
                except IntegrityError:
                    session.expire_all()
                    existing = session.get(TwResearchResearchRun, user_id)
                    if existing is None:
                        raise ResearchRunConflict() from None
                    raise ResearchRunConflict() from None
            else:
                updated = session.execute(
                    update(TwResearchResearchRun)
                    .where(
                        TwResearchResearchRun.user_id == user_id,
                        TwResearchResearchRun.revision == expected_revision,
                    )
                    .values(
                        research_run_id=str(payload["research_run_id"]),
                        version=str(payload.get("version") or row.version),
                        phase=str(payload["phase"]),
                        status=str(payload["status"]),
                        trading_day=payload.get("trading_day"),
                        first_pass_job_id=payload.get("first_pass_job_id"),
                        first_pass_snapshot_id=payload.get("first_pass_snapshot_id"),
                        candidate_set_id=payload.get("candidate_set_id"),
                        candidate_set_version=payload.get("candidate_set_version"),
                        technical_scan_id=payload.get("technical_scan_id"),
                        gates_json=dict(payload.get("gates") or {}),
                        dimensions_json=dict(payload.get("dimensions") or {}),
                        notes_json=list(payload.get("notes") or [])[-50:],
                        schedule_enabled=bool(payload.get("schedule_enabled")),
                        revision=expected_revision + 1,
                        job_claim_token=payload.get("job_claim_token"),
                        job_claim_expires_at=_as_utc_iso(
                            payload.get("job_claim_expires_at")
                        ),
                        pending_first_pass_job_id=payload.get(
                            "pending_first_pass_job_id"
                        ),
                        phase2_claim_token=payload.get("phase2_claim_token"),
                        phase2_claim_expires_at=_as_utc_iso(
                            payload.get("phase2_claim_expires_at")
                        ),
                        pending_technical_scan_id=payload.get(
                            "pending_technical_scan_id"
                        ),
                        updated_at=now,
                    )
                )
                if int(updated.rowcount or 0) != 1:
                    raise ResearchRunConflict()
                session.refresh(row)
            session.commit()
            session.refresh(row)
            return _research_run_to_dict(row)

    def delete_research_run(self, user_id: str) -> None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchResearchRun, user_id)
            if row is not None:
                session.delete(row)
                session.commit()

    def claim_research_run_job_slot(
        self,
        user_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        """Atomically claim the right to create/attach a first-pass job (#341 P1-1).

        Uses ``SELECT … FOR UPDATE`` — not process-local locks. Expired claims
        are recoverable; an already-attached job short-circuits creation.
        """
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchResearchRun)
                .where(TwResearchResearchRun.user_id == user_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("research_run_not_found")
            if row.first_pass_job_id:
                session.commit()
                return {
                    "outcome": "already_attached",
                    "run": _research_run_to_dict(row),
                    "first_pass_job_id": row.first_pass_job_id,
                }
            expires = _as_utc(row.job_claim_expires_at)
            held_by_other = (
                row.job_claim_token
                and row.job_claim_token != claim_token
                and expires is not None
                and expires > now
            )
            if held_by_other:
                session.commit()
                return {
                    "outcome": "claim_held",
                    "run": _research_run_to_dict(row),
                    "first_pass_job_id": None,
                }
            # Own claim, expired claim, or free slot → take/renew ownership.
            row.job_claim_token = claim_token
            row.job_claim_expires_at = now + timedelta(seconds=int(lease_seconds))
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return {
                "outcome": "claimed",
                "run": _research_run_to_dict(row),
                "pending_first_pass_job_id": row.pending_first_pass_job_id,
                "first_pass_job_id": None,
            }

    def record_pending_first_pass_job(
        self,
        user_id: str,
        *,
        claim_token: str,
        job_id: str,
    ) -> dict[str, Any]:
        """Persist pending job id under an owned claim (crash between create+attach)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchResearchRun)
                .where(TwResearchResearchRun.user_id == user_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("research_run_not_found")
            if row.first_pass_job_id:
                session.commit()
                return {
                    "outcome": "already_attached",
                    "run": _research_run_to_dict(row),
                    "first_pass_job_id": row.first_pass_job_id,
                    "orphan_job_id": job_id
                    if str(row.first_pass_job_id) != str(job_id)
                    else None,
                }
            expires = _as_utc(row.job_claim_expires_at)
            if row.job_claim_token != claim_token or expires is None or expires <= now:
                session.commit()
                return {
                    "outcome": "claim_lost",
                    "run": _research_run_to_dict(row),
                    "orphan_job_id": job_id,
                }
            row.pending_first_pass_job_id = job_id
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return {
                "outcome": "recorded",
                "run": _research_run_to_dict(row),
                "pending_first_pass_job_id": job_id,
            }

    def attach_first_pass_job_atomic(
        self,
        user_id: str,
        *,
        claim_token: str,
        job_id: str,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Attach job id and clear the claim under ``FOR UPDATE``."""
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchResearchRun)
                .where(TwResearchResearchRun.user_id == user_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("research_run_not_found")
            if row.first_pass_job_id:
                session.commit()
                return {
                    "outcome": "already_attached",
                    "run": _research_run_to_dict(row),
                    "first_pass_job_id": row.first_pass_job_id,
                    "orphan_job_id": job_id
                    if str(row.first_pass_job_id) != str(job_id)
                    else None,
                }
            expires = _as_utc(row.job_claim_expires_at)
            owns = (
                row.job_claim_token == claim_token
                and expires is not None
                and expires > now
            )
            pending_ok = row.pending_first_pass_job_id in {None, job_id}
            if not owns or not pending_ok:
                session.commit()
                return {
                    "outcome": "claim_lost",
                    "run": _research_run_to_dict(row),
                    "orphan_job_id": job_id,
                }
            row.first_pass_job_id = job_id
            row.pending_first_pass_job_id = None
            row.job_claim_token = None
            row.job_claim_expires_at = None
            row.status = "running"
            row.phase = "phase1_first_pass"
            dims = dict(row.dimensions_json or {})
            dims["fundamentals"] = "in_progress"
            row.dimensions_json = dims
            note_list = list(row.notes_json or [])
            for note in notes or []:
                note_list.append(note)
            row.notes_json = note_list[-50:]
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return {
                "outcome": "attached",
                "run": _research_run_to_dict(row),
                "first_pass_job_id": job_id,
            }

    def mark_first_pass_job_orphaned(
        self,
        job_id: str,
        *,
        user_id: str,
        authoritative_job_id: str | None,
        reason: str,
    ) -> None:
        """Explicitly mark a non-authoritative job so it is not silently abandoned."""
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchFirstPassJob)
                .where(
                    TwResearchFirstPassJob.job_id == job_id,
                    TwResearchFirstPassJob.user_id == user_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return
            if row.status in {"completed", "partial"} and row.active_snapshot_id:
                # Preserve completed evidence; only annotate.
                notes = list(row.notes_json or [])
                notes.append(
                    f"orphan_note:{reason}:authoritative={authoritative_job_id or 'none'}"
                )
                row.notes_json = notes[-50:]
            else:
                row.status = "failed"
                notes = list(row.notes_json or [])
                notes.append(
                    f"orphaned_superseded:{reason}:authoritative={authoritative_job_id or 'none'}"
                )
                row.notes_json = notes[-50:]
                row.quality_status = "incomplete"
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def claim_research_run_phase2_slot(
        self,
        user_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        """Atomically claim the right to create/attach a technical scan (#341 P1-2)."""
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchResearchRun)
                .where(TwResearchResearchRun.user_id == user_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("research_run_not_found")
            gates = dict(row.gates_json or {})
            if gates.get("candidateSelection") != "approved":
                session.commit()
                return {
                    "outcome": "gate_not_approved",
                    "run": _research_run_to_dict(row),
                }
            if row.technical_scan_id:
                session.commit()
                return {
                    "outcome": "already_attached",
                    "run": _research_run_to_dict(row),
                    "technical_scan_id": row.technical_scan_id,
                }
            expires = _as_utc(row.phase2_claim_expires_at)
            held_by_other = (
                row.phase2_claim_token
                and row.phase2_claim_token != claim_token
                and expires is not None
                and expires > now
            )
            if held_by_other:
                session.commit()
                return {
                    "outcome": "claim_held",
                    "run": _research_run_to_dict(row),
                }
            row.phase2_claim_token = claim_token
            row.phase2_claim_expires_at = now + timedelta(seconds=int(lease_seconds))
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return {
                "outcome": "claimed",
                "run": _research_run_to_dict(row),
                "pending_technical_scan_id": row.pending_technical_scan_id,
            }

    def record_pending_technical_scan(
        self,
        user_id: str,
        *,
        claim_token: str,
        scan_id: str,
    ) -> dict[str, Any]:
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchResearchRun)
                .where(TwResearchResearchRun.user_id == user_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("research_run_not_found")
            if row.technical_scan_id:
                session.commit()
                return {
                    "outcome": "already_attached",
                    "run": _research_run_to_dict(row),
                    "technical_scan_id": row.technical_scan_id,
                }
            expires = _as_utc(row.phase2_claim_expires_at)
            if (
                row.phase2_claim_token != claim_token
                or expires is None
                or expires <= now
            ):
                session.commit()
                return {
                    "outcome": "claim_lost",
                    "run": _research_run_to_dict(row),
                }
            row.pending_technical_scan_id = scan_id
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return {
                "outcome": "recorded",
                "run": _research_run_to_dict(row),
                "pending_technical_scan_id": scan_id,
            }

    def attach_technical_scan_atomic(
        self,
        user_id: str,
        *,
        claim_token: str,
        scan_id: str,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchResearchRun)
                .where(TwResearchResearchRun.user_id == user_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("research_run_not_found")
            if row.technical_scan_id:
                session.commit()
                return {
                    "outcome": "already_attached",
                    "run": _research_run_to_dict(row),
                    "technical_scan_id": row.technical_scan_id,
                }
            expires = _as_utc(row.phase2_claim_expires_at)
            owns = (
                row.phase2_claim_token == claim_token
                and expires is not None
                and expires > now
            )
            if not owns:
                session.commit()
                return {
                    "outcome": "claim_lost",
                    "run": _research_run_to_dict(row),
                }
            gates = dict(row.gates_json or {})
            if gates.get("candidateSelection") != "approved":
                session.commit()
                return {
                    "outcome": "gate_not_approved",
                    "run": _research_run_to_dict(row),
                }
            row.technical_scan_id = scan_id
            row.pending_technical_scan_id = None
            row.phase2_claim_token = None
            row.phase2_claim_expires_at = None
            row.phase = "phase2_technical"
            row.status = "running"
            dims = dict(row.dimensions_json or {})
            dims["monthlyTechnical"] = "in_progress"
            dims["dailyTechnical"] = "pending_monthly_gate"
            row.dimensions_json = dims
            note_list = list(row.notes_json or [])
            for note in list(notes or []) + [
                "Phase-2 monthly-first technical started from approved candidates only.",
                "Stops for analyst report decision; no Notion write.",
            ]:
                note_list.append(note)
            row.notes_json = note_list[-50:]
            row.revision = int(row.revision or 0) + 1
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return {
                "outcome": "attached",
                "run": _research_run_to_dict(row),
                "technical_scan_id": scan_id,
            }

    # --- First-pass resumable jobs (#335) ---

    def create_first_pass_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = TwResearchFirstPassJob(
                job_id=payload["job_id"],
                user_id=payload["user_id"],
                universe_version=payload["universe_version"],
                universe_digest=payload["universe_digest"],
                membership_version=int(payload.get("membership_version") or 0),
                first_pass_version=payload["first_pass_version"],
                stock_ids_json=list(payload["stock_ids"]),
                selector_json=dict(payload.get("selector") or {}),
                status=payload.get("status") or "pending",
                quality_status=payload.get("quality_status") or "incomplete",
                cursor=0,
                reserve_cursor=0,
                notes_json=list(payload.get("notes") or []),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _first_pass_job_to_dict(row)

    def get_first_pass_job(
        self, job_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchFirstPassJob, job_id)
            if row is None:
                return None
            if user_id is not None and row.user_id != user_id:
                return None
            return _first_pass_job_to_dict(row)

    def list_first_pass_jobs(self, user_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchFirstPassJob)
                .where(TwResearchFirstPassJob.user_id == user_id)
                .order_by(TwResearchFirstPassJob.updated_at.desc())
                .limit(limit)
            ).scalars().all()
            return [_first_pass_job_to_dict(row) for row in rows]

    def claim_first_pass_batch(
        self,
        job_id: str,
        *,
        batch_size: int,
        lease_seconds: int = BATCH_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        """Lease the next batch of unfinished first-pass symbols."""
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchFirstPassJob)
                .where(TwResearchFirstPassJob.job_id == job_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            stock_ids = list(row.stock_ids_json or [])
            lease_until = _as_utc(row.reserve_expires_at)
            cursor = int(row.cursor or 0)
            reserve_cursor = int(row.reserve_cursor or 0)

            if lease_until is not None and lease_until <= now:
                cursor = _first_missing_first_pass_index(session, job_id, stock_ids)
                row.cursor = cursor
                row.reserve_cursor = cursor
                row.reserve_expires_at = None
                reserve_cursor = cursor
                lease_until = None

            own_lease_active = (
                lease_until is not None
                and lease_until > now
                and reserve_cursor > cursor
            )
            if own_lease_active:
                session.commit()
                return {
                    "batch_ids": [],
                    "job": _first_pass_job_to_dict(row),
                    "already_complete": False,
                    "lease_held": True,
                    "lease_generation": int(row.lease_generation or 0),
                }

            cursor = _first_missing_first_pass_index(session, job_id, stock_ids)
            row.cursor = cursor
            if cursor >= len(stock_ids):
                cooling = _first_pass_cooling_retry_count(session, job_id, stock_ids)
                row.reserve_cursor = cursor
                row.reserve_expires_at = None
                if cooling:
                    # Failures still in backoff — do not finalize or claim complete.
                    row.status = "deferred"
                    session.commit()
                    return {
                        "batch_ids": [],
                        "job": _first_pass_job_to_dict(row),
                        "already_complete": False,
                        "waiting_for_retry": True,
                        "lease_held": False,
                        "lease_generation": int(row.lease_generation or 0),
                    }
                # Match complete_first_pass_batch: exhausted symbol failures ⇒ partial.
                row.status = _resolve_first_pass_terminal_status(session, job_id)
                session.commit()
                return {
                    "batch_ids": [],
                    "job": _first_pass_job_to_dict(row),
                    "already_complete": True,
                    "waiting_for_retry": False,
                    "lease_held": False,
                    "lease_generation": int(row.lease_generation or 0),
                }

            if row.status in {"pending", "deferred", "partial"}:
                row.status = "running"
            # Claim contiguous unfinished symbols starting at cursor, skipping any
            # that became non-actionable between index scans (cooling retries).
            batch_ids: list[str] = []
            by_stock = {
                item.stock_id: item
                for item in session.execute(
                    select(TwResearchFirstPassSymbolResult).where(
                        TwResearchFirstPassSymbolResult.job_id == job_id
                    )
                ).scalars().all()
            }
            for stock_id in stock_ids[cursor:]:
                if not _first_pass_symbol_needs_work(by_stock.get(stock_id), now=now):
                    continue
                batch_ids.append(stock_id)
                if len(batch_ids) >= max(1, int(batch_size)):
                    break
            if not batch_ids:
                cooling = _first_pass_cooling_retry_count(session, job_id, stock_ids)
                if cooling:
                    row.status = "deferred"
                else:
                    row.status = _resolve_first_pass_terminal_status(session, job_id)
                row.reserve_cursor = cursor
                row.reserve_expires_at = None
                session.commit()
                return {
                    "batch_ids": [],
                    "job": _first_pass_job_to_dict(row),
                    "already_complete": not cooling,
                    "waiting_for_retry": bool(cooling),
                    "lease_held": False,
                    "lease_generation": int(row.lease_generation or 0),
                }
            generation = int(row.lease_generation or 0) + 1
            row.lease_generation = generation
            claimed = set(batch_ids)
            last_claimed_index = cursor
            for index, stock_id in enumerate(stock_ids):
                if stock_id in claimed:
                    last_claimed_index = index
            row.reserve_cursor = last_claimed_index + 1
            row.reserve_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
            row.updated_at = now
            session.commit()
            return {
                "batch_ids": batch_ids,
                "job": _first_pass_job_to_dict(row),
                "already_complete": False,
                "lease_held": False,
                "lease_generation": generation,
            }

    def get_first_pass_symbol(
        self, job_id: str, stock_id: str
    ) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.execute(
                select(TwResearchFirstPassSymbolResult).where(
                    TwResearchFirstPassSymbolResult.job_id == job_id,
                    TwResearchFirstPassSymbolResult.stock_id == stock_id,
                )
            ).scalars().first()
            if row is None:
                return None
            return {
                "stock_id": row.stock_id,
                "status": row.status,
                "payload": dict(row.payload_json or {}),
                "updated_at": row.updated_at.isoformat()
                if hasattr(row.updated_at, "isoformat")
                else str(row.updated_at),
            }

    def upsert_first_pass_symbol(
        self,
        *,
        job_id: str,
        stock_id: str,
        status: str,
        payload: dict[str, Any],
        lease_generation: int,
    ) -> bool:
        """Persist one symbol result if lease generation is still current."""
        session_factory = self._session_factory()
        with session_factory() as session:
            job = session.execute(
                select(TwResearchFirstPassJob)
                .where(TwResearchFirstPassJob.job_id == job_id)
                .with_for_update()
            ).scalar_one_or_none()
            if job is None:
                return False
            if int(job.lease_generation or 0) != int(lease_generation):
                return False
            existing = session.execute(
                select(TwResearchFirstPassSymbolResult).where(
                    TwResearchFirstPassSymbolResult.job_id == job_id,
                    TwResearchFirstPassSymbolResult.stock_id == stock_id,
                )
            ).scalars().first()
            now = datetime.now(timezone.utc)
            if existing is None:
                session.add(
                    TwResearchFirstPassSymbolResult(
                        job_id=job_id,
                        stock_id=stock_id,
                        status=status,
                        payload_json=payload,
                        lease_generation=lease_generation,
                        updated_at=now,
                    )
                )
            else:
                existing.status = status
                existing.payload_json = payload
                existing.lease_generation = lease_generation
                existing.updated_at = now
            session.commit()
            return True

    def complete_first_pass_batch(
        self,
        job_id: str,
        *,
        lease_generation: int,
        processed_count: int,
    ) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            now = datetime.now(timezone.utc)
            row = session.execute(
                select(TwResearchFirstPassJob)
                .where(TwResearchFirstPassJob.job_id == job_id)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            if int(row.lease_generation or 0) != int(lease_generation):
                return None
            stock_ids = list(row.stock_ids_json or [])
            cursor = _first_missing_first_pass_index(session, job_id, stock_ids)
            row.cursor = cursor
            row.reserve_cursor = cursor
            row.reserve_expires_at = None
            row.updated_at = now
            if cursor >= len(stock_ids):
                cooling = _first_pass_cooling_retry_count(session, job_id, stock_ids)
                if cooling:
                    row.status = "deferred"
                else:
                    row.status = _resolve_first_pass_terminal_status(session, job_id)
            else:
                row.status = "running"
            session.commit()
            return {
                "job": _first_pass_job_to_dict(row),
                "processed_count": processed_count,
            }

    def list_first_pass_symbols(
        self,
        job_id: str,
        *,
        status: str | None = None,
        q: str | None = None,
        completeness: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        session_factory = self._session_factory()
        with session_factory() as session:
            stmt = select(TwResearchFirstPassSymbolResult).where(
                TwResearchFirstPassSymbolResult.job_id == job_id
            )
            if status:
                stmt = stmt.where(TwResearchFirstPassSymbolResult.status == status)
            rows = session.execute(
                stmt.order_by(TwResearchFirstPassSymbolResult.stock_id)
            ).scalars().all()
            matched = [
                row
                for row in rows
                if _first_pass_symbol_matches_filters(
                    row,
                    q=q,
                    completeness=completeness,
                )
            ]
            start = max(0, offset)
            end = start + max(1, min(limit, 200))
            page = matched[start:end]
            return [
                {
                    "stock_id": row.stock_id,
                    "status": row.status,
                    "payload": dict(row.payload_json or {}),
                    "updated_at": row.updated_at.isoformat()
                    if hasattr(row.updated_at, "isoformat")
                    else str(row.updated_at),
                }
                for row in page
            ]

    def count_first_pass_symbols_matching(
        self,
        job_id: str,
        *,
        status: str | None = None,
        q: str | None = None,
        completeness: str | None = None,
    ) -> int:
        session_factory = self._session_factory()
        with session_factory() as session:
            stmt = select(TwResearchFirstPassSymbolResult).where(
                TwResearchFirstPassSymbolResult.job_id == job_id
            )
            if status:
                stmt = stmt.where(TwResearchFirstPassSymbolResult.status == status)
            rows = session.execute(stmt).scalars().all()
            return sum(
                1
                for row in rows
                if _first_pass_symbol_matches_filters(
                    row,
                    q=q,
                    completeness=completeness,
                )
            )

    def count_first_pass_symbols(self, job_id: str) -> dict[str, int]:
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(
                    TwResearchFirstPassSymbolResult.status,
                    func.count(),
                )
                .where(TwResearchFirstPassSymbolResult.job_id == job_id)
                .group_by(TwResearchFirstPassSymbolResult.status)
            ).all()
            counts = {str(status): int(count) for status, count in rows}
            total = sum(counts.values())
            return {
                "total": total,
                "ok": counts.get("ok", 0),
                "partial": counts.get("partial", 0),
                "failed": counts.get("failed", 0),
                "deferred": counts.get("deferred", 0),
                "pending": counts.get("pending", 0),
            }

    def set_first_pass_job_snapshot(
        self, job_id: str, *, snapshot_id: str, quality_status: str
    ) -> dict[str, Any] | None:
        session_factory = self._session_factory()
        with session_factory() as session:
            row = session.get(TwResearchFirstPassJob, job_id)
            if row is None:
                return None
            row.active_snapshot_id = snapshot_id
            row.quality_status = quality_status
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _first_pass_job_to_dict(row)

    def list_first_pass_snapshot_payloads(
        self, job_id: str
    ) -> list[dict[str, Any]]:
        session_factory = self._session_factory()
        with session_factory() as session:
            rows = session.execute(
                select(TwResearchFirstPassSymbolResult)
                .where(TwResearchFirstPassSymbolResult.job_id == job_id)
                .order_by(TwResearchFirstPassSymbolResult.stock_id)
            ).scalars().all()
            return [dict(row.payload_json or {}) for row in rows]


def _membership_to_dict(row: TwResearchMembership) -> dict[str, Any]:
    return {
        "user_id": row.user_id,
        "version": int(row.version),
        "base_preset_version": getattr(row, "base_preset_version", None) or "starter",
        "theme_keys": list(row.theme_keys_json or []),
        "disabled_theme_keys": list(getattr(row, "disabled_theme_keys_json", None) or []),
        "sector_additions": dict(getattr(row, "sector_additions_json", None) or {}),
        "sector_removals": dict(getattr(row, "sector_removals_json", None) or {}),
        "custom_sectors": list(getattr(row, "custom_sectors_json", None) or []),
        "extra_stock_ids": list(row.extra_stock_ids_json or []),
        "compute_subset_ids": list(row.compute_subset_ids_json or []),
        "updated_at": row.updated_at.isoformat()
        if hasattr(row.updated_at, "isoformat")
        else str(row.updated_at),
    }


def _first_pass_job_to_dict(row: TwResearchFirstPassJob) -> dict[str, Any]:
    stock_ids = list(row.stock_ids_json or [])
    return {
        "job_id": row.job_id,
        "user_id": row.user_id,
        "universe_version": row.universe_version,
        "universe_digest": row.universe_digest,
        "membership_version": int(row.membership_version or 0),
        "first_pass_version": row.first_pass_version,
        "stock_ids": stock_ids,
        "universe_size": len(stock_ids),
        "selector": dict(row.selector_json or {}),
        "status": row.status,
        "quality_status": row.quality_status,
        "cursor": int(row.cursor or 0),
        "reserve_cursor": int(row.reserve_cursor or 0),
        "lease_generation": int(row.lease_generation or 0),
        "active_snapshot_id": row.active_snapshot_id,
        "notes": list(row.notes_json or []),
        "created_at": row.created_at.isoformat()
        if hasattr(row.created_at, "isoformat")
        else str(row.created_at),
        "updated_at": row.updated_at.isoformat()
        if hasattr(row.updated_at, "isoformat")
        else str(row.updated_at),
    }


def _parse_iso_utc(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_pass_symbol_matches_filters(
    row: TwResearchFirstPassSymbolResult,
    *,
    q: str | None = None,
    completeness: str | None = None,
) -> bool:
    """Server-side evidence search across the job (bounded in-process filter)."""
    payload = dict(row.payload_json or {})
    needle = str(q or "").strip().lower()
    if needle:
        stock_id = str(row.stock_id or "").lower()
        name = str(payload.get("stockName") or "").lower()
        sectors = " ".join(str(item).lower() for item in (payload.get("sectorThemeKeys") or []))
        status = str(row.status or "").lower()
        haystack = f"{stock_id} {name} {sectors} {status}"
        if needle not in haystack and needle not in stock_id.replace("tw:", ""):
            return False
    wanted = str(completeness or "").strip().lower()
    if wanted:
        completeness_payload = dict(payload.get("completeness") or {})
        fields_ok = int(completeness_payload.get("fieldsOk") or 0)
        fields_unavailable = int(completeness_payload.get("fieldsUnavailable") or 0)
        if wanted in {"complete", "ok"} and fields_ok <= 0:
            return False
        if wanted in {"incomplete", "partial"} and fields_unavailable <= 0 and str(row.status) == "ok":
            return False
        if wanted == "failed" and str(row.status) != "failed":
            return False
    return True


def _first_pass_symbol_needs_work(
    row: TwResearchFirstPassSymbolResult | None,
    *,
    now: datetime,
) -> bool:
    """True when the symbol is missing or has a retryable durable failure (#335)."""
    if row is None:
        return True
    status = str(row.status or "")
    if status in {"ok", "partial", "pending"}:
        # partial = usable incomplete evidence (missing PEG etc.); do not refetch.
        # pending rows are claimed placeholders and should not be re-queued here.
        return status == "pending"
    if status == "deferred":
        payload = dict(row.payload_json or {})
        if payload.get("retryExhausted"):
            return False
        next_retry = _parse_iso_utc(payload.get("nextRetryAt"))
        return next_retry is None or next_retry <= now
    if status == "failed":
        payload = dict(row.payload_json or {})
        if payload.get("retryExhausted"):
            return False
        if str(payload.get("failureClass") or "") == "permanent":
            return False
        next_retry = _parse_iso_utc(payload.get("nextRetryAt"))
        return next_retry is None or next_retry <= now
    return True


def _first_missing_first_pass_index(
    session: Session, job_id: str, stock_ids: list[str]
) -> int:
    """Next index that is missing or eligible for bounded retry (#335)."""
    if not stock_ids:
        return 0
    rows = session.execute(
        select(TwResearchFirstPassSymbolResult).where(
            TwResearchFirstPassSymbolResult.job_id == job_id
        )
    ).scalars().all()
    by_stock = {row.stock_id: row for row in rows}
    now = datetime.now(timezone.utc)
    for index, stock_id in enumerate(stock_ids):
        if _first_pass_symbol_needs_work(by_stock.get(stock_id), now=now):
            return index
    return len(stock_ids)


def _first_pass_cooling_retry_count(
    session: Session, job_id: str, stock_ids: list[str]
) -> int:
    """Count failed/deferred symbols waiting for nextRetryAt (#335)."""
    if not stock_ids:
        return 0
    wanted = set(stock_ids)
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(TwResearchFirstPassSymbolResult).where(
            TwResearchFirstPassSymbolResult.job_id == job_id
        )
    ).scalars().all()
    cooling = 0
    for row in rows:
        if row.stock_id not in wanted:
            continue
        if str(row.status) not in {"failed", "deferred"}:
            continue
        payload = dict(row.payload_json or {})
        if payload.get("retryExhausted"):
            continue
        if str(payload.get("failureClass") or "") == "permanent":
            continue
        next_retry = _parse_iso_utc(payload.get("nextRetryAt"))
        if next_retry is not None and next_retry > now:
            cooling += 1
    return cooling


def _count_first_pass_status(session: Session, job_id: str, status: str) -> int:
    total = session.execute(
        select(func.count())
        .select_from(TwResearchFirstPassSymbolResult)
        .where(
            TwResearchFirstPassSymbolResult.job_id == job_id,
            TwResearchFirstPassSymbolResult.status == status,
        )
    ).scalar_one()
    return int(total or 0)


def _resolve_first_pass_terminal_status(session: Session, job_id: str) -> str:
    """Terminal job status when no symbols remain actionable (#335/#336).

    Exhausted symbol failures stay ``partial`` — never silently upgraded to
    ``completed`` on claim/reclaim after ``complete_first_pass_batch``.
    """
    failed = _count_first_pass_status(session, job_id, "failed")
    deferred = _count_first_pass_status(session, job_id, "deferred")
    if failed:
        return "partial"
    if deferred:
        return "deferred"
    return "completed"



def _first_pass_snapshot_to_dict(row: TwResearchFirstPassSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": row.snapshot_id,
        "user_id": row.user_id,
        "universe_version": row.universe_version,
        "first_pass_version": row.first_pass_version,
        "content_digest": row.content_digest,
        "payload": dict(row.payload_json or {}),
        "stock_ids": list(row.stock_ids_json or []),
        "created_at": row.created_at.isoformat()
        if hasattr(row.created_at, "isoformat")
        else str(row.created_at),
    }


def _candidate_set_to_dict(row: TwResearchCandidateSet) -> dict[str, Any]:
    return {
        "candidate_set_id": row.candidate_set_id,
        "user_id": row.user_id,
        "version": int(row.version),
        "first_pass_snapshot_id": row.first_pass_snapshot_id,
        "first_pass_digest": row.first_pass_digest,
        "stock_ids": list(row.stock_ids_json or []),
        "origin": row.origin,
        "selection_reasons": dict(row.selection_reasons_json or {}),
        "decision_date": row.decision_date,
        "notes": list(row.notes_json or []),
        "created_at": row.created_at.isoformat()
        if hasattr(row.created_at, "isoformat")
        else str(row.created_at),
    }


def _research_run_to_dict(row: TwResearchResearchRun) -> dict[str, Any]:
    def _iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    return {
        "user_id": row.user_id,
        "research_run_id": row.research_run_id,
        "version": row.version,
        "phase": row.phase,
        "status": row.status,
        "trading_day": row.trading_day,
        "first_pass_job_id": row.first_pass_job_id,
        "first_pass_snapshot_id": row.first_pass_snapshot_id,
        "candidate_set_id": row.candidate_set_id,
        "candidate_set_version": row.candidate_set_version,
        "technical_scan_id": row.technical_scan_id,
        "gates": dict(row.gates_json or {}),
        "dimensions": dict(row.dimensions_json or {}),
        "notes": list(row.notes_json or []),
        "schedule_enabled": bool(row.schedule_enabled),
        "revision": int(getattr(row, "revision", 0) or 0),
        "job_claim_token": getattr(row, "job_claim_token", None),
        "job_claim_expires_at": _iso(getattr(row, "job_claim_expires_at", None)),
        "pending_first_pass_job_id": getattr(row, "pending_first_pass_job_id", None),
        "phase2_claim_token": getattr(row, "phase2_claim_token", None),
        "phase2_claim_expires_at": _iso(getattr(row, "phase2_claim_expires_at", None)),
        "pending_technical_scan_id": getattr(row, "pending_technical_scan_id", None),
        "created_at": row.created_at.isoformat()
        if hasattr(row.created_at, "isoformat")
        else str(row.created_at),
        "updated_at": row.updated_at.isoformat()
        if hasattr(row.updated_at, "isoformat")
        else str(row.updated_at),
    }


tw_research_repository = TwResearchRepository()
