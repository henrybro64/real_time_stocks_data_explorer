from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes import history as history_route
from app.main import app
from app.services.stock_analysis_service import (
    ComputedStockAnalysisData,
    StockAnalysisResult,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_provider_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider resolution needs the API-key schema; irrelevant to these tests."""
    monkeypatch.setattr(history_route, "resolve_user_ai_provider", lambda *_args: None)


def _computed(**overrides) -> ComputedStockAnalysisData:
    base = dict(
        ticker="AAPL",
        company_name="Apple Inc.",
        currency="USD",
        sector=None,
        industry=None,
        market_cap=None,
        trailing_pe=None,
        forward_pe=None,
        current_price=210.5,
        return_1m_percent=None,
        return_3m_percent=None,
        return_6m_percent=None,
        return_1y_percent=None,
        sma_50=None,
        sma_200=None,
        price_vs_sma_50_percent=None,
        price_vs_sma_200_percent=None,
        rsi_14=None,
        volatility_30d_annualized=None,
        max_drawdown_1y_percent=None,
        average_volume_30d=None,
        technical_analysis={"period": "1Y", "availability": "ok"},
    )
    base.update(overrides)
    return ComputedStockAnalysisData(**base)


def test_analysis_endpoint_rejects_unsupported_language() -> None:
    response = client.get("/api/stocks/US:AAPL/analysis?language=fr")
    assert response.status_code == 400
    assert "Unsupported analysis language" in response.json()["detail"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("?language=en", "en"), ("?language=zh_TW", "zh-TW"), ("", "zh-TW")],
)
def test_analysis_endpoint_normalizes_and_forwards_language(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected: str,
) -> None:
    seen: dict[str, object] = {}

    def fake_analyze(stock_id, *, provider_override=None, run_ai=True, language=None, include_news=True):
        seen["language"] = language
        return StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            language=language or "zh-TW",
            computed_data=_computed(),
            ai_provider=None,
            ai_model=None,
            ai_analysis=None,
        )

    monkeypatch.setattr(history_route.stock_analysis_service, "analyze_stock", fake_analyze)
    monkeypatch.setattr(history_route, "get_cached_stock_analysis", lambda *_args: None)

    response = client.get(f"/api/stocks/US:AAPL/analysis{query}")

    assert response.status_code == 200
    assert seen["language"] == expected
    assert response.json()["language"] == expected


def test_cache_read_is_scoped_to_the_requested_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups: list[str] = []

    def fake_analyze(stock_id, *, provider_override=None, run_ai=True, language=None, include_news=True):
        return StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            language=language or "zh-TW",
            computed_data=_computed(),
            ai_provider=None,
            ai_model=None,
            ai_analysis=None,
        )

    def fake_get_cached(_user_id, _stock_id, _bucket, language):
        lookups.append(language)
        return None

    monkeypatch.setattr(history_route.stock_analysis_service, "analyze_stock", fake_analyze)
    monkeypatch.setattr(history_route, "get_cached_stock_analysis", fake_get_cached)

    client.get("/api/stocks/US:AAPL/analysis?language=en")
    client.get("/api/stocks/US:AAPL/analysis?language=zh-TW")

    assert lookups == ["en", "zh-TW"]


def test_non_refresh_read_never_fetches_live_news(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_analyze(stock_id, *, provider_override=None, run_ai=True, language=None, include_news=True):
        seen["include_news"] = include_news
        seen["run_ai"] = run_ai
        return StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            language=language or "zh-TW",
            computed_data=_computed(),
            ai_provider=None,
            ai_model=None,
            ai_analysis=None,
        )

    monkeypatch.setattr(history_route.stock_analysis_service, "analyze_stock", fake_analyze)
    monkeypatch.setattr(history_route, "get_cached_stock_analysis", lambda *_args: None)

    response = client.get("/api/stocks/US:AAPL/analysis?language=en")

    assert response.status_code == 200
    assert seen == {"include_news": False, "run_ai": False}
    assert response.json()["computedData"]["recentNewsContext"] is None
