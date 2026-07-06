import pytest
from fastapi import HTTPException

from app.api.routes import themes as themes_route
from app.models.stock import StockRecord
from app.services.stock_theme_service import ThemeDefinition, ThemeStockGroup


def test_list_themes_route_delegates_to_service(monkeypatch) -> None:
    monkeypatch.setattr(
        themes_route.stock_theme_service,
        "list_themes",
        lambda: (
            ThemeDefinition(key="gpu", label="GPU"),
            ThemeDefinition(key="cloud", label="Cloud"),
        ),
    )

    response = themes_route.list_themes()

    assert [item.key for item in response] == ["gpu", "cloud"]
    assert [item.label for item in response] == ["GPU", "Cloud"]


def test_get_theme_stocks_route_returns_themed_stocks(monkeypatch) -> None:
    monkeypatch.setattr(
        themes_route.stock_theme_service,
        "get_stocks_for_theme",
        lambda theme_key: ThemeStockGroup(
            theme=ThemeDefinition(key=theme_key, label="GPU"),
            stocks=(
                StockRecord(
                    id="US:NVDA",
                    market="US",
                    symbol="NVDA",
                    name="NVIDIA Corporation",
                    exchange="NASDAQ",
                    currency="USD",
                    timezone="America/New_York",
                    name_en="NVIDIA Corporation",
                ),
            ),
        ),
    )

    response = themes_route.get_theme_stocks("gpu")

    assert response.theme.key == "gpu"
    assert [stock.symbol for stock in response.stocks] == ["NVDA"]


def test_get_theme_stocks_route_returns_404_for_unknown_theme(monkeypatch) -> None:
    def _raise(_theme_key: str):
        raise ValueError("Unknown theme: missing")

    monkeypatch.setattr(
        themes_route.stock_theme_service,
        "get_stocks_for_theme",
        _raise,
    )

    with pytest.raises(HTTPException) as exc_info:
        themes_route.get_theme_stocks("missing")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Unknown theme: missing"
