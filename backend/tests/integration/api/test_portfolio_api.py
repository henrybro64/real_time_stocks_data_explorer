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
            "status": "ok",
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
    assert created["currentPrice"] == 950.0
    assert created["costBasis"] == 1800.0
    assert created["marketValue"] == 1900.0
    assert created["unrealizedPl"] == 100.0
    assert created["unrealizedPlPercent"] == 5.56
    assert update.status_code == 200
    updated = update.json()
    assert updated["quantity"] == 3.0
    assert updated["currentPrice"] == 950.0
    assert updated["costBasis"] == 2700.0
    assert updated["marketValue"] == 2850.0
    assert updated["unrealizedPl"] == 150.0
    assert updated["unrealizedPlPercent"] == 5.56
    assert positions.status_code == 200
    assert positions.json()[0]["quote"] is None
    assert positions.json()[0]["currentPrice"] is None
    assert positions.json()[0]["costBasis"] == 2700.0
    assert positions.json()[0]["marketValue"] is None
    assert positions.json()[0]["unrealizedPl"] is None
    assert positions.json()[0]["unrealizedPlPercent"] is None
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["positions"][0]["marketValue"] is None
    assert summary_payload["missingQuoteStockIds"] == ["TW:2330"]
    assert summary_payload["incomplete"] is True
    assert summary_payload["fxStatus"] == "ok"
    assert summary_payload["totalTwd"] == 0.0
    assert summary_payload["totalCostTwd"] == 0.0
    assert summary_payload["combinedCostUsd"] == 0.0
    assert summary_payload["combinedUsd"] == 0.0
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
            "status": "ok",
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
    payload = response.json()
    assert payload["skippedStockIds"] == ["US:AAPL"]
    assert payload["positions"] == []
    assert payload["missingQuoteStockIds"] == []
    assert payload["incomplete"] is True
    assert payload["fxStatus"] == "ok"
    assert payload["totalUsd"] == 0.0


def test_portfolio_summary_renormalizes_allocation_when_stocks_are_skipped(
    monkeypatch,
    client,
) -> None:
    aapl = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    msft = stock_record(
        "US:MSFT",
        market="US",
        symbol="MSFT",
        name="Microsoft Corp.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Microsoft Corp.",
    )
    aapl_quote = quote_record(
        "US:AAPL",
        last_price=200.0,
        change=1.0,
        change_percent=0.5,
        previous_close=199.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    msft_quote = quote_record(
        "US:MSFT",
        last_price=400.0,
        change=1.0,
        change_percent=0.5,
        previous_close=399.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    stocks = {"US:AAPL": aapl, "US:MSFT": msft}
    quotes = {"US:AAPL": aapl_quote, "US:MSFT": msft_quote}

    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: stocks.get(stock_id))
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

    first = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:AAPL",
            "quantity": 1,
            "averageCost": 180,
            "currency": "USD",
            "notes": None,
        },
    )
    second = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:MSFT",
            "quantity": 1,
            "averageCost": 300,
            "currency": "USD",
            "notes": None,
        },
    )

    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: aapl if stock_id == "US:AAPL" else None)
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: aapl_quote if stock_id == "US:AAPL" else None,
    )
    response = client.get("/api/portfolio/summary")

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["skippedStockIds"] == ["US:MSFT"]
    assert len(payload["positions"]) == 1
    assert payload["positions"][0]["stockId"] == "US:AAPL"
    assert payload["positions"][0]["allocationPercent"] == 100.0


def test_portfolio_summary_excludes_unpriced_holdings_from_allocation(
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
    quote = quote_record(
        "TW:2330",
        last_price=950.0,
        change=5.0,
        change_percent=0.53,
        previous_close=945.0,
        timestamp="2026-04-01T09:00:00+08:00",
        is_market_open=True,
    )
    quotes = {"TW:2330": quote}

    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: stock if stock_id == "TW:2330" else None)
    monkeypatch.setattr(quote_service, "get_quote", lambda stock_id: quotes.get(stock_id))
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
            "status": "ok",
        },
    )

    create = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "TW:2330",
            "quantity": 3,
            "averageCost": 900,
            "currency": "TWD",
            "notes": None,
        },
    )
    quotes["TW:2330"] = None
    response = client.get("/api/portfolio/summary")

    assert create.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["positions"][0]["marketValue"] is None
    assert payload["positions"][0]["allocationPercent"] == 0.0
    assert payload["missingQuoteStockIds"] == ["TW:2330"]
    assert payload["incomplete"] is True
    assert payload["totalTwd"] == 0.0


def test_portfolio_summary_includes_backend_calculated_combined_gain(
    monkeypatch,
    client,
) -> None:
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
    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: stock if stock_id == "US:AAPL" else None)
    monkeypatch.setattr(quote_service, "get_quote", lambda stock_id: quote if stock_id == "US:AAPL" else None)
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
            "status": "ok",
        },
    )

    create = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:AAPL",
            "quantity": 2,
            "averageCost": 180,
            "currency": "USD",
            "notes": None,
        },
    )
    response = client.get("/api/portfolio/summary")

    assert create.status_code == 200
    assert response.status_code == 200
    assert response.json()["totalCostUsd"] == 360.0
    assert response.json()["combinedCostUsd"] == 360.0
    assert response.json()["combinedGainUsd"] == 40.0
    assert response.json()["combinedGainTwd"] == 1280.0
    assert response.json()["combinedGainPercent"] == 11.11
    assert response.json()["positions"][0]["allocationPercent"] == 100.0


def test_portfolio_summary_includes_per_holding_allocation_percent(
    monkeypatch,
    client,
) -> None:
    stocks = {
        "US:AAPL": stock_record(
            "US:AAPL",
            market="US",
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            name_en="Apple Inc.",
        ),
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
            name_en="TSMC",
            name_zh_tw="台積電",
        ),
    }
    quotes = {
        "US:AAPL": quote_record(
            "US:AAPL",
            last_price=200.0,
            change=1.0,
            change_percent=0.5,
            previous_close=199.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        ),
        "TW:2330": quote_record(
            "TW:2330",
            last_price=950.0,
            change=5.0,
            change_percent=0.53,
            previous_close=945.0,
            timestamp="2026-04-01T09:00:00+08:00",
            is_market_open=True,
        ),
    }
    monkeypatch.setattr(quote_service, "get_stock", lambda stock_id: stocks.get(stock_id))
    monkeypatch.setattr(quote_service, "get_quote", lambda stock_id: quotes.get(stock_id))
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
            "status": "ok",
        },
    )

    first = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "US:AAPL",
            "quantity": 2,
            "averageCost": 180,
            "currency": "USD",
            "notes": None,
        },
    )
    second = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "TW:2330",
            "quantity": 2,
            "averageCost": 900,
            "currency": "TWD",
            "notes": None,
        },
    )
    response = client.get("/api/portfolio/summary")

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 200

    payload = response.json()
    assert payload["totalCostUsd"] == 360.0
    assert payload["totalCostTwd"] == 1800.0
    assert payload["combinedCostUsd"] == 416.25
    assert payload["combinedCostTwd"] == 13320.0

    positions = payload["positions"]
    assert positions[0]["stockId"] == "US:AAPL"
    assert positions[0]["allocationPercent"] == 87.07
    assert positions[1]["stockId"] == "TW:2330"
    assert positions[1]["allocationPercent"] == 12.93
    assert round(sum(item["allocationPercent"] for item in positions), 2) == 100.0


def test_empty_portfolio_summary_returns_zero_gain_metrics(monkeypatch, client) -> None:
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
    )

    response = client.get("/api/portfolio/summary")

    assert response.status_code == 200
    assert response.json()["totalCostUsd"] == 0.0
    assert response.json()["totalCostTwd"] == 0.0
    assert response.json()["combinedCostUsd"] == 0.0
    assert response.json()["combinedCostTwd"] == 0.0
    assert response.json()["combinedGainUsd"] == 0.0
    assert response.json()["combinedGainTwd"] == 0.0
    assert response.json()["combinedGainPercent"] == 0.0
    assert response.json()["positions"] == []


def test_portfolio_position_rejects_currency_mismatch(monkeypatch, client) -> None:
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
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "TW:2330" else None,
    )

    response = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "TW:2330",
            "quantity": 1,
            "averageCost": 900,
            "currency": "USD",
            "notes": None,
        },
    )

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_portfolio_position_normalizes_supported_currency(monkeypatch, client) -> None:
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
    quote = quote_record(
        "TW:2330",
        last_price=950.0,
        change=5.0,
        change_percent=0.53,
        previous_close=945.0,
        timestamp="2026-04-01T09:00:00+08:00",
        is_market_open=True,
    )
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "TW:2330" else None,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: quote if stock_id == "TW:2330" else None,
    )

    response = client.post(
        "/api/portfolio/positions",
        json={
            "stockId": "TW:2330",
            "quantity": 1,
            "averageCost": 900,
            "currency": " twd ",
            "notes": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["currency"] == "TWD"


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
