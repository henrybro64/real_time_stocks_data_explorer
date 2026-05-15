import pytest

from app.api.routes import portfolio as portfolio_routes
from app.services.ai_providers.base import AIAnalysisResult
from app.services.quote_service import quote_service
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


def test_portfolio_positions_and_summary_handle_updates_and_missing_quotes(
    monkeypatch,
    client,
) -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="TSMC",
        name_zh_tw="台積電",
    )
    quotes = {
        "TW:2330": quote_record(
            "TW:2330",
            last_price=950.0,
            change=5.0,
            change_percent=0.53,
            previous_close=945.0,
            timestamp="2026-04-01T09:00:00+08:00",
            is_market_open=True,
            open_price=948.0,
            high=955.0,
            low=944.0,
            volume=12_500,
        )
    }
    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: stock if stock_id == "TW:2330" else None)
    monkeypatch.setattr(quote_service, "get_quote", lambda stock_id: quotes.get(stock_id))
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
    )

    create = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "TW:2330",
            "quantity": 2,
            "averageCost": 900,
            "currency": "TWD",
            "notes": None,
        },
    )
    created = create.json()
    update = client.patch(
        f"/api/portfolio/positions/{created['id']}",
        json={"quantity": 3},
    )
    quotes["TW:2330"] = None
    positions = client.get("/api/portfolio/positions")
    summary = client.get("/api/portfolio/summary")
    delete = client.delete(f"/api/portfolio/positions/{created['id']}")

    assert create.status_code == 200
    assert created["stockId"] == "TW:2330"
    assert update.status_code == 200
    assert update.json()["quantity"] == 3.0
    assert positions.status_code == 200
    assert positions.json()[0]["quote"] is None
    assert summary.status_code == 200
    assert summary.json()["totalTwd"] == 2700.0
    assert summary.json()["combinedUsd"] == 84.38
    assert delete.status_code == 204


def test_portfolio_summary_skips_unknown_stocks(monkeypatch, client) -> None:
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock_record(
            "US:AAPL",
            market="US",
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            name_en="Apple Inc.",
        )
        if stock_id == "US:AAPL"
        else None,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: quote_record(
            "US:AAPL",
            last_price=200.0,
            change=1.0,
            change_percent=0.5,
            previous_close=199.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        )
        if stock_id == "US:AAPL"
        else None,
    )
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
    )

    create = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:AAPL",
            "quantity": 1,
            "averageCost": 180,
            "currency": "USD",
            "notes": None,
        },
    )
    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: None)
    response = client.get("/api/portfolio/summary")

    assert create.status_code == 200
    assert response.status_code == 200
    assert response.json()["skippedStockIds"] == ["US:AAPL"]


def test_portfolio_analysis_returns_structured_response(monkeypatch, client) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=200.0,
        change=1.0,
        change_percent=0.5,
        previous_close=199.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )

    class _MockProvider:
        provider_name = "openai"
        model_name = "gpt-test"

        def generate_portfolio_analysis(
            self,
            input_data: dict[str, object],
        ) -> AIAnalysisResult:
            return AIAnalysisResult(
                provider=self.provider_name,
                model=self.model_name,
                content="Portfolio report",
            )

    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_stock_loader",
        lambda stock_id: stock if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_quote_loader",
        lambda stock_id: quote if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_fx_snapshot_loader",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
    )
    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_provider_resolver",
        lambda user_id: _MockProvider(),
    )

    create = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:AAPL",
            "quantity": 2,
            "averageCost": 180,
            "currency": "USD",
            "notes": "Core holding",
        },
    )
    response = client.post("/api/portfolio/analysis")

    assert create.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-test"
    assert payload["aiReport"] == "Portfolio report"
    assert payload["computedSummary"]["totalMarketValueUsd"] == 400.0
    assert payload["computedSummary"]["totalUnrealizedPlUsd"] == 40.0
    assert payload["computedSummary"]["holdings"][0]["stockId"] == "US:AAPL"


def test_portfolio_analysis_returns_empty_portfolio_error(client) -> None:
    response = client.post("/api/portfolio/analysis")

    assert response.status_code == 400
    assert response.json()["detail"] == "Portfolio is empty."


def test_portfolio_analysis_returns_missing_ai_config_error(monkeypatch, client) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=200.0,
        change=1.0,
        change_percent=0.5,
        previous_close=199.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )

    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_stock_loader",
        lambda stock_id: stock if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_quote_loader",
        lambda stock_id: quote if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_fx_snapshot_loader",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
    )
    monkeypatch.setattr(
        portfolio_routes.portfolio_analysis_service,
        "_provider_resolver",
        lambda user_id: None,
    )

    create = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:AAPL",
            "quantity": 1,
            "averageCost": 180,
            "currency": "USD",
            "notes": None,
        },
    )
    response = client.post("/api/portfolio/analysis")

    assert create.status_code == 200
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "AI is not configured. Please set up your AI provider in AI Settings."
    )
