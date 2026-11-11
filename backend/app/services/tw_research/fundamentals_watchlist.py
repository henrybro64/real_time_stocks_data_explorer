"""Versioned research membership + analyst compute-subset retention (#336).

Supports editable per-sector additions/removals on top of the starter preset
without mutating the global preset. Production path is PostgreSQL fail-closed
with DB-level optimistic concurrency (UPDATE … WHERE version = expected).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal

from collections.abc import Callable

from app.models.stock import StockRecord
from app.repositories.tw_research_repository import tw_research_repository
from app.services.tw_research.preset import (
    TW_RESEARCH_THEME_ASSIGNMENTS,
    TW_RESEARCH_THEME_DEFINITIONS,
    starter_preset_theme_keys,
)
from app.services.tw_research.universe import resolve_universe, UniverseSelector

WatchlistConflictReason = Literal["version_mismatch", "unknown_state"]
StockResolver = Callable[[str], StockRecord | None]


class ResearchMembershipUnavailable(RuntimeError):
    def __init__(self, message: str = "research_membership_store_unavailable") -> None:
        super().__init__(message)
        self.operation = "research_membership"


@dataclass(frozen=True)
class ResearchMembershipSnapshot:
    user_id: str
    version: int
    theme_keys: tuple[str, ...]
    disabled_theme_keys: tuple[str, ...]
    sector_additions: dict[str, tuple[str, ...]]
    sector_removals: dict[str, tuple[str, ...]]
    custom_sectors: tuple[dict[str, Any], ...]
    extra_stock_ids: tuple[str, ...]
    compute_subset_ids: tuple[str, ...]
    resolved_stock_ids: tuple[str, ...]
    sector_membership: dict[str, tuple[str, ...]]
    universe_version: str
    universe_digest: str
    base_preset_version: str
    updated_at: str
    notes: tuple[str, ...] = ()
    durable: bool = True
    preview: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "userId": payload["user_id"],
            "version": payload["version"],
            "basePresetVersion": payload["base_preset_version"],
            "themeKeys": list(payload["theme_keys"]),
            "disabledThemeKeys": list(payload["disabled_theme_keys"]),
            "sectorAdditions": {
                key: list(values) for key, values in payload["sector_additions"].items()
            },
            "sectorRemovals": {
                key: list(values) for key, values in payload["sector_removals"].items()
            },
            "customSectors": list(payload["custom_sectors"]),
            "extraStockIds": list(payload["extra_stock_ids"]),
            "computeSubsetIds": list(payload["compute_subset_ids"]),
            "resolvedStockIds": list(payload["resolved_stock_ids"]),
            "sectorMembership": {
                key: list(values) for key, values in payload["sector_membership"].items()
            },
            "universeVersion": payload["universe_version"],
            "universeDigest": payload["universe_digest"],
            "updatedAt": payload["updated_at"],
            "notes": list(payload["notes"]),
            "durable": payload["durable"],
            "preview": payload["preview"],
            "notNotionReportList": True,
        }


@dataclass
class _StoredMembership:
    version: int = 0
    theme_keys: tuple[str, ...] = ()
    disabled_theme_keys: tuple[str, ...] = ()
    sector_additions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    sector_removals: dict[str, tuple[str, ...]] = field(default_factory=dict)
    custom_sectors: tuple[dict[str, Any], ...] = ()
    extra_stock_ids: tuple[str, ...] = ()
    compute_subset_ids: tuple[str, ...] = ()
    base_preset_version: str = "starter"
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def _normalize_sector_map(
    raw: dict[str, list[str] | tuple[str, ...]] | None,
) -> dict[str, tuple[str, ...]]:
    if not raw:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, values in raw.items():
        theme = str(key).strip()
        if not theme:
            continue
        out[theme] = _dedupe_preserve(values)
    return out


def _digest_for(stock_ids: tuple[str, ...], version: int) -> str:
    import hashlib
    import json

    raw = json.dumps(
        {"version": version, "stockIds": list(stock_ids)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_sector_membership(
    stored: _StoredMembership,
    *,
    stock_resolver: StockResolver | None,
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], list[str]]:
    """Build per-sector membership and deduped universe without mutating preset."""
    notes: list[str] = []
    known_themes = {theme.key for theme in TW_RESEARCH_THEME_DEFINITIONS}
    for custom in stored.custom_sectors:
        key = str(custom.get("key") or "").strip()
        if key:
            known_themes.add(key)

    enabled_themes = stored.theme_keys or starter_preset_theme_keys()
    enabled = tuple(
        theme
        for theme in enabled_themes
        if theme not in stored.disabled_theme_keys and theme in known_themes
    )
    if not enabled and not stored.extra_stock_ids and not stored.sector_additions:
        enabled = starter_preset_theme_keys()
        notes.append("No sectors selected; using starter preset themes as template.")

    # Base membership from immutable preset assignments.
    sector_map: dict[str, list[str]] = {theme: [] for theme in enabled}
    for assignment in TW_RESEARCH_THEME_ASSIGNMENTS:
        for theme in assignment.theme_keys:
            if theme in sector_map:
                sector_map[theme].append(assignment.stock_id)

    # Apply per-sector removals then additions.
    for theme, removals in stored.sector_removals.items():
        if theme not in sector_map:
            continue
        remove_set = set(removals)
        sector_map[theme] = [sid for sid in sector_map[theme] if sid not in remove_set]

    for theme, additions in stored.sector_additions.items():
        if theme not in known_themes:
            notes.append(f"ignored_unknown_sector:{theme}")
            continue
        if theme not in sector_map and theme not in stored.disabled_theme_keys:
            sector_map[theme] = []
        if theme in sector_map:
            sector_map[theme].extend(list(additions))

    # Validate against catalog when resolver provided.
    validated: dict[str, tuple[str, ...]] = {}
    rejected: list[str] = []
    for theme, ids in sector_map.items():
        cleaned: list[str] = []
        for sid in _dedupe_preserve(ids):
            if stock_resolver is not None and stock_resolver(sid) is None:
                rejected.append(sid)
                continue
            cleaned.append(sid)
        validated[theme] = tuple(cleaned)

    extras = list(stored.extra_stock_ids)
    if stock_resolver is not None:
        valid_extras = []
        for sid in extras:
            if stock_resolver(sid) is None:
                rejected.append(sid)
            else:
                valid_extras.append(sid)
        extras = valid_extras

    ordered: list[str] = []
    seen: set[str] = set()
    for theme in sorted(validated.keys()):
        for sid in validated[theme]:
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
    for sid in _dedupe_preserve(extras):
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)

    if rejected:
        notes.append(f"catalog_rejected:{','.join(sorted(set(rejected))[:12])}")
    notes.append("Global starter preset assignments were not mutated.")
    return validated, tuple(ordered), notes


class ResearchMembershipStore:
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
        stored = self._load(user_id)
        return self._snapshot_from_stored(user_id, stored, durable=self._persist)

    def preview(
        self,
        user_id: str,
        *,
        theme_keys: list[str] | tuple[str, ...] | None = None,
        disabled_theme_keys: list[str] | tuple[str, ...] | None = None,
        sector_additions: dict[str, list[str]] | None = None,
        sector_removals: dict[str, list[str]] | None = None,
        custom_sectors: list[dict[str, Any]] | None = None,
        extra_stock_ids: list[str] | tuple[str, ...] | None = None,
        compute_subset_ids: list[str] | tuple[str, ...] | None = None,
    ) -> ResearchMembershipSnapshot:
        current = self._load(user_id)
        draft = _StoredMembership(
            version=current.version,
            theme_keys=_dedupe_preserve(theme_keys)
            if theme_keys is not None
            else current.theme_keys,
            disabled_theme_keys=_dedupe_preserve(disabled_theme_keys)
            if disabled_theme_keys is not None
            else current.disabled_theme_keys,
            sector_additions=_normalize_sector_map(sector_additions)
            if sector_additions is not None
            else current.sector_additions,
            sector_removals=_normalize_sector_map(sector_removals)
            if sector_removals is not None
            else current.sector_removals,
            custom_sectors=tuple(custom_sectors)
            if custom_sectors is not None
            else current.custom_sectors,
            extra_stock_ids=_dedupe_preserve(extra_stock_ids)
            if extra_stock_ids is not None
            else current.extra_stock_ids,
            compute_subset_ids=_dedupe_preserve(compute_subset_ids)
            if compute_subset_ids is not None
            else current.compute_subset_ids,
            base_preset_version=current.base_preset_version,
            updated_at=current.updated_at,
        )
        snap = self._snapshot_from_stored(user_id, draft, durable=False, preview=True)
        return snap

    def save(
        self,
        user_id: str,
        *,
        expected_version: int,
        theme_keys: list[str] | tuple[str, ...] | None = None,
        disabled_theme_keys: list[str] | tuple[str, ...] | None = None,
        sector_additions: dict[str, list[str]] | None = None,
        sector_removals: dict[str, list[str]] | None = None,
        custom_sectors: list[dict[str, Any]] | None = None,
        extra_stock_ids: list[str] | tuple[str, ...] | None = None,
        compute_subset_ids: list[str] | tuple[str, ...] | None = None,
        reset_to_starter: bool = False,
    ) -> ResearchMembershipSnapshot:
        current = self._load(user_id)
        if current.version != expected_version:
            raise ResearchMembershipConflict(
                reason="version_mismatch",
                current_version=current.version,
                expected_version=expected_version,
            )

        if reset_to_starter:
            next_state = _StoredMembership(
                version=expected_version + 1,
                base_preset_version="starter",
                updated_at=datetime.now(UTC).isoformat(),
            )
        else:
            next_state = _StoredMembership(
                version=expected_version + 1,
                theme_keys=_dedupe_preserve(theme_keys)
                if theme_keys is not None
                else current.theme_keys,
                disabled_theme_keys=_dedupe_preserve(disabled_theme_keys)
                if disabled_theme_keys is not None
                else current.disabled_theme_keys,
                sector_additions=_normalize_sector_map(sector_additions)
                if sector_additions is not None
                else current.sector_additions,
                sector_removals=_normalize_sector_map(sector_removals)
                if sector_removals is not None
                else current.sector_removals,
                custom_sectors=tuple(custom_sectors)
                if custom_sectors is not None
                else current.custom_sectors,
                extra_stock_ids=_dedupe_preserve(extra_stock_ids)
                if extra_stock_ids is not None
                else current.extra_stock_ids,
                compute_subset_ids=_dedupe_preserve(compute_subset_ids)
                if compute_subset_ids is not None
                else current.compute_subset_ids,
                base_preset_version=current.base_preset_version or "starter",
                updated_at=datetime.now(UTC).isoformat(),
            )

        preview = self._snapshot_from_stored(user_id, next_state, durable=self._persist)
        next_state.compute_subset_ids = preview.compute_subset_ids

        if self._persist:
            if not self._repository.is_available():
                raise ResearchMembershipUnavailable()
            try:
                saved = self._repository.save_membership(
                    user_id,
                    expected_version=expected_version,
                    theme_keys=list(next_state.theme_keys),
                    extra_stock_ids=list(next_state.extra_stock_ids),
                    compute_subset_ids=list(next_state.compute_subset_ids),
                    disabled_theme_keys=list(next_state.disabled_theme_keys),
                    sector_additions={
                        key: list(vals) for key, vals in next_state.sector_additions.items()
                    },
                    sector_removals={
                        key: list(vals) for key, vals in next_state.sector_removals.items()
                    },
                    custom_sectors=list(next_state.custom_sectors),
                    base_preset_version=next_state.base_preset_version,
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
                self._stored_from_repo(saved),
                durable=True,
            )

        with self._lock:
            self._by_user[user_id] = next_state
            return self._snapshot_from_stored(user_id, next_state, durable=False)

    def reset(self, user_id: str, *, expected_version: int) -> ResearchMembershipSnapshot:
        return self.save(
            user_id,
            expected_version=expected_version,
            reset_to_starter=True,
        )

    def clear_for_tests(self) -> None:
        with self._lock:
            self._by_user.clear()

    def _load(self, user_id: str) -> _StoredMembership:
        if self._persist:
            if not self._repository.is_available():
                raise ResearchMembershipUnavailable()
            stored = self._repository.get_membership(user_id)
            if stored is None:
                return _StoredMembership()
            return self._stored_from_repo(stored)
        with self._lock:
            return self._by_user.get(user_id) or _StoredMembership()

    def _stored_from_repo(self, stored: dict[str, Any]) -> _StoredMembership:
        return _StoredMembership(
            version=int(stored["version"]),
            theme_keys=tuple(stored.get("theme_keys") or []),
            disabled_theme_keys=tuple(stored.get("disabled_theme_keys") or []),
            sector_additions=_normalize_sector_map(stored.get("sector_additions")),
            sector_removals=_normalize_sector_map(stored.get("sector_removals")),
            custom_sectors=tuple(stored.get("custom_sectors") or []),
            extra_stock_ids=tuple(stored.get("extra_stock_ids") or []),
            compute_subset_ids=tuple(stored.get("compute_subset_ids") or []),
            base_preset_version=str(stored.get("base_preset_version") or "starter"),
            updated_at=str(stored["updated_at"]),
        )

    def _snapshot_from_stored(
        self,
        user_id: str,
        stored: _StoredMembership,
        *,
        durable: bool,
        preview: bool = False,
    ) -> ResearchMembershipSnapshot:
        sector_membership, stock_ids, notes = _resolve_sector_membership(
            stored,
            stock_resolver=self._stock_resolver,
        )
        # Also allow pure stock_ids mode via resolve_universe for extras-only.
        if not stock_ids and stored.extra_stock_ids:
            resolved = resolve_universe(
                UniverseSelector(mode="stock_ids", stock_ids=stored.extra_stock_ids),
                **(
                    {"stock_resolver": self._stock_resolver}
                    if self._stock_resolver is not None
                    else {}
                ),
            )
            stock_ids = tuple(resolved.stock_ids)
            notes.extend(resolved.notes)

        allowed = set(stock_ids)
        subset = tuple(sid for sid in stored.compute_subset_ids if sid in allowed)
        universe_version = f"membership:{stored.version}:{_digest_for(stock_ids, stored.version)[:16]}"
        if durable:
            notes = list(notes) + ["durable_postgres"]
        elif preview:
            notes = list(notes) + ["preview_not_persisted"]
        else:
            notes = list(notes) + ["process_local_test_store"]

        return ResearchMembershipSnapshot(
            user_id=user_id,
            version=stored.version,
            theme_keys=stored.theme_keys,
            disabled_theme_keys=stored.disabled_theme_keys,
            sector_additions=dict(stored.sector_additions),
            sector_removals=dict(stored.sector_removals),
            custom_sectors=stored.custom_sectors,
            extra_stock_ids=stored.extra_stock_ids,
            compute_subset_ids=subset,
            resolved_stock_ids=stock_ids,
            sector_membership=sector_membership,
            universe_version=universe_version,
            universe_digest=_digest_for(stock_ids, stored.version),
            base_preset_version=stored.base_preset_version,
            updated_at=stored.updated_at,
            notes=tuple(notes),
            durable=durable,
            preview=preview,
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
        out.append(key)
    normalized: list[str] = []
    seen2: set[str] = set()
    for item in out:
        sid = item if item.startswith("TW:") else (f"TW:{item}" if item.isdigit() else item)
        if sid in seen2:
            continue
        seen2.add(sid)
        normalized.append(sid)
    return tuple(normalized)


research_membership_store = ResearchMembershipStore(persist=True)
