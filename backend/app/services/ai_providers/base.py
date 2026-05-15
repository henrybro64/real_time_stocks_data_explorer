from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol


SYSTEM_PROMPT = (
    "You are a senior equity research assistant. "
    "Use the provided computed stock metrics as the primary source of truth. "
    "Use clear assumptions, show your reasoning structure, and be explicit about uncertainty. "
    "If recent news context cannot be verified, explicitly state that analysis is based on "
    "historical price and valuation data only. "
    "Always include a clear statement that this is informational only and not investment advice."
)


@dataclass(frozen=True)
class AnalysisCitation:
    url: str
    title: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class AnalysisSearchResult:
    title: str
    url: str
    source: str | None = None


@dataclass(frozen=True)
class AIAnalysisResult:
    provider: str
    model: str
    content: str
    citations: tuple[AnalysisCitation, ...] = ()
    search_results: tuple[AnalysisSearchResult, ...] = ()


class AIProviderError(RuntimeError):
    pass


class AIProviderConfigurationError(AIProviderError):
    pass


class AIProvider(Protocol):
    provider_name: str
    model_name: str

    def generate_stock_analysis(
        self,
        input_data: Mapping[str, object],
    ) -> AIAnalysisResult: ...

    def generate_portfolio_analysis(
        self,
        input_data: Mapping[str, object],
    ) -> AIAnalysisResult: ...


def build_stock_analysis_user_prompt(input_data: Mapping[str, object]) -> str:
    payload = json.dumps(input_data, ensure_ascii=False, indent=2, sort_keys=True)
    base = f"Computed stock data JSON:\n{payload}\n"
    if _is_tw_market(input_data):
        return (
            "你是資深股票研究分析助理。請使用繁體中文輸出，"
            "並依據提供資料與近期市場常見分析方法，給出結構化回答。"
            "請明確標示這不是投資建議。\n\n"
            "請回答以下三題：\n"
            "1) 請以近期收集的數據跟你自己的分析一下進場的買點?\n"
            "2) 請以近期收集的數據跟你自己分析一下出場的賣點?\n"
            "3) 依據巴菲特價值投資哲學，估算每股內在價值（Value per share）。"
            "請列出假設、計算步驟與估值區間。\n\n"
            "輸出格式（繁中）：\n"
            "## 進場買點\n"
            "- 建議買入區間\n"
            "- 觸發條件\n"
            "- 風險提醒\n"
            "## 出場賣點\n"
            "- 建議賣出區間\n"
            "- 觸發條件\n"
            "- 風險提醒\n"
            "## 巴菲特哲學每股價值估算\n"
            "- 估值假設\n"
            "- 推導摘要\n"
            "- Value per share（區間）\n"
            "- 安全邊際建議\n\n"
            f"{base}"
        )
    return (
        "You are a senior equity research assistant. "
        "Respond in English only, using recent-analysis style reasoning "
        "from the provided data and common "
        "market frameworks. Clearly state this is not investment advice.\n\n"
        "Answer these exactly:\n"
        "1)Based on the analysis in the market and your own analysis,"
        "what is the suggested buying price for this stock "
        "according to recent analysis?\n"
        "2)Based on the analysis in the market and your own analysis,"
        "what is the suggested selling price for this stock "
        "according to recent analysis?\n"
        "3) For this stock, what is the value per share according to "
        "Warren Buffett's calculation philosophy? Include assumptions "
        "and an estimated range.\n\n"
        "Output format (English):\n"
        "## Suggested Buying Price\n"
        "- Suggested buy range\n"
        "- Trigger conditions\n"
        "- Risk notes\n"
        "## Suggested Selling Price\n"
        "- Suggested sell range\n"
        "- Trigger conditions\n"
        "- Risk notes\n"
        "## Buffett-Style Value per Share\n"
        "- Valuation assumptions\n"
        "- Derivation summary\n"
        "- Estimated value per share (range)\n"
        "- Margin-of-safety comment\n\n"
        f"{base}"
    )


def build_portfolio_analysis_user_prompt(input_data: Mapping[str, object]) -> str:
    payload = json.dumps(input_data, ensure_ascii=False, indent=2, sort_keys=True)
    base = f"Backend-computed portfolio summary JSON:\n{payload}\n"
    if _is_tw_portfolio(input_data):
        return (
            "你是資深投資組合分析助理。請使用繁體中文輸出，"
            "且只能依據後端已計算好的投資組合資料做解讀。"
            "不要重新從零計算持倉總值，也不要假設缺漏報價的即時價格。"
            "請清楚指出任何不確定性，並明確標示這不是投資建議。\n\n"
            "請依下列段落輸出：\n"
            "## Portfolio Summary\n"
            "## Concentration Risk\n"
            "## Currency Exposure\n"
            "## Largest Holdings\n"
            "## Unrealized Gain/Loss Review\n"
            "## Stock-by-stock Comments\n"
            "## Possible Actions\n"
            "## Risks to Monitor\n"
            "## Disclaimer\n\n"
            "請明確引用後端提供的集中度、幣別曝險、最大持股與未實現損益資料。\n\n"
            f"{base}"
        )
    return (
        "You are a senior portfolio analysis assistant. "
        "Use only the backend-computed portfolio summary as the source of truth. "
        "Do not recalculate raw portfolio values from scratch, and do not invent live prices "
        "for skipped holdings. Be explicit about uncertainty and clearly state that this is not "
        "financial advice.\n\n"
        "Structure the response with these exact sections:\n"
        "## Portfolio Summary\n"
        "## Concentration Risk\n"
        "## Currency Exposure\n"
        "## Largest Holdings\n"
        "## Unrealized Gain/Loss Review\n"
        "## Stock-by-stock Comments\n"
        "## Possible Actions\n"
        "## Risks to Monitor\n"
        "## Disclaimer\n\n"
        "Explicitly reference the backend-provided concentration, currency exposure, largest "
        "holdings, and unrealized gain/loss data.\n\n"
        f"{base}"
    )


def _is_tw_market(input_data: Mapping[str, object]) -> bool:
    market = input_data.get("market")
    if isinstance(market, str) and market.strip().upper() == "TW":
        return True

    stock_id = input_data.get("stockId")
    if isinstance(stock_id, str) and stock_id.strip().upper().startswith("TW:"):
        return True

    ticker = input_data.get("ticker")
    if isinstance(ticker, str):
        normalized = ticker.strip().upper()
        if normalized.endswith(".TW") or normalized.endswith(".TWO"):
            return True
    return False


def _is_tw_portfolio(input_data: Mapping[str, object]) -> bool:
    holdings = input_data.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return False

    tw_like = 0
    for holding in holdings:
        if not isinstance(holding, Mapping):
            continue
        stock_id = holding.get("stockId")
        market = holding.get("market")
        currency = holding.get("currency")
        if isinstance(stock_id, str) and stock_id.strip().upper().startswith("TW:"):
            tw_like += 1
            continue
        if isinstance(market, str) and market.strip().upper() == "TW":
            tw_like += 1
            continue
        if isinstance(currency, str) and currency.strip().upper() == "TWD":
            tw_like += 1

    return tw_like == len(holdings)
