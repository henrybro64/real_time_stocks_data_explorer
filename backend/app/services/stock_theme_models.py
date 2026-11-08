"""Shared theme dataclasses used by stock themes and TW research presets.

Kept in a leaf module so ``stock_theme_service`` and ``tw_research.preset`` can
both import these types without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeDefinition:
    key: str
    label: str


@dataclass(frozen=True)
class ThemeAssignment:
    stock_id: str
    theme_keys: tuple[str, ...]
