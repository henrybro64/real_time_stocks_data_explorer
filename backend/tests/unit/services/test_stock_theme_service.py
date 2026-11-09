from __future__ import annotations

import pytest

from app.services.stock_theme_service import (
    DEFAULT_THEME_ASSIGNMENTS,
    DEFAULT_THEME_DEFINITIONS,
    US_THEME_ASSIGNMENTS,
    US_THEME_DEFINITIONS,
    StockThemeService,
    ThemeAssignment,
    ThemeDefinition,
)
from app.services.tw_research.preset import TW_RESEARCH_THEME_DEFINITIONS
from tests.support import stock_record


def _nvda_stock():
    return stock_record(
        "US:NVDA",
        market="US",
        symbol="NVDA",
        name="NVIDIA Corporation",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="NVIDIA Corporation",
    )


def _anet_stock():
    return stock_record(
        "US:ANET",
        market="US",
        symbol="ANET",
        name="Arista Networks, Inc.",
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        name_en="Arista Networks, Inc.",
    )


def test_stock_theme_service_lists_default_themes() -> None:
    service = StockThemeService(stock_loader=lambda stock_id: None)

    themes = service.list_themes()
    keys = [theme.key for theme in themes]
    labels = [theme.label for theme in themes]

    assert keys[:8] == [
        "ai-infrastructure",
        "gpu",
        "networking",
        "optical",
        "cloud",
        "security",
        "cooling",
        "power",
    ]
    assert labels[:8] == [
        "AI Infrastructure",
        "GPU",
        "Networking",
        "Optical",
        "Cloud",
        "Security",
        "Cooling",
        "Power",
    ]
    assert "tw-pcb" in keys
    assert "tw-ccl" in keys
    assert len(keys) == len(US_THEME_DEFINITIONS) + len(TW_RESEARCH_THEME_DEFINITIONS)


def test_stock_theme_service_default_assignments_match_issue_examples() -> None:
    assignments = {
        assignment.stock_id: assignment.theme_keys for assignment in DEFAULT_THEME_ASSIGNMENTS
    }

    assert assignments["US:NVDA"] == ("ai-infrastructure", "gpu")
    assert assignments["US:ANET"] == ("ai-infrastructure", "networking")
    assert assignments["US:LITE"] == ("optical",)
    assert assignments["US:CRWD"] == ("security",)
    assert len(US_THEME_DEFINITIONS) == 8
    assert len(US_THEME_ASSIGNMENTS) == 9
    assert assignments["TW:2383"] == ("tw-ccl", "tw-pcb")
    assert len(DEFAULT_THEME_DEFINITIONS) == len(US_THEME_DEFINITIONS) + len(
        TW_RESEARCH_THEME_DEFINITIONS
    )


def test_stock_theme_service_returns_stocks_for_theme() -> None:
    stocks = {
        "US:NVDA": _nvda_stock(),
        "US:ANET": _anet_stock(),
    }
    service = StockThemeService(
        themes=(
            ThemeDefinition(key="gpu", label="GPU"),
            ThemeDefinition(key="networking", label="Networking"),
        ),
        assignments=(
            ThemeAssignment(stock_id="US:NVDA", theme_keys=("gpu",)),
            ThemeAssignment(stock_id="US:ANET", theme_keys=("networking",)),
        ),
        stock_loader=lambda stock_id: stocks.get(stock_id),
    )

    group = service.get_stocks_for_theme("gpu")

    assert group.theme.key == "gpu"
    assert [stock.symbol for stock in group.stocks] == ["NVDA"]


def test_stock_theme_service_normalizes_theme_key_lookup() -> None:
    stocks = {"US:NVDA": _nvda_stock()}
    service = StockThemeService(
        themes=(ThemeDefinition(key="gpu", label="GPU"),),
        assignments=(ThemeAssignment(stock_id="US:NVDA", theme_keys=("gpu",)),),
        stock_loader=lambda stock_id: stocks.get(stock_id),
    )

    group = service.get_stocks_for_theme(" GPU ")

    assert group.theme.key == "gpu"
    assert [stock.symbol for stock in group.stocks] == ["NVDA"]


def test_stock_theme_service_skips_missing_stocks_from_loader() -> None:
    service = StockThemeService(
        themes=(ThemeDefinition(key="gpu", label="GPU"),),
        assignments=(
            ThemeAssignment(stock_id="US:NVDA", theme_keys=("gpu",)),
            ThemeAssignment(stock_id="US:AMD", theme_keys=("gpu",)),
        ),
        stock_loader=lambda stock_id: _nvda_stock() if stock_id == "US:NVDA" else None,
    )

    group = service.get_stocks_for_theme("gpu")

    assert [stock.symbol for stock in group.stocks] == ["NVDA"]


def test_stock_theme_service_get_theme_raises_for_unknown_theme() -> None:
    service = StockThemeService(
        themes=(ThemeDefinition(key="gpu", label="GPU"),),
        assignments=(ThemeAssignment(stock_id="US:NVDA", theme_keys=("gpu",)),),
        stock_loader=lambda stock_id: None,
    )

    with pytest.raises(ValueError, match="Unknown theme: missing"):
        service.get_theme("missing")


@pytest.mark.parametrize("theme_key", ["", "   "])
def test_stock_theme_service_rejects_empty_theme_keys(theme_key: str) -> None:
    with pytest.raises(ValueError, match="Theme key must not be empty"):
        StockThemeService(
            themes=(ThemeDefinition(key=theme_key, label="GPU"),),
            assignments=(),
            stock_loader=lambda stock_id: None,
        )


def test_stock_theme_service_rejects_duplicate_theme_keys() -> None:
    with pytest.raises(ValueError, match="Duplicate theme key: gpu"):
        StockThemeService(
            themes=(
                ThemeDefinition(key="gpu", label="GPU"),
                ThemeDefinition(key="GPU", label="GPU Duplicate"),
            ),
            assignments=(),
            stock_loader=lambda stock_id: None,
        )


def test_stock_theme_service_rejects_empty_assignment_stock_id() -> None:
    with pytest.raises(ValueError, match="Theme assignment stock_id must not be empty"):
        StockThemeService(
            themes=(ThemeDefinition(key="gpu", label="GPU"),),
            assignments=(ThemeAssignment(stock_id="", theme_keys=("gpu",)),),
            stock_loader=lambda stock_id: None,
        )


def test_stock_theme_service_rejects_empty_assignment_theme_keys() -> None:
    with pytest.raises(
        ValueError,
        match="Theme assignment for US:NVDA must include at least one theme key",
    ):
        StockThemeService(
            themes=(ThemeDefinition(key="gpu", label="GPU"),),
            assignments=(ThemeAssignment(stock_id="US:NVDA", theme_keys=()),),
            stock_loader=lambda stock_id: None,
        )


def test_stock_theme_service_rejects_unknown_assignment_theme() -> None:
    with pytest.raises(ValueError, match="unknown theme: security"):
        StockThemeService(
            themes=(ThemeDefinition(key="gpu", label="GPU"),),
            assignments=(
                ThemeAssignment(stock_id="US:NVDA", theme_keys=("security",)),
            ),
            stock_loader=lambda stock_id: None,
        )
