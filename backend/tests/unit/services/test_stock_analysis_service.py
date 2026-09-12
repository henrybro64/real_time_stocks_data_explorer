import numpy as np
import pandas as pd
import pytest

from app.services.ai_providers.base import AIAnalysisResult, AIProviderError
from app.services.news_service import AnalysisNewsContext
from app.services.stock_analysis_service import (
    INVALID_YFINANCE_TICKER_ERROR,
    METADATA_LOAD_WARNING,
    NO_HISTORY_ERROR,
    SKIP_AI_MISSING_HISTORY_WARNING,
    ComputedStockAnalysisData,
    StockAnalysisService,
    merge_news_citations,
    normalize_stock_id_to_yfinance,
)
from tests.support import stock_record


def _empty_news(_stock) -> AnalysisNewsContext:
    return AnalysisNewsContext(
        available=False,
        window_start=None,
        window_end=None,
        data_as_of=None,
        items=(),
        failure_reason="no news in unit test",
    )


def _history_frame(days: int = 260) -> pd.DataFrame:
    close = np.linspace(100.0, 150.0, days)
    adjusted = close * 0.995
    volume = np.linspace(1_000_000, 1_500_000, days)
    # Dated business-day index like yfinance returns, anchored in the past so
    # the completed-session filter keeps every bar.
    index = pd.date_range(end=pd.Timestamp("2026-08-03"), periods=days, freq="B")
    return pd.DataFrame(
        {
            "Close": close,
            "Adj Close": adjusted,
            "Volume": volume,
        },
        index=index,
    )


def _service(**kwargs) -> StockAnalysisService:
    kwargs.setdefault("news_selector", _empty_news)
    return StockAnalysisService(**kwargs)


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
    service = _service(
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
    service = _service(
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
    service = _service(
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
    service = _service(
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
    service = _service(
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
    service = _service(
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
    service = _service(
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


def test_analyze_stock_run_ai_false_skips_provider_and_ai_warnings(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    service = _service(
        history_loader=lambda ticker: pd.DataFrame(),
        info_loader=lambda ticker: {
            "longName": "Apple Inc.",
            "currency": "USD",
        },
        provider_factory=lambda: (_ for _ in ()).throw(AssertionError("provider_factory must not run")),
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL", run_ai=False)

    assert result.ai_analysis is None
    assert result.ai_provider is None
    assert SKIP_AI_MISSING_HISTORY_WARNING not in result.warnings
    assert "AI provider is not configured" not in result.warnings


def test_analyze_stock_prompt_includes_nested_context_and_language(monkeypatch) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    captured: dict[str, object] = {}

    class _CaptureProvider:
        provider_name = "openai"
        model_name = "gpt-test"

        def generate_stock_analysis(self, input_data: dict[str, object]) -> AIAnalysisResult:
            captured.update(input_data)
            return AIAnalysisResult(
                provider=self.provider_name,
                model=self.model_name,
                content="ok",
            )

    service = _service(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {
            "longName": "Apple Inc.",
            "currency": "USD",
            "trailingPE": 30.0,
            "forwardPE": 28.0,
            "trailingEps": 6.0,
            "forwardEps": 7.0,
            "currentPrice": 150.0,
        },
        provider_factory=lambda: _CaptureProvider(),
        news_selector=lambda _stock: AnalysisNewsContext(
            available=True,
            window_start="2026-09-01T00:00:00+00:00",
            window_end="2026-09-08T00:00:00+00:00",
            data_as_of="2026-09-08T00:00:00+00:00",
            items=(),
            selected_count=0,
        ),
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.build_institutional_analysis",
        lambda *_args, **_kwargs: None,
    )

    result = service.analyze_stock("US:AAPL", language="en", include_news=True)

    assert result.language == "en"
    assert captured["language"] == "en"
    assert "technicalAnalysis" in captured
    assert "fundamentalOutlook" in captured
    assert "recentNewsContext" in captured
    assert result.computed_data.technical_analysis is not None
    assert result.computed_data.fundamental_outlook is not None
    assert result.computed_data.institutional_analysis is None


def test_merge_news_citations_prefers_backend_news_and_dedupes() -> None:
    from app.services.ai_providers.base import AnalysisCitation
    from app.services.news_service import AnalysisNewsItem

    news = AnalysisNewsContext(
        available=True,
        window_start=None,
        window_end=None,
        data_as_of=None,
        items=(
            AnalysisNewsItem(
                id="N1",
                title="Apple earnings",
                url="https://example.com/a",
                source="Example",
                published_at="2026-09-07T00:00:00Z",
            ),
        ),
    )
    provider = (
        AnalysisCitation(url="https://example.com/a", title="dup"),
        AnalysisCitation(url="https://example.com/b", title="extra"),
    )
    merged = merge_news_citations(provider, news)
    assert [item.url for item in merged] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert merged[0].published_at == "2026-09-07T00:00:00Z"
    assert merged[0].title == "Apple earnings"

def _us_stock():
    return stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def _tw_stock():
    return stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def test_merge_news_citations_dedupes_equivalent_urls() -> None:
    from app.services.ai_providers.base import AnalysisCitation
    from app.services.news_service import AnalysisNewsItem

    news = AnalysisNewsContext(
        available=True,
        window_start=None,
        window_end=None,
        data_as_of=None,
        items=(
            AnalysisNewsItem(
                id="N1",
                title="Apple earnings",
                url="https://www.example.com/a/",
                source="Example",
                published_at="2026-09-07T00:00:00Z",
            ),
        ),
    )
    provider = (
        # Same article, different scheme/host prefix/trailing slash.
        AnalysisCitation(url="http://example.com/a", title="dup"),
        AnalysisCitation(url="https://example.com/b", title="extra"),
    )

    merged = merge_news_citations(provider, news)

    assert [item.url for item in merged] == [
        "https://www.example.com/a/",
        "https://example.com/b",
    ]


def test_institutional_failure_does_not_block_technical_analysis(monkeypatch) -> None:
    stock = _tw_stock()
    service = _service(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {"longName": "TSMC", "currency": "TWD"},
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("twse down")

    monkeypatch.setattr(
        "app.services.stock_analysis_service.build_institutional_analysis",
        boom,
    )

    result = service.analyze_stock("TW:2330", run_ai=False, language="en")

    institutional = result.computed_data.institutional_analysis
    assert institutional == {
        "available": False,
        "reason": "build_failed",
        "reasonDetail": "RuntimeError",
        "market": "TW",
    }
    # Raw upstream text must never reach the payload the model reads.
    assert "twse down" not in str(institutional)
    assert result.computed_data.technical_analysis["availability"] == "ok"
    assert result.computed_data.rsi_14 is not None


def test_fundamental_failure_does_not_block_technical_analysis(monkeypatch) -> None:
    stock = _us_stock()
    service = _service(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {"longName": "Apple Inc.", "currency": "USD"},
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.build_fundamental_outlook",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad info")),
    )

    result = service.analyze_stock("US:AAPL", run_ai=False, language="en")

    assert result.computed_data.fundamental_outlook["failureReason"] == "build_failed:ValueError"
    assert result.computed_data.technical_analysis["availability"] == "ok"


def test_news_selector_failure_is_soft_and_classified(monkeypatch) -> None:
    stock = _us_stock()
    service = _service(
        history_loader=lambda ticker: _history_frame(),
        info_loader=lambda ticker: {"longName": "Apple Inc.", "currency": "USD"},
        provider_factory=lambda: None,
        news_selector=lambda _stock: (_ for _ in ()).throw(TimeoutError("rss stalled")),
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )

    result = service.analyze_stock("US:AAPL", run_ai=False, language="en", include_news=True)

    news = result.computed_data.recent_news_context
    assert news["available"] is False
    assert news["failureReason"] == "selector_failed:TimeoutError"
    assert "rss stalled" not in str(news)
    assert result.computed_data.technical_analysis["availability"] == "ok"


def test_apply_cached_news_context_restores_the_snapshot() -> None:
    from app.services.stock_analysis_service import apply_cached_news_context

    computed = ComputedStockAnalysisData(
        ticker="AAPL",
        company_name=None,
        currency=None,
        sector=None,
        industry=None,
        market_cap=None,
        trailing_pe=None,
        forward_pe=None,
        current_price=None,
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
        recent_news_context={"available": True, "items": [{"id": "N9"}]},
    )

    snapshot = {"available": True, "windowDays": 7, "items": [{"id": "N1"}]}
    restored = apply_cached_news_context(computed, snapshot)
    assert restored.recent_news_context == snapshot

    # No cached snapshot means the narrative was generated without news; a live
    # read must not attach newer headlines to it.
    cleared = apply_cached_news_context(computed, None)
    assert cleared.recent_news_context is None


def test_computed_metrics_ignore_a_still_forming_session(monkeypatch) -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    stock = _us_stock()
    frame = _history_frame(days=120)
    dates = pd.date_range(end=pd.Timestamp("2026-09-11 16:00", tz=tz), periods=120, freq="B")
    frame.index = dates

    service = _service(
        history_loader=lambda ticker: frame,
        info_loader=lambda ticker: {"longName": "Apple Inc.", "currency": "USD"},
        provider_factory=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.stock_analysis_service.quote_service.get_stock",
        lambda stock_id: stock,
    )
    # Mid-session: the final bar is still forming.
    monkeypatch.setattr(
        "app.services.stock_analysis_technical.is_daily_candle_complete",
        lambda candle, market, now=None: candle.time != dates[-1].to_pydatetime().isoformat(),
    )

    result = service.analyze_stock("US:AAPL", run_ai=False, language="en")

    technical = result.computed_data.technical_analysis
    assert technical["completedSessions"] == 119
    assert technical["dataAsOf"] == dates[-2].date().isoformat()
