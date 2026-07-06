import pytest

from app.api.routes import themes as themes_route
from app.services.stock_theme_service import StockThemeService, ThemeAssignment, ThemeDefinition
from tests.support import stock_record


pytestmark = pytest.mark.integration


def _stock(stock_id: str, symbol: str, name: str):
    return stock_record(
        stock_id,
        market="US",
        symbol=symbol,
        name=name,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en=name,
    )


def _gpu_stocks_loader(stock_id: str):
    stocks = {
        "US:NVDA": _stock("US:NVDA", "NVDA", "NVIDIA Corporation"),
        "US:AMD": _stock("US:AMD", "AMD", "Advanced Micro Devices, Inc."),
    }
    return stocks.get(stock_id)


def test_themes_api_lists_default_themes(client) -> None:
    response = client.get("/api/themes")

    assert response.status_code == 200
    payload = response.json()
    assert [item["key"] for item in payload] == [
        "ai-infrastructure",
        "gpu",
        "networking",
        "optical",
        "cloud",
        "security",
        "cooling",
        "power",
    ]
    assert payload[1]["label"] == "GPU"


def test_themes_api_returns_stocks_for_theme(monkeypatch, client) -> None:
    monkeypatch.setattr(
        themes_route.stock_theme_service,
        "_stock_loader",
        _gpu_stocks_loader,
    )

    response = client.get("/api/themes/gpu/stocks")

    assert response.status_code == 200
    payload = response.json()
    assert payload["theme"] == {"key": "gpu", "label": "GPU"}
    assert sorted(stock["symbol"] for stock in payload["stocks"]) == ["AMD", "NVDA"]


def test_themes_api_normalizes_theme_key_in_path(monkeypatch, client) -> None:
    monkeypatch.setattr(
        themes_route.stock_theme_service,
        "_stock_loader",
        _gpu_stocks_loader,
    )

    response = client.get("/api/themes/GPU/stocks")

    assert response.status_code == 200
    assert response.json()["theme"]["key"] == "gpu"


def test_themes_api_returns_404_for_unknown_theme(client) -> None:
    response = client.get("/api/themes/unknown/stocks")

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown theme: unknown"


def test_themes_api_skips_missing_stocks(monkeypatch, client) -> None:
    service = StockThemeService(
        themes=(ThemeDefinition(key="gpu", label="GPU"),),
        assignments=(
            ThemeAssignment(stock_id="US:NVDA", theme_keys=("gpu",)),
            ThemeAssignment(stock_id="US:AMD", theme_keys=("gpu",)),
        ),
        stock_loader=lambda stock_id: _stock("US:NVDA", "NVDA", "NVIDIA Corporation")
        if stock_id == "US:NVDA"
        else None,
    )
    monkeypatch.setattr(themes_route, "stock_theme_service", service)

    response = client.get("/api/themes/gpu/stocks")

    assert response.status_code == 200
    assert [stock["symbol"] for stock in response.json()["stocks"]] == ["NVDA"]
