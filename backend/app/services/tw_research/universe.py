"""Configurable Taiwan research universe selection (#310, #312).

Universe membership and sector/theme tags are metadata. Eligibility is catalog
validation for TWSE/TPEx symbols — never theme membership.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.models.stock import StockRecord
from app.services.quote_service import quote_service
from app.services.stock_theme_service import StockThemeService, stock_theme_service
from app.services.tw_research.constants import UNIVERSE_PRESET_VERSION
from app.services.tw_research.preset import (
    starter_preset_stock_ids,
    starter_preset_theme_keys,
)

StockResolver = Callable[[str], StockRecord | None]
UniverseMode = Literal["starter_preset", "theme_keys", "stock_ids"]


@dataclass(frozen=True)
class UniverseSelector:
    """How to build a scan universe.

    Exactly one of the modes should drive membership; extra fields are ignored
    for other modes but kept for request round-trips.
    """

    mode: UniverseMode = "starter_preset"
    theme_keys: tuple[str, ...] = ()
    stock_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RejectedStock:
    stock_id: str
    reason: str


@dataclass(frozen=True)
class ResolvedUniverse:
    universe_version: str
    selector: UniverseSelector
    stock_ids: tuple[str, ...]
    rejected: tuple[RejectedStock, ...] = ()
    theme_keys_applied: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class UniverseResolveStats:
    requested: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates_dropped: int = 0


def resolve_universe(
    selector: UniverseSelector,
    *,
    stock_resolver: StockResolver | None = None,
    theme_service: StockThemeService | None = None,
    universe_version: str | None = None,
) -> ResolvedUniverse:
    """Validate and deduplicate a configurable universe against the TW catalog."""
    resolver = stock_resolver or quote_service.get_catalog_stock
    themes = theme_service or stock_theme_service
    version = universe_version or _default_universe_version(selector)
    notes: list[str] = [
        "Theme membership is optional metadata and never an eligibility gate.",
        "Starter preset size is not a product maximum.",
    ]

    raw_ids, theme_keys_applied = _collect_raw_ids(selector, themes=themes, notes=notes)
    accepted, rejected, duplicates = _validate_and_dedupe(raw_ids, resolver)

    return ResolvedUniverse(
        universe_version=version,
        selector=selector,
        stock_ids=tuple(accepted),
        rejected=tuple(rejected),
        theme_keys_applied=tuple(theme_keys_applied),
        notes=tuple(notes),
    )


def _default_universe_version(selector: UniverseSelector) -> str:
    if selector.mode == "starter_preset":
        return UNIVERSE_PRESET_VERSION
    if selector.mode == "theme_keys":
        keys = ",".join(sorted(_normalize_keys(selector.theme_keys)))
        return f"themes:{keys or 'none'}"
    ids = ",".join(sorted({_normalize_stock_id(item) for item in selector.stock_ids if item}))
    digest = str(abs(hash(ids)) % (10**12))
    return f"explicit:{len(selector.stock_ids)}:{digest}"


def _collect_raw_ids(
    selector: UniverseSelector,
    *,
    themes: StockThemeService,
    notes: list[str],
) -> tuple[list[str], list[str]]:
    if selector.mode == "starter_preset":
        notes.append(f"Using optional starter preset {UNIVERSE_PRESET_VERSION}.")
        return list(starter_preset_stock_ids()), list(starter_preset_theme_keys())

    if selector.mode == "theme_keys":
        keys = _normalize_keys(selector.theme_keys)
        if not keys:
            raise ValueError("theme_keys mode requires at least one theme key")
        collected: list[str] = []
        applied: list[str] = []
        for key in keys:
            group = themes.get_stocks_for_theme(key)
            applied.append(group.theme.key)
            collected.extend(stock.id for stock in group.stocks)
        return collected, applied

    if selector.mode == "stock_ids":
        if not selector.stock_ids:
            raise ValueError("stock_ids mode requires at least one canonical stock id")
        return list(selector.stock_ids), []

    raise ValueError(f"Unknown universe mode: {selector.mode}")


def _validate_and_dedupe(
    raw_ids: Sequence[str],
    resolver: StockResolver,
) -> tuple[list[str], list[RejectedStock], int]:
    accepted: list[str] = []
    rejected: list[RejectedStock] = []
    seen: set[str] = set()
    duplicates = 0

    for raw in raw_ids:
        stock_id = _normalize_stock_id(raw)
        if not stock_id:
            rejected.append(RejectedStock(stock_id=raw, reason="empty_stock_id"))
            continue
        if stock_id in seen:
            duplicates += 1
            continue
        seen.add(stock_id)

        if not stock_id.startswith("TW:"):
            rejected.append(RejectedStock(stock_id=stock_id, reason="not_taiwan_market"))
            continue

        stock = resolver(stock_id)
        if stock is None:
            rejected.append(RejectedStock(stock_id=stock_id, reason="not_in_catalog"))
            continue
        if stock.market.upper() != "TW":
            rejected.append(RejectedStock(stock_id=stock_id, reason="not_taiwan_market"))
            continue
        if stock.exchange not in {"TWSE", "TPEx"}:
            rejected.append(
                RejectedStock(
                    stock_id=stock_id,
                    reason=f"unsupported_exchange:{stock.exchange}",
                )
            )
            continue

        accepted.append(stock_id)

    return accepted, rejected, duplicates


def _normalize_stock_id(raw: str) -> str:
    text = raw.strip().upper()
    if not text:
        return ""
    if text.isdigit():
        return f"TW:{text}"
    return text


def _normalize_keys(keys: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        normalized = key.strip().lower().replace("_", "-").replace(" ", "-")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out
