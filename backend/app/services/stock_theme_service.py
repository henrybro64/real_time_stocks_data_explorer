from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re

from app.models.stock import StockRecord
from app.services.quote_service import quote_service


StockLoader = Callable[[str], StockRecord | None]


@dataclass(frozen=True)
class ThemeDefinition:
    key: str
    label: str


@dataclass(frozen=True)
class ThemeAssignment:
    stock_id: str
    theme_keys: tuple[str, ...]


@dataclass(frozen=True)
class ThemeStockGroup:
    theme: ThemeDefinition
    stocks: tuple[StockRecord, ...]


DEFAULT_THEME_DEFINITIONS = (
    ThemeDefinition(key="ai-infrastructure", label="AI Infrastructure"),
    ThemeDefinition(key="gpu", label="GPU"),
    ThemeDefinition(key="networking", label="Networking"),
    ThemeDefinition(key="optical", label="Optical"),
    ThemeDefinition(key="cloud", label="Cloud"),
    ThemeDefinition(key="security", label="Security"),
    ThemeDefinition(key="cooling", label="Cooling"),
    ThemeDefinition(key="power", label="Power"),
)

DEFAULT_THEME_ASSIGNMENTS = (
    ThemeAssignment(stock_id="US:NVDA", theme_keys=("ai-infrastructure", "gpu")),
    ThemeAssignment(stock_id="US:AMD", theme_keys=("ai-infrastructure", "gpu")),
    ThemeAssignment(stock_id="US:ANET", theme_keys=("ai-infrastructure", "networking")),
    ThemeAssignment(stock_id="US:LITE", theme_keys=("optical",)),
    ThemeAssignment(stock_id="US:AMZN", theme_keys=("cloud",)),
    ThemeAssignment(stock_id="US:MSFT", theme_keys=("cloud", "security")),
    ThemeAssignment(stock_id="US:CRWD", theme_keys=("security",)),
    ThemeAssignment(stock_id="US:VRT", theme_keys=("cooling", "power")),
    ThemeAssignment(stock_id="US:ETN", theme_keys=("power",)),
)

_SEPARATOR_PATTERN = re.compile(r"[\s_]+")


class StockThemeService:
    def __init__(
        self,
        *,
        themes: Sequence[ThemeDefinition] = DEFAULT_THEME_DEFINITIONS,
        assignments: Sequence[ThemeAssignment] = DEFAULT_THEME_ASSIGNMENTS,
        stock_loader: StockLoader | None = None,
    ) -> None:
        self._stock_loader = stock_loader or quote_service.get_stock
        self._themes = tuple(
            ThemeDefinition(
                key=_normalize_theme_key(theme.key),
                label=theme.label,
            )
            for theme in themes
        )
        self._themes_by_key = {theme.key: theme for theme in self._themes}
        self._assignments = tuple(
            ThemeAssignment(
                stock_id=assignment.stock_id,
                theme_keys=tuple(
                    _normalize_theme_key(theme_key) for theme_key in assignment.theme_keys
                ),
            )
            for assignment in assignments
        )
        self._validate_configuration()

    def list_themes(self) -> tuple[ThemeDefinition, ...]:
        return self._themes

    def get_theme(self, theme_key: str) -> ThemeDefinition:
        normalized = _normalize_theme_key(theme_key)
        theme = self._themes_by_key.get(normalized)
        if theme is None:
            raise ValueError(f"Unknown theme: {theme_key}")
        return theme

    def get_stocks_for_theme(self, theme_key: str) -> ThemeStockGroup:
        theme = self.get_theme(theme_key)
        stocks: list[StockRecord] = []
        for assignment in self._assignments:
            if theme.key not in assignment.theme_keys:
                continue
            stock = self._stock_loader(assignment.stock_id)
            if stock is not None:
                stocks.append(stock)
        return ThemeStockGroup(theme=theme, stocks=tuple(stocks))

    def get_themes_for_stock(self, stock_id: str) -> tuple[ThemeDefinition, ...]:
        stock_themes: list[ThemeDefinition] = []
        for assignment in self._assignments:
            if assignment.stock_id != stock_id:
                continue
            stock_themes.extend(self._themes_by_key[theme_key] for theme_key in assignment.theme_keys)
        return tuple(stock_themes)

    def _validate_configuration(self) -> None:
        seen_theme_keys: set[str] = set()
        for theme in self._themes:
            if not theme.key:
                raise ValueError("Theme key must not be empty.")
            if theme.key in seen_theme_keys:
                raise ValueError(f"Duplicate theme key: {theme.key}")
            seen_theme_keys.add(theme.key)

        for assignment in self._assignments:
            if not assignment.stock_id:
                raise ValueError("Theme assignment stock_id must not be empty.")
            if not assignment.theme_keys:
                raise ValueError(
                    f"Theme assignment for {assignment.stock_id} must include at least one theme key."
                )
            for theme_key in assignment.theme_keys:
                if theme_key not in self._themes_by_key:
                    raise ValueError(
                        f"Theme assignment for {assignment.stock_id} references unknown theme: {theme_key}"
                    )


def _normalize_theme_key(theme_key: str) -> str:
    collapsed = _SEPARATOR_PATTERN.sub("-", theme_key.strip().lower())
    return collapsed


stock_theme_service = StockThemeService()
