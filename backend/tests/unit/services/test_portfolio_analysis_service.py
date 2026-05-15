import pytest

from app.db.models import PortfolioPosition
from app.services.ai_providers.base import AIAnalysisResult, AIProviderError
from app.services.portfolio_analysis_service import (
    AI_NOT_CONFIGURED_ERROR,
    AI_TEMPORARILY_UNAVAILABLE_WARNING,
    EMPTY_PORTFOLIO_ERROR,
    NO_ANALYZABLE_HOLDINGS_ERROR,
    PortfolioAnalysisService,
)
from tests.support import quote_record, stock_record


def _position(
    stock_id: str,
    *,
    quantity: float,
    average_cost: float,
    currency: str,
    notes: str | None = None,
) -> PortfolioPosition:
    return PortfolioPosition(
        id=1,
        user_id="user-1",
        stock_id=stock_id,
        quantity=quantity,
        average_cost=average_cost,
        currency=currency,
        notes=notes,
    )


class _MockProvider:
    provider_name = "openai"
    model_name = "gpt-test"

    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    def generate_portfolio_analysis(
        self,
        input_data: dict[str, object],
    ) -> AIAnalysisResult:
        self.payload = input_data
        return AIAnalysisResult(
            provider=self.provider_name,
            model=self.model_name,
            content="Structured portfolio analysis",
        )


class _FailingProvider:
    provider_name = "openai"
    model_name = "gpt-test"

    def generate_portfolio_analysis(
        self,
        input_data: dict[str, object],
    ) -> AIAnalysisResult:
        raise AIProviderError("boom")


def test_analyze_portfolio_calculates_metrics_and_calls_provider() -> None:
    positions = [
        _position("US:AAPL", quantity=2, average_cost=100, currency="USD"),
        _position("TW:2330", quantity=3, average_cost=900, currency="TWD"),
    ]
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
            last_price=125.0,
            change=1.0,
            change_percent=0.8,
            previous_close=124.0,
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
    provider = _MockProvider()
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: positions,
        stock_loader=lambda stock_id: stocks.get(stock_id),
        quote_loader=lambda stock_id: quotes.get(stock_id),
        fx_snapshot_loader=lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
        provider_resolver=lambda user_id: provider,
    )

    result = service.analyze_portfolio("user-1")

    assert result.ai_report == "Structured portfolio analysis"
    assert result.provider == "openai"
    assert result.model == "gpt-test"
    assert result.computed_summary.total_market_value_usd == 339.06
    assert result.computed_summary.total_cost_basis_usd == 284.38
    assert result.computed_summary.total_unrealized_pl_usd == 54.68
    assert result.computed_summary.total_unrealized_pl_percent == 19.23
    assert result.computed_summary.currency_exposure[0].currency == "USD"
    assert result.computed_summary.currency_exposure[0].portfolio_weight_percent == 73.73
    assert result.computed_summary.currency_exposure[1].currency == "TWD"
    assert result.computed_summary.largest_holdings[0].stock_id == "US:AAPL"
    assert result.computed_summary.largest_holdings[0].portfolio_weight_percent == 73.73
    assert result.computed_summary.concentration_risk.level == "high"
    assert provider.payload is not None
    assert provider.payload["totalMarketValueUsd"] == 339.06


def test_analyze_portfolio_raises_when_portfolio_is_empty() -> None:
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: [],
        provider_resolver=lambda user_id: _MockProvider(),
    )

    with pytest.raises(ValueError, match=EMPTY_PORTFOLIO_ERROR):
        service.analyze_portfolio("user-1")


def test_analyze_portfolio_raises_when_no_holdings_have_quotes() -> None:
    positions = [_position("US:AAPL", quantity=1, average_cost=100, currency="USD")]
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
        )
    }
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: positions,
        stock_loader=lambda stock_id: stocks.get(stock_id),
        quote_loader=lambda stock_id: None,
        fx_snapshot_loader=lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
        provider_resolver=lambda user_id: _MockProvider(),
    )

    with pytest.raises(ValueError, match=NO_ANALYZABLE_HOLDINGS_ERROR):
        service.analyze_portfolio("user-1")


def test_analyze_portfolio_raises_when_ai_is_not_configured() -> None:
    positions = [_position("US:AAPL", quantity=1, average_cost=100, currency="USD")]
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
        )
    }
    quotes = {
        "US:AAPL": quote_record(
            "US:AAPL",
            last_price=110.0,
            change=1.0,
            change_percent=0.9,
            previous_close=109.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        )
    }
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: positions,
        stock_loader=lambda stock_id: stocks.get(stock_id),
        quote_loader=lambda stock_id: quotes.get(stock_id),
        fx_snapshot_loader=lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
        provider_resolver=lambda user_id: None,
    )

    with pytest.raises(ValueError, match=AI_NOT_CONFIGURED_ERROR):
        service.analyze_portfolio("user-1")


def test_analyze_portfolio_reports_skipped_quotes() -> None:
    positions = [
        _position("US:AAPL", quantity=1, average_cost=100, currency="USD"),
        _position("TW:2330", quantity=2, average_cost=900, currency="TWD"),
    ]
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
            last_price=105.0,
            change=1.0,
            change_percent=0.96,
            previous_close=104.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        ),
        "TW:2330": None,
    }
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: positions,
        stock_loader=lambda stock_id: stocks.get(stock_id),
        quote_loader=lambda stock_id: quotes.get(stock_id),
        fx_snapshot_loader=lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
        provider_resolver=lambda user_id: _MockProvider(),
    )

    result = service.analyze_portfolio("user-1")

    assert result.computed_summary.analyzed_holdings_count == 1
    assert result.computed_summary.skipped_holdings_count == 1
    assert result.computed_summary.skipped_holdings[0].stock_id == "TW:2330"
    assert "TW:2330" in result.warnings[0]


def test_analyze_portfolio_propagates_unexpected_provider_errors() -> None:
    class _BrokenProvider:
        provider_name = "openai"
        model_name = "gpt-test"

        def generate_portfolio_analysis(
            self,
            input_data: dict[str, object],
        ) -> AIAnalysisResult:
            raise RuntimeError("unexpected failure")

    positions = [_position("US:AAPL", quantity=1, average_cost=100, currency="USD")]
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
        )
    }
    quotes = {
        "US:AAPL": quote_record(
            "US:AAPL",
            last_price=110.0,
            change=1.0,
            change_percent=0.9,
            previous_close=109.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        )
    }
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: positions,
        stock_loader=lambda stock_id: stocks.get(stock_id),
        quote_loader=lambda stock_id: quotes.get(stock_id),
        fx_snapshot_loader=lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
        provider_resolver=lambda user_id: _BrokenProvider(),
    )

    with pytest.raises(RuntimeError, match="unexpected failure"):
        service.analyze_portfolio("user-1")


def test_analyze_portfolio_returns_summary_when_provider_fails() -> None:
    positions = [_position("US:AAPL", quantity=1, average_cost=100, currency="USD")]
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
        )
    }
    quotes = {
        "US:AAPL": quote_record(
            "US:AAPL",
            last_price=110.0,
            change=1.0,
            change_percent=0.9,
            previous_close=109.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        )
    }
    service = PortfolioAnalysisService(
        position_loader=lambda user_id: positions,
        stock_loader=lambda stock_id: stocks.get(stock_id),
        quote_loader=lambda stock_id: quotes.get(stock_id),
        fx_snapshot_loader=lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
        },
        provider_resolver=lambda user_id: _FailingProvider(),
    )

    result = service.analyze_portfolio("user-1")

    assert result.ai_report is None
    assert AI_TEMPORARILY_UNAVAILABLE_WARNING in result.warnings
    assert result.computed_summary.total_market_value_usd == 110.0
