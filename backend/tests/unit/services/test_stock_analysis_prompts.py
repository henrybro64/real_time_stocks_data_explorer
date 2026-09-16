from __future__ import annotations

from typing import Any

from app.services.ai_providers.base import (
    SYSTEM_PROMPT,
    build_stock_analysis_user_prompt,
)


def _tw_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "language": "en",
        "market": "TW",
        "stockId": "TW:2330",
        "ticker": "2330.TW",
        "institutionalAnalysis": {"available": False},
        "recentNewsContext": {"available": False, "items": []},
        "fundamentalOutlook": {"available": False},
    }
    payload.update(overrides)
    return payload


def _us_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "language": "zh-TW",
        "market": "US",
        "stockId": "US:AAPL",
        "ticker": "AAPL",
        "institutionalAnalysis": {"available": False},
        "recentNewsContext": {"available": False, "items": []},
        "fundamentalOutlook": {"available": False},
    }
    payload.update(overrides)
    return payload


EN_REQUIRED_HEADINGS = [
    "## Plain-language summary",
    "## Price trend and momentum",
    "## Moving averages",
    "## Key price levels",
    "## Possible entry considerations",
    "## Possible exit/risk considerations",
    "## Fundamental outlook",
    "## Valuation context",
    "## What could make this analysis wrong",
    "## Disclaimer",
]

ZH_REQUIRED_HEADINGS = [
    "## 白話摘要",
    "## 價格趨勢與動能",
    "## 移動平均線",
    "## 關鍵價位",
    "## 可能的進場考量",
    "## 可能的出場與風險考量",
    "## 基本面展望",
    "## 估值背景",
    "## 可能讓分析失準的情況",
    "## 免責聲明",
]


def test_system_prompt_emphasizes_json_truth_and_plain_language() -> None:
    assert "ONLY the provided computed JSON" in SYSTEM_PROMPT
    assert "non-finance readers" in SYSTEM_PROMPT
    assert "informational only" in SYSTEM_PROMPT
    assert "not investment advice" in SYSTEM_PROMPT
    assert "Do not invent" in SYSTEM_PROMPT
    assert "metadata only" in SYSTEM_PROMPT


def test_english_language_prompt_for_tw_stock() -> None:
    prompt = build_stock_analysis_user_prompt(_tw_payload(language="en"))

    assert "Respond in English only" in prompt
    assert "請使用繁體中文" not in prompt
    assert "TW:2330" in prompt
    for heading in EN_REQUIRED_HEADINGS:
        assert heading in prompt
    for heading in ZH_REQUIRED_HEADINGS:
        assert heading not in prompt


def test_zh_tw_language_prompt_for_us_stock() -> None:
    prompt = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    assert "請使用繁體中文輸出" in prompt
    assert "Respond in English only" not in prompt
    assert "US:AAPL" in prompt
    for heading in ZH_REQUIRED_HEADINGS:
        assert heading in prompt
    for heading in EN_REQUIRED_HEADINGS:
        assert heading not in prompt


def test_missing_language_defaults_to_zh_tw() -> None:
    payload = _us_payload()
    del payload["language"]
    prompt = build_stock_analysis_user_prompt(payload)

    assert "請使用繁體中文輸出" in prompt
    assert "## 白話摘要" in prompt


def test_prompt_contains_plain_language_rules_and_no_buffett() -> None:
    en = build_stock_analysis_user_prompt(_tw_payload(language="en"))
    zh = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    for prompt in (en, zh):
        assert "Buffett" not in prompt
        assert "巴菲特" not in prompt
        assert "Value per share" not in prompt
        assert "每股內在價值" not in prompt
        assert "Suggested Buying Price" not in prompt
        assert "進場買點" not in prompt

    assert "simple moving average" in en
    assert "momentum indicator" in en
    assert "without finance training" in en
    assert "簡單移動平均線" in zh
    assert "動能指標" in zh
    assert "沒有金融背景" in zh


def test_institutional_section_gated_by_availability() -> None:
    without = build_stock_analysis_user_prompt(
        _tw_payload(language="en", institutionalAnalysis={"available": False})
    )
    with_inst = build_stock_analysis_user_prompt(
        _tw_payload(
            language="en",
            institutionalAnalysis={"available": True, "periodDays": 20},
        )
    )

    assert "## Institutional activity" not in without
    assert "Omit the Institutional activity section" in without
    assert "## Institutional activity" in with_inst
    assert "foreign investors" in with_inst
    assert "Omit the Institutional activity section" not in with_inst

    zh_without = build_stock_analysis_user_prompt(
        _us_payload(language="zh-TW", institutionalAnalysis={"available": False})
    )
    zh_with = build_stock_analysis_user_prompt(
        _tw_payload(
            language="zh-TW",
            institutionalAnalysis={"available": True, "periodDays": 20},
        )
    )
    assert "## 法人動向" not in zh_without
    assert "請完全省略「法人動向」" in zh_without
    assert "## 法人動向" in zh_with
    assert "請完全省略「法人動向」" not in zh_with


def test_news_section_gated_by_recent_news_context() -> None:
    unavailable = build_stock_analysis_user_prompt(
        _tw_payload(
            language="en",
            recentNewsContext={"available": False, "items": []},
        )
    )
    available_empty = build_stock_analysis_user_prompt(
        _tw_payload(
            language="en",
            recentNewsContext={"available": True, "items": []},
        )
    )
    with_news = build_stock_analysis_user_prompt(
        _tw_payload(
            language="en",
            recentNewsContext={
                "available": True,
                "items": [
                    {
                        "id": "N1",
                        "title": "Example",
                        "source": "Reuters",
                        "publishedAt": "2026-09-07T03:00:00Z",
                        "url": "https://example.com",
                    }
                ],
            },
        )
    )

    assert "## Recent news context" not in unavailable
    assert "based on market data only" in unavailable
    assert "## Recent news context" not in available_empty
    assert "## Recent news context" in with_news
    assert "may have contributed" in with_news
    assert "N1" in with_news
    assert "do not claim you read the full article" in with_news

    zh_with = build_stock_analysis_user_prompt(
        _us_payload(
            language="zh-TW",
            recentNewsContext={
                "available": True,
                "items": [{"id": "N1", "title": "標題", "source": "來源"}],
            },
        )
    )
    zh_without = build_stock_analysis_user_prompt(
        _us_payload(
            language="zh-TW",
            recentNewsContext={"available": False, "items": []},
        )
    )
    assert "## 近期新聞背景" in zh_with
    assert "可能是影響因素之一" in zh_with
    assert "## 近期新聞背景" not in zh_without
    assert "僅依據市場資料" in zh_without


def test_insufficient_valuation_wording_present() -> None:
    en = build_stock_analysis_user_prompt(_tw_payload(language="en"))
    zh = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    assert "available data is insufficient" in en
    assert "intrinsic-value" in en
    assert "analyst target" in en
    assert "資料不足以做出" in zh
    assert "內在價值" in zh
    assert "分析師目標價" in zh


def test_prompt_appends_json_payload() -> None:
    payload = _tw_payload(language="en")
    prompt = build_stock_analysis_user_prompt(payload)

    assert "Computed stock data JSON:" in prompt
    assert '"stockId": "TW:2330"' in prompt
    assert '"language": "en"' in prompt


def test_fundamental_outlook_is_its_own_section_before_valuation() -> None:
    en = build_stock_analysis_user_prompt(_tw_payload(language="en"))
    zh = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    assert en.index("## Fundamental outlook") < en.index("## Valuation context")
    assert zh.index("## 基本面展望") < zh.index("## 估值背景")
    # The merged heading from the first implementation must be gone.
    assert "## Valuation / fundamental outlook" not in en
    assert "## 估值與基本面展望" not in zh


def test_prompt_names_the_source_block_for_each_section() -> None:
    en = build_stock_analysis_user_prompt(_tw_payload(language="en"))
    zh = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    for block in (
        "technicalAnalysis",
        "institutionalAnalysis",
        "fundamentalOutlook",
        "recentNewsContext",
        "missingInputs",
    ):
        assert block in en
        assert block in zh


def test_prompt_requires_mixed_signals_and_period_disclosure() -> None:
    en = build_stock_analysis_user_prompt(_tw_payload(language="en"))
    zh = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    assert "call the picture mixed" in en
    assert "technicalAnalysis.dataAsOf" in en
    assert "volatility" in en
    assert "drawdown" in en
    assert "訊號分歧" in zh
    assert "technicalAnalysis.dataAsOf" in zh
    assert "波動度" in zh
    assert "最大回落" in zh


def test_prompt_requires_reconciling_evidence_layers() -> None:
    available_news = {
        "available": True,
        "items": [{"id": "N1", "title": "Headline", "source": "Example", "url": "u"}],
    }
    en = build_stock_analysis_user_prompt(
        _tw_payload(
            language="en",
            institutionalAnalysis={"available": True},
            recentNewsContext=available_news,
        )
    )
    zh = build_stock_analysis_user_prompt(
        _tw_payload(
            language="zh-TW",
            institutionalAnalysis={"available": True},
            recentNewsContext=available_news,
        )
    )

    assert "supports or conflicts with the price trend" in en
    assert "agree, conflict, or cannot yet be connected" in en
    assert "positive headline does not by itself" in en
    assert "相互支持還是彼此矛盾" in zh
    assert "無法連結" in zh
    assert "正面標題本身不足以" in zh


def test_prompt_forbids_inventing_key_price_levels() -> None:
    en = build_stock_analysis_user_prompt(_tw_payload(language="en"))
    zh = build_stock_analysis_user_prompt(_us_payload(language="zh-TW"))

    assert "technicalAnalysis.keyPriceLevels" in en
    assert "must NOT invent, move, or strengthen levels" in en
    assert "watch zones, not guarantees" in en
    assert "keyPriceLevels.dataAsOf" in en
    assert "## Support and resistance" not in en

    assert "technicalAnalysis.keyPriceLevels" in zh
    assert "不可捏造、移動或強化價位" in zh
    assert "觀察區間而非保證轉折價" in zh
    assert "keyPriceLevels.dataAsOf" in zh
    assert "## 支撐與壓力" not in zh


def test_openai_and_perplexity_build_the_same_prompt() -> None:
    from app.services.ai_providers import openai_provider, perplexity_provider

    assert (
        openai_provider.build_stock_analysis_user_prompt
        is perplexity_provider.build_stock_analysis_user_prompt
        is build_stock_analysis_user_prompt
    )
    assert openai_provider.SYSTEM_PROMPT is perplexity_provider.SYSTEM_PROMPT is SYSTEM_PROMPT
