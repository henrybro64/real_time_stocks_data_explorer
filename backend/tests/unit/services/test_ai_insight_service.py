import pytest

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services import ai_insight_service as ai_module


def test_build_prompt_for_tw_stocks_uses_traditional_chinese_sections() -> None:
    prompt = ai_module._build_prompt(
        stock_id="TW:2330",
        market="TW",
        symbol="2330",
        exchange="TWSE",
        name="台積電",
        currency="TWD",
        last_price=950.0,
        change=10.0,
        change_percent=1.06,
        volume=123456,
        key_stats={"marketCap": "24T"},
    )

    assert "請使用繁體中文輸出" in prompt
    assert "## 進場買點" in prompt
    assert "TW:2330" in prompt


def test_build_prompt_for_us_stocks_uses_english_sections() -> None:
    prompt = ai_module._build_prompt(
        stock_id="US:AAPL",
        market="US",
        symbol="AAPL",
        exchange="NASDAQ",
        name="Apple Inc.",
        currency="USD",
        last_price=200.0,
        change=1.5,
        change_percent=0.75,
        volume=5000,
        key_stats={"peTtm": 30.5},
    )

    assert "Respond in English only" in prompt
    assert "## Suggested Buying Price" in prompt
    assert "Apple Inc." in prompt


def test_generate_insight_dispatches_to_openai(monkeypatch) -> None:
    user_id = "test-user"
    stock = StockRecord(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = QuoteRecord(
        id="US:AAPL",
        last_price=210.0,
        change=2.0,
        change_percent=0.96,
        previous_close=208.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
        volume=1234,
    )
    captured: dict[str, str] = {}

    monkeypatch.setattr(ai_module.quote_service, "get_stock", lambda stock_id: stock)
    monkeypatch.setattr(ai_module.quote_service, "get_quote", lambda stock_id: quote)
    monkeypatch.setattr(
        ai_module.api_key_service,
        "get_key",
        lambda current_user_id, provider: "sk-test",
    )
    monkeypatch.setattr(ai_module, "get_key_statistics", lambda stock_id: {"peTtm": 30.5})

    def fake_call_openai(prompt: str, api_key: str) -> str:
        captured["prompt"] = prompt
        captured["api_key"] = api_key
        return "analysis"

    monkeypatch.setattr(ai_module, "_call_openai", fake_call_openai)

    result = ai_module.AiInsightService().generate_insight(
        user_id,
        "US:AAPL",
        "openai",
    )

    assert result == "analysis"
    assert captured["api_key"] == "sk-test"
    assert "Apple Inc." in captured["prompt"]


def test_generate_insight_requires_configured_api_key(monkeypatch) -> None:
    user_id = "test-user"
    stock = StockRecord(
        id="US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    quote = QuoteRecord(
        id="US:AAPL",
        last_price=210.0,
        change=2.0,
        change_percent=0.96,
        previous_close=208.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
        volume=1234,
    )

    monkeypatch.setattr(ai_module.quote_service, "get_stock", lambda stock_id: stock)
    monkeypatch.setattr(ai_module.quote_service, "get_quote", lambda stock_id: quote)
    monkeypatch.setattr(
        ai_module.api_key_service,
        "get_key",
        lambda current_user_id, provider: None,
    )

    with pytest.raises(ValueError, match="API key is not configured"):
        ai_module.AiInsightService().generate_insight(
            user_id,
            "US:AAPL",
            "openai",
        )
