import numpy as np
import pandas as pd
import pytest

from app.services.ai_providers.base import AIAnalysisResult, AIProviderError
from app.services.stock_analysis_service import (
    INVALID_YFINANCE_TICKER_ERROR,
    METADATA_LOAD_WARNING,
    NO_HISTORY_ERROR,
    SKIP_AI_MISSING_HISTORY_WARNING,
    StockAnalysisService,
    normalize_stock_id_to_yfinance,
)
from tests.support import stock_record


def _history_frame(days: int = 260) -> pd.DataFrame:
    close = np.linspace(100.0, 150.0, days)
    adjusted = close * 0.995
    volume = np.linspace(1_000_000, 1_500_000, days)
    return pd.DataFrame(
        {
            "Close": close,
            "Adj Close": adjusted,
            "Volume": volume,
        }
    )


class _MockProvider:
    provider_name = "openai"
    model_name = "gpt-test"

    def generate_stock_analysis(self, input_data: dict[str, object]) -> AIAnalysisResult:
        return AIAnalysisResult(
            provider=self.provider_name,
            model=self.model_name,
            content=f"Analysis for {input_data['ticker']}",
        )


class _FailingProvider:
    provider_name = "openai"
    model_name = "gpt-test"

    def generate_stock_analysis(self, input_data: dict[str, object]) -> AIAnalysisResult:
        raise AIProviderError("boom")


def test_normalize_stock_id_to_yfinance_uses_market_specific_suffixes() -> None:
    us_stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    tw_stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    assert normalize_stock_id_to_yfinance(us_stock) == "AAPL"
    assert normalize_stock_id_to_yfinance(tw_stock) == "2330.TW"


def test_analyze_stock_raises_for_unknown_stock(monkeypatch) -> None:
    service = StockAnalysisService(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {},
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: None,
    )

    with pytest.raises(ValueError, match="Stock not found"):
        service.analyze_stock("US:UNKNOWN")


def test_analyze_stock_handles_empty_yfinance_response(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = StockAnalysisService(
        history_loader=lambda ticker: pd.DataFrame(),
        info_loader=lambda ticker: {},
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL")

    assert result.ticker == "AAPL"
    assert result.ai_analysis is None
    assert NO_HISTORY_ERROR in result.errors
    assert INVALID_YFINANCE_TICKER_ERROR in result.errors
    assert result.computed_data.return_1m_percent is None


def test_analyze_stock_uses_mock_provider_result(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = StockAnalysisService(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {
            "longName": "Apple Inc.",
            "currency": "USD",
            "marketCap": 1000,
            "trailingPE": 30.5,
            "forwardPE": 28.2,
            "currentPrice": 150.0,
        },
        provider_factory=lambda: _MockProvider(),
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL")

    assert result.ai_provider == "openai"
    assert result.ai_model == "gpt-test"
    assert result.ai_analysis == "Analysis for AAPL"
    assert result.computed_data.current_price == 150.0


def test_analyze_stock_returns_computed_metrics_when_provider_fails(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = StockAnalysisService(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {
            "longName": "Apple Inc.",
            "currency": "USD",
            "currentPrice": 150.0,
        },
        provider_factory=lambda: _FailingProvider(),
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL")

    assert result.ai_analysis is None
    assert result.computed_data.current_price == 150.0
    assert (
        "AI analysis is temporarily unavailable, but computed stock metrics are still available."
        in result.warnings
    )


def test_analyze_stock_skips_ai_when_yfinance_history_is_missing(
    monkeypatch,
) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )

    class _TrackingProvider:
        provider_name = "openai"
        model_name = "gpt-test"

        def __init__(self) -> None:
            self.calls = 0

        def generate_stock_analysis(self, input_data: dict[str, object]) -> AIAnalysisResult:
            self.calls += 1
            return AIAnalysisResult(
                provider=self.provider_name,
                model=self.model_name,
                content="should not be used",
            )

    provider = _TrackingProvider()
    service = StockAnalysisService(
        history_loader=lambda ticker: pd.DataFrame(),
        info_loader=lambda ticker: {
            "longName": "Apple Inc.",
            "currency": "USD",
        },
        provider_factory=lambda: provider,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL")

    assert provider.calls == 0
    assert result.ai_provider == "openai"
    assert result.ai_model == "gpt-test"
    assert result.ai_analysis is None
    assert NO_HISTORY_ERROR in result.errors
    assert INVALID_YFINANCE_TICKER_ERROR not in result.errors
    assert SKIP_AI_MISSING_HISTORY_WARNING in result.warnings


def test_analyze_stock_does_not_mark_invalid_ticker_when_metadata_fetch_fails(
    monkeypatch,
) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = StockAnalysisService(
        history_loader=lambda ticker: pd.DataFrame(),
        info_loader=lambda ticker: (_ for _ in ()).throw(RuntimeError("boom")),
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL")

    assert NO_HISTORY_ERROR in result.errors
    assert INVALID_YFINANCE_TICKER_ERROR not in result.errors
    assert METADATA_LOAD_WARNING in result.warnings


def test_analyze_stock_treats_nan_yfinance_metadata_as_missing_signal(
    monkeypatch,
) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = StockAnalysisService(
        history_loader=lambda ticker: pd.DataFrame(),
        info_loader=lambda ticker: {
            "longName": np.nan,
            "currency": np.nan,
            "marketCap": np.nan,
            "trailingPE": np.nan,
            "forwardPE": np.nan,
            "currentPrice": np.nan,
        },
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL")

    assert NO_HISTORY_ERROR in result.errors
    assert INVALID_YFINANCE_TICKER_ERROR in result.errors
