"""Versioned research membership + analyst compute-subset retention (#336).

Atomic, idempotent, optimistic-concurrency saves for:
- sector/theme membership overrides
- extra explicit stock IDs
- analyst-selected compute subset (symbols that may receive deeper work)

Production path is **PostgreSQL fail-closed** (no silent in-memory fallback).
``persist=False`` keeps a process-local store for deterministic unit tests only.

This is **research universe state**, not the personal Portfolio watchlist API.
Stock Ticker does not decide Notion report inclusion from this state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal

from collections.abc import Callable

from app.models.stock import StockRecord
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.universe import resolve_universe, UniverseSelector

WatchlistConflictReason = Literal["version_mismatch", "unknown_state"]
StockResolver = Callable[[str], StockRecord | None]


class ResearchMembershipUnavailable(RuntimeError):
    """Raised when durable membership storage is required but unavailable."""

    def __init__(self, message: str = "research_membership_store_unavailable") -> None:
        super().__init__(message)
        self.operation = "research_membership"


@dataclass(frozen=True)
class ResearchMembershipSnapshot:
    user_id: str
    version: int
    theme_keys: tuple[str, ...]
    extra_stock_ids: tuple[str, ...]
    compute_subset_ids: tuple[str, ...]
    resolved_stock_ids: tuple[str, ...]
    universe_version: str
    updated_at: str
    notes: tuple[str, ...] = ()
    durable: bool = True

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # camelCase for API consumers
        return {
            "userId": payload["user_id"],
            "version": payload["version"],
            "themeKeys": list(payload["theme_keys"]),
            "extraStockIds": list(payload["extra_stock_ids"]),
            "computeSubsetIds": list(payload["compute_subset_ids"]),
            "resolvedStockIds": list(payload["resolved_stock_ids"]),
            "universeVersion": payload["universe_version"],
            "updatedAt": payload["updated_at"],
            "notes": list(payload["notes"]),
            "durable": payload["durable"],
        }


@dataclass
class _StoredMembership:
    version: int = 0
    theme_keys: tuple[str, ...] = ()
    extra_stock_ids: tuple[str, ...] = ()
    compute_subset_ids: tuple[str, ...] = ()
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class ResearchMembershipStore:
    """Optimistic membership store.

    ``persist=True`` (default): PostgreSQL via ``tw_research_repository``.
    Fail closed when the durable store is unavailable.
    ``persist=False``: process-local dict for unit tests only.
    """

    def __init__(
        self,
        *,
        persist: bool = True,
        stock_resolver: StockResolver | None = None,
        repository: Any | None = None,
    ) -> None:
        self._persist = persist
        self._lock = Lock()
        self._by_user: dict[str, _StoredMembership] = {}
        self._stock_resolver = stock_resolver
        self._repository = repository or tw_research_repository

    def get(self, user_id: str) -> ResearchMembershipSnapshot:
        if self._persist:
            if not self._repository.is_available():
                raise ResearchMembershipUnavailable()
            stored = self._repository.get_membership(user_id)
            if stored is None:
                return self._snapshot_from_stored(user_id, _StoredMembership(), durable=True)
            return self._snapshot_from_stored(
                user_id,
                _StoredMembership(
                    version=int(stored["version"]),
                    theme_keys=tuple(stored["theme_keys"]),
                    extra_stock_ids=tuple(stored["extra_stock_ids"]),
                    compute_subset_ids=tuple(stored["compute_subset_ids"]),
                    updated_at=str(stored["updated_at"]),
                ),
                durable=True,
            )

        with self._lock:
            stored = self._by_user.get(user_id) or _StoredMembership()
            return self._snapshot_from_stored(user_id, stored, durable=False)

    def save(
        self,
        user_id: str,
        *,
        expected_version: int,
        theme_keys: list[str] | tuple[str, ...] | None = None,
        extra_stock_ids: list[str] | tuple[str, ...] | None = None,
        compute_subset_ids: list[str] | tuple[str, ...] | None = None,
        reset_to_starter: bool = False,
    ) -> ResearchMembershipSnapshot:
        if self._persist:
            if not self._repository.is_available():
                raise ResearchMembershipUnavailable()
            current = self.get(user_id)
            if current.version != expected_version:
                raise ResearchMembershipConflict(
                    reason="version_mismatch",
                    current_version=current.version,
                    expected_version=expected_version,
                )
            if reset_to_starter:
                next_theme: tuple[str, ...] = ()
                next_extra: tuple[str, ...] = ()
                next_subset: tuple[str, ...] = ()
            else:
                next_theme = (
                    _dedupe_preserve(theme_keys)
                    if theme_keys is not None
                    else current.theme_keys
                )
                next_extra = (
                    _dedupe_preserve(extra_stock_ids)
                    if extra_stock_ids is not None
                    else current.extra_stock_ids
                )
                next_subset = (
                    _dedupe_preserve(compute_subset_ids)
                    if compute_subset_ids is not None
                    else current.compute_subset_ids
                )
            # Resolve / validate before durable write so orphans never persist.
            preview = self._snapshot_from_stored(
                user_id,
                _StoredMembership(
                    version=expected_version + 1,
                    theme_keys=next_theme,
                    extra_stock_ids=next_extra,
                    compute_subset_ids=next_subset,
                ),
                durable=True,
            )
            try:
                saved = self._repository.save_membership(
                    user_id,
                    expected_version=expected_version,
                    theme_keys=list(next_theme),
                    extra_stock_ids=list(next_extra),
                    compute_subset_ids=list(preview.compute_subset_ids),
                )
            except ValueError as exc:
                if "version_mismatch" in str(exc):
                    raise ResearchMembershipConflict(
                        reason="version_mismatch",
                        current_version=self.get(user_id).version,
                        expected_version=expected_version,
                    ) from exc
                raise
            return self._snapshot_from_stored(
                user_id,
                _StoredMembership(
                    version=int(saved["version"]),
                    theme_keys=tuple(saved["theme_keys"]),
                    extra_stock_ids=tuple(saved["extra_stock_ids"]),
                    compute_subset_ids=tuple(saved["compute_subset_ids"]),
                    updated_at=str(saved["updated_at"]),
                ),
                durable=True,
            )

        with self._lock:
            current = self._by_user.get(user_id) or _StoredMembership()
            if current.version != expected_version:
                raise ResearchMembershipConflict(
                    reason="version_mismatch",
                    current_version=current.version,
                    expected_version=expected_version,
                )

            if reset_to_starter:
                next_state = _StoredMembership(
                    version=current.version + 1,
                    theme_keys=(),
                    extra_stock_ids=(),
                    compute_subset_ids=(),
                    updated_at=datetime.now(UTC).isoformat(),
                )
            else:
                next_theme = (
                    _dedupe_preserve(theme_keys)
                    if theme_keys is not None
                    else current.theme_keys
                )
                next_extra = (
                    _dedupe_preserve(extra_stock_ids)
                    if extra_stock_ids is not None
                    else current.extra_stock_ids
                )
                next_subset = (
                    _dedupe_preserve(compute_subset_ids)
                    if compute_subset_ids is not None
                    else current.compute_subset_ids
                )
                next_state = _StoredMembership(
                    version=current.version + 1,
                    theme_keys=next_theme,
                    extra_stock_ids=next_extra,
                    compute_subset_ids=next_subset,
                    updated_at=datetime.now(UTC).isoformat(),
                )

            snapshot = self._snapshot_from_stored(user_id, next_state, durable=False)
            # Persist validated subset (orphans already dropped in snapshot).
            next_state.compute_subset_ids = snapshot.compute_subset_ids
            self._by_user[user_id] = next_state
            return snapshot

    def reset(self, user_id: str, *, expected_version: int) -> ResearchMembershipSnapshot:
        return self.save(
            user_id,
            expected_version=expected_version,
            reset_to_starter=True,
        )

    def clear_for_tests(self) -> None:
        with self._lock:
            self._by_user.clear()

    def _snapshot_from_stored(
        self,
        user_id: str,
        stored: _StoredMembership,
        *,
        durable: bool,
    ) -> ResearchMembershipSnapshot:
        resolver_kwargs: dict[str, Any] = {}
        if self._stock_resolver is not None:
            resolver_kwargs["stock_resolver"] = self._stock_resolver

        if stored.theme_keys or stored.extra_stock_ids:
            if stored.theme_keys:
                resolved = resolve_universe(
                    UniverseSelector(
                        mode="theme_keys",
                        theme_keys=stored.theme_keys,
                        stock_ids=stored.extra_stock_ids,
                    ),
                    **resolver_kwargs,
                )
                extras = stored.extra_stock_ids
                if self._stock_resolver is not None:
                    extras = tuple(
                        sid
                        for sid in stored.extra_stock_ids
                        if self._stock_resolver(sid) is not None
                    )
                merged = _dedupe_preserve(list(resolved.stock_ids) + list(extras))
                universe_version = (
                    f"membership:{stored.version}:"
                    f"{resolved.universe_version}:{len(merged)}"
                )
                notes = list(resolved.notes) + [
                    "Membership is versioned research state, not Notion inclusion.",
                    "Duplicates across sectors are dropped before collection.",
                ]
                stock_ids = merged
            else:
                resolved = resolve_universe(
                    UniverseSelector(mode="stock_ids", stock_ids=stored.extra_stock_ids),
                    **resolver_kwargs,
                )
                stock_ids = resolved.stock_ids
                universe_version = f"membership:{stored.version}:{resolved.universe_version}"
                notes = list(resolved.notes)
        else:
            resolved = resolve_universe(
                UniverseSelector(mode="starter_preset"),
                **resolver_kwargs,
            )
            stock_ids = resolved.stock_ids
            universe_version = f"membership:{stored.version}:{resolved.universe_version}"
            notes = list(resolved.notes) + ["Using optional starter preset as template."]

        allowed = set(stock_ids)
        subset = tuple(sid for sid in stored.compute_subset_ids if sid in allowed)
        if durable:
            notes = list(notes) + ["durable_postgres"]
        else:
            notes = list(notes) + ["process_local_test_store"]

        return ResearchMembershipSnapshot(
            user_id=user_id,
            version=stored.version,
            theme_keys=stored.theme_keys,
            extra_stock_ids=stored.extra_stock_ids,
            compute_subset_ids=subset,
            resolved_stock_ids=tuple(stock_ids),
            universe_version=universe_version,
            updated_at=stored.updated_at,
            notes=tuple(notes),
            durable=durable,
        )


class ResearchMembershipConflict(Exception):
    def __init__(
        self,
        *,
        reason: WatchlistConflictReason,
        current_version: int,
        expected_version: int,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_version = current_version
        self.expected_version = expected_version


def _dedupe_preserve(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        key = str(raw).strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key if key.startswith("TW:") else key)
    normalized: list[str] = []
    seen2: set[str] = set()
    for item in out:
        sid = item if item.startswith("TW:") else (f"TW:{item}" if item.isdigit() else item)
        if sid in seen2:
            continue
        seen2.add(sid)
        normalized.append(sid)
    return tuple(normalized)


# Production singleton: durable PostgreSQL, fail closed when DB is down.
research_membership_store = ResearchMembershipStore(persist=True)
