from __future__ import annotations

from app.services.stock_theme_service import (
    StockThemeService,
    ThemeAssignment,
    ThemeDefinition,
)
from tests.support import stock_record


def test_stock_theme_service_returns_stocks_for_theme() -> None:
    stocks = {
        "US:NVDA": stock_record(
            "US:NVDA",
            market="US",
            symbol="NVDA",
            name="NVIDIA Corporation",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            name_en="NVIDIA Corporation",
        ),
        "US:ANET": stock_record(
            "US:ANET",
            market="US",
            symbol="ANET",
            name="Arista Networks, Inc.",
            exchange="NYSE",
            currency="USD",
            timezone="America/New_York",
            name_en="Arista Networks, Inc.",
        ),
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


def test_stock_theme_service_supports_multiple_themes_per_stock() -> None:
    service = StockThemeService(
        themes=(
            ThemeDefinition(key="ai-infrastructure", label="AI Infrastructure"),
            ThemeDefinition(key="gpu", label="GPU"),
        ),
        assignments=(
            ThemeAssignment(
                stock_id="US:NVDA",
                theme_keys=("ai infrastructure", "gpu"),
            ),
        ),
        stock_loader=lambda stock_id: None,
    )

    themes = service.get_themes_for_stock("US:NVDA")

    assert [theme.key for theme in themes] == ["ai-infrastructure", "gpu"]


def test_stock_theme_service_rejects_unknown_assignment_theme() -> None:
    try:
        StockThemeService(
            themes=(ThemeDefinition(key="gpu", label="GPU"),),
            assignments=(
                ThemeAssignment(stock_id="US:NVDA", theme_keys=("security",)),
            ),
            stock_loader=lambda stock_id: None,
        )
    except ValueError as exc:
        assert "unknown theme" in str(exc)
    else:
        raise AssertionError("Expected unknown theme assignment to raise ValueError")
