from app.services import ai_insight_service as ai_module
from app.services.stock_analysis_service import (
    INVALID_YFINANCE_TICKER_ERROR,
    SKIP_AI_MISSING_HISTORY_WARNING,
    StockAnalysisResult,
)


class _ComputedDataStub:
    def to_prompt_payload(self) -> dict[str, object]:
        return {"ticker": "AAPL"}


def test_generate_insight_delegates_to_stock_analysis_service(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_resolve_user_ai_provider(user_id: str, provider: str):
        captured["user_id"] = user_id
        captured["provider"] = provider
        return object()

    def fake_analyze_stock(stock_id: str, *, provider_override, language=None):
        captured["stock_id"] = stock_id
        captured["provider_override"] = provider_override
        captured["language"] = language
        return StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            computed_data=_ComputedDataStub(),
            ai_provider="openai",
            ai_model="gpt-test",
            ai_analysis="analysis",
        )

    monkeypatch.setattr(
        ai_module,
        "resolve_user_ai_provider",
        fake_resolve_user_ai_provider,
    )
    monkeypatch.setattr(
        ai_module.stock_analysis_service,
        "analyze_stock",
        fake_analyze_stock,
    )

    result = ai_module.AiInsightService().generate_insight(
        "test-user",
        "US:AAPL",
        "openai",
    )

    assert result == "analysis"
    assert captured["user_id"] == "test-user"
    assert captured["provider"] == "openai"
    assert captured["stock_id"] == "US:AAPL"
    assert captured["language"] is None


def test_generate_insight_raises_when_ai_analysis_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ai_module,
        "resolve_user_ai_provider",
        lambda user_id, provider: object(),
    )
    monkeypatch.setattr(
        ai_module.stock_analysis_service,
        "analyze_stock",
        lambda stock_id, *, provider_override, language=None: StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            computed_data=_ComputedDataStub(),
            ai_provider="openai",
            ai_model="gpt-test",
            ai_analysis=None,
            warnings=("AI analysis is temporarily unavailable.",),
        ),
    )

    try:
        ai_module.AiInsightService().generate_insight(
            "test-user",
            "US:AAPL",
            "openai",
        )
    except RuntimeError as exc:
        assert str(exc) == "AI explanation is unavailable right now."
    else:
        raise AssertionError(
            "Expected RuntimeError when no AI explanation is present.",
        )


def test_generate_insight_raises_for_invalid_yfinance_ticker(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ai_module,
        "resolve_user_ai_provider",
        lambda user_id, provider: object(),
    )
    monkeypatch.setattr(
        ai_module.stock_analysis_service,
        "analyze_stock",
        lambda stock_id, *, provider_override, language=None: StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            computed_data=_ComputedDataStub(),
            ai_provider="openai",
            ai_model="gpt-test",
            ai_analysis=None,
            errors=(INVALID_YFINANCE_TICKER_ERROR,),
        ),
    )

    try:
        ai_module.AiInsightService().generate_insight(
            "test-user",
            "US:AAPL",
            "openai",
        )
    except ValueError as exc:
        assert str(exc) == INVALID_YFINANCE_TICKER_ERROR
    else:
        raise AssertionError("Expected ValueError for invalid yfinance ticker.")


def test_generate_insight_raises_for_missing_yfinance_history(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ai_module,
        "resolve_user_ai_provider",
        lambda user_id, provider: object(),
    )
    monkeypatch.setattr(
        ai_module.stock_analysis_service,
        "analyze_stock",
        lambda stock_id, *, provider_override, language=None: StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            computed_data=_ComputedDataStub(),
            ai_provider="openai",
            ai_model="gpt-test",
            ai_analysis=None,
            warnings=(SKIP_AI_MISSING_HISTORY_WARNING,),
        ),
    )

    try:
        ai_module.AiInsightService().generate_insight(
            "test-user",
            "US:AAPL",
            "openai",
        )
    except RuntimeError as exc:
        assert str(exc) == "AI insight requires usable yfinance historical price data."
    else:
        raise AssertionError("Expected RuntimeError for missing yfinance history.")
