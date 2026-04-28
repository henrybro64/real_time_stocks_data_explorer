from app.services import ai_insight_service as ai_module
from app.services.stock_analysis_service import StockAnalysisResult


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

    def fake_analyze_stock(stock_id: str, *, provider_override):
        captured["stock_id"] = stock_id
        captured["provider_override"] = provider_override
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
        lambda stock_id, *, provider_override: StockAnalysisResult(
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
