from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol


SYSTEM_PROMPT = (
    "You are a plain-language equity research assistant for non-finance readers. "
    "Use ONLY the provided computed JSON as the primary source of truth. "
    "Do not invent prices, news details, institutional trends, key price levels, "
    "or valuation inputs that are missing from the payload. "
    "Clearly distinguish observed facts from interpretation, and use uncertainty language "
    "(may, suggests, possibly). "
    "News headlines in the payload are metadata only (titles, sources, dates, URLs)—not full articles. "
    "This content is informational only and is not investment advice."
)


@dataclass(frozen=True)
class AnalysisCitation:
    url: str
    title: str | None = None
    source: str | None = None
    published_at: str | None = None


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
    language = _resolve_stock_analysis_language(input_data)
    payload = json.dumps(input_data, ensure_ascii=False, indent=2, sort_keys=True)
    base = f"Computed stock data JSON:\n{payload}\n"
    include_institutional = _is_institutional_available(input_data)
    include_news = _is_news_context_available(input_data)

    if language == "zh-TW":
        return _build_zh_tw_stock_analysis_prompt(
            base=base,
            include_institutional=include_institutional,
            include_news=include_news,
        )
    return _build_en_stock_analysis_prompt(
        base=base,
        include_institutional=include_institutional,
        include_news=include_news,
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


def _resolve_stock_analysis_language(input_data: Mapping[str, object]) -> str:
    """Return ``en`` or ``zh-TW`` from ``input_data['language']``.

    Callers normalize before reaching here; this repeats the normalization so an
    unexpected value falls back to the default instead of leaking into the prompt.
    """
    from app.services.stock_analysis_language import (  # pylint: disable=import-outside-toplevel
        DEFAULT_ANALYSIS_LANGUAGE,
        normalize_analysis_language,
    )

    language = input_data.get("language")
    if not isinstance(language, str):
        return DEFAULT_ANALYSIS_LANGUAGE
    try:
        return normalize_analysis_language(language)
    except ValueError:
        return DEFAULT_ANALYSIS_LANGUAGE


def _is_institutional_available(input_data: Mapping[str, object]) -> bool:
    institutional = input_data.get("institutionalAnalysis")
    if not isinstance(institutional, Mapping):
        return False
    return institutional.get("available") is True


def _is_news_context_available(input_data: Mapping[str, object]) -> bool:
    news = input_data.get("recentNewsContext")
    if not isinstance(news, Mapping):
        return False
    if news.get("available") is not True:
        return False
    items = news.get("items")
    return isinstance(items, list) and len(items) > 0


def _build_en_stock_analysis_prompt(
    *,
    base: str,
    include_institutional: bool,
    include_news: bool,
) -> str:
    sections = [
        "## Plain-language summary",
        "## Price trend and momentum",
        "## Moving averages",
        "## Key price levels",
    ]
    if include_institutional:
        sections.append("## Institutional activity")
    if include_news:
        sections.append("## Recent news context")
    sections.extend(
        [
            "## Possible entry considerations",
            "## Possible exit/risk considerations",
            "## Fundamental outlook",
            "## Valuation context",
            "## What could make this analysis wrong",
            "## Disclaimer",
        ]
    )
    section_block = "\n".join(sections)

    news_instruction = (
        "Include a ## Recent news context section. Cite each mentioned headline by its "
        "reference id (N1, N2, …) together with its source and publication date. "
        "Say a headline may have contributed to attention or risk—not that it caused a "
        "price move. Do not invent article details beyond the supplied title metadata, "
        "and do not claim you read the full article. News must not override contradictory "
        "price or volume data. A positive headline does not by itself justify a bullish "
        "conclusion, and a negative headline does not by itself justify a bearish one; if a "
        "headline is ambiguous, say so or leave it out. State plainly whether the news and "
        "the price signals broadly agree, conflict, or cannot yet be connected.\n"
        if include_news
        else (
            "Omit the Recent news context section. In the summary and/or disclaimer, briefly "
            "state that this analysis is based on market data only because reliable recent "
            "news context is unavailable.\n"
        )
    )
    institutional_instruction = (
        "Include a ## Institutional activity section using only the supplied Taiwan "
        "institutionalAnalysis facts. Explain foreign investors (外資), investment trusts "
        "(投信), dealers (自營商), institutional net buying/selling (法人買賣超), and 張 "
        "(backend-normalized trading-lot unit) in plain English the first time they appear. "
        "Say whether this institutional activity supports or conflicts with the price trend "
        "from technicalAnalysis.\n"
        if include_institutional
        else (
            "Omit the Institutional activity section entirely—do not invent institutional "
            "trends when institutionalAnalysis.available is not true.\n"
        )
    )

    return (
        "Respond in English only. Write for a reader without finance training.\n\n"
        "Use ONLY the computed JSON below as primary truth. Do not invent prices, news "
        "details, institutional trends, key price levels, or valuation inputs.\n"
        "Distinguish facts from interpretation. Use uncertainty language (may, suggests, "
        "possibly). Never state that the user should definitely buy or sell. This is "
        "informational only and not investment advice.\n\n"
        "Keep the whole response short: about 700 English words maximum. "
        "Plain-language summary: 2–3 sentences. Other sections: 1–3 short bullets each.\n\n"
        "Which JSON block feeds which section:\n"
        "- technicalAnalysis is the source of truth for price trend, momentum, moving "
        "averages, and key price levels. Do not recompute indicators yourself.\n"
        "- institutionalAnalysis is the only source for Taiwan institutional activity.\n"
        "- fundamentalOutlook is the only source for the Fundamental outlook section.\n"
        "- recentNewsContext is the only source for news statements.\n"
        "- Anything listed in missingInputs or marked unavailable must be described as "
        "missing, never filled in from memory or from another block.\n\n"
        "Writing rules:\n"
        "- Lead with the conclusion, then evidence. Short sentences and common words.\n"
        "- First time you use SMA, call it the simple moving average "
        "(e.g. SMA10 = 10-day simple moving average). First time you use RSI, explain it as "
        "a momentum indicator from 0 to 100; never claim one threshold guarantees a reversal. "
        "Explain volatility as how much the price swings and drawdown as the drop from a "
        "previous peak, the first time each appears.\n"
        "- Open by stating the analysis period and the data as-of date from "
        "technicalAnalysis.period and technicalAnalysis.dataAsOf in everyday wording.\n"
        "- When the moving averages, momentum, and volume disagree, call the picture mixed. "
        "Do not force a bullish or bearish verdict.\n"
        "- Key price levels must come only from technicalAnalysis.keyPriceLevels. Show each "
        "zone as a price range with the supplied currency. Explain the listed evidence in "
        "plain language. These are watch zones, not guarantees. You may omit weak zones. "
        "You must NOT invent, move, or strengthen levels. Mention keyPriceLevels.dataAsOf. "
        "Use conditional break language only (e.g. if price holds / if price breaks).\n"
        "- Keep the Fundamental outlook section about the business (revenue, earnings, "
        "estimates, dividends) and separate from the chart. A strong chart can coexist with "
        "weak fundamentals and the reverse. Label historical results, company guidance, and "
        "analyst consensus distinctly, and never present your own reading as either.\n"
        "- Never invent an intrinsic value. If valuation or fundamental inputs are "
        "insufficient, say plainly that available data is insufficient for a defensible "
        "intrinsic-value calculation. Never treat analyst target prices as intrinsic value "
        "or as company guidance. A lower forward P/E alone does not prove earnings will "
        "grow—estimates change.\n"
        f"- {institutional_instruction}"
        f"- {news_instruction}"
        "\n"
        "Structure the response with these exact section headings (omit gated sections "
        "exactly as instructed above):\n"
        f"{section_block}\n\n"
        f"{base}"
    )


def _build_zh_tw_stock_analysis_prompt(
    *,
    base: str,
    include_institutional: bool,
    include_news: bool,
) -> str:
    sections = [
        "## 白話摘要",
        "## 價格趨勢與動能",
        "## 移動平均線",
        "## 關鍵價位",
    ]
    if include_institutional:
        sections.append("## 法人動向")
    if include_news:
        sections.append("## 近期新聞背景")
    sections.extend(
        [
            "## 可能的進場考量",
            "## 可能的出場與風險考量",
            "## 基本面展望",
            "## 估值背景",
            "## 可能讓分析失準的情況",
            "## 免責聲明",
        ]
    )
    section_block = "\n".join(sections)

    news_instruction = (
        "請輸出 ## 近期新聞背景。提及新聞時須以參考編號（N1、N2…）並附上來源與發布日期。"
        "只能說該標題「可能是影響因素之一」，不可僅因時間接近就斷定造成股價變動。"
        "不可捏造標題以外的文章細節，也不可宣稱已閱讀全文。"
        "新聞解讀不得覆蓋與之矛盾的價格或成交量資料。"
        "正面標題本身不足以推出看多結論，負面標題本身也不足以推出看空結論；"
        "若標題語意不明，請說明或直接略過。"
        "並請明確說明新聞與價格訊號是大致一致、彼此矛盾，或目前還無法連結。\n"
        if include_news
        else (
            "請省略「近期新聞背景」段落。並在摘要或免責聲明中簡短說明："
            "因缺乏可靠近期新聞背景，本次分析僅依據市場資料。\n"
        )
    )
    institutional_instruction = (
        "請輸出 ## 法人動向，且只能使用提供的 institutionalAnalysis 事實。"
        "首次出現時請用繁體中文說明：外資、投信、自營商、法人買賣超，以及「張」"
        "（後端正規化後的交易單位）。"
        "並請說明法人動向與 technicalAnalysis 的價格趨勢是相互支持還是彼此矛盾。\n"
        if include_institutional
        else (
            "請完全省略「法人動向」段落——當 institutionalAnalysis.available 不為 true 時，"
            "不可捏造法人趨勢。\n"
        )
    )

    return (
        "請使用繁體中文輸出，寫給沒有金融背景的讀者。\n\n"
        "只能以下方已計算的 JSON 作為主要事實來源。不可捏造價格、新聞細節、法人趨勢、"
        "關鍵價位或估值輸入。\n"
        "請清楚區分事實與解讀，並使用不確定用語（可能、顯示、或許）。"
        "不可斷言使用者「應該」買進或賣出。內容僅供參考，並非投資建議。\n\n"
        "全文請保持簡短：約等同於英文 700 字以內的繁中篇幅。"
        "白話摘要：2–3 句。其餘各段：1–3 則短要點。\n\n"
        "各段落對應的 JSON 區塊：\n"
        "- technicalAnalysis 是價格趨勢、動能、移動平均線與關鍵價位的唯一事實來源，"
        "請勿自行重算指標。\n"
        "- institutionalAnalysis 是台股法人動向的唯一來源。\n"
        "- fundamentalOutlook 是「基本面展望」段落的唯一來源。\n"
        "- recentNewsContext 是新聞敘述的唯一來源。\n"
        "- 列在 missingInputs 或標示為不可用的項目，一律說明為缺少資料，"
        "不可憑記憶或用其他區塊補齊。\n\n"
        "寫作規則：\n"
        "- 先給結論，再說明依據；句子要短，用詞要平常。\n"
        "- 首次出現 SMA 時須說明為簡單移動平均線（例如 SMA10＝10 日簡單移動平均線）；"
        "首次出現 RSI 時須說明為 0 到 100 的動能指標，不可宣稱某一門檻保證反轉；"
        "首次出現波動度時請說明為價格上下擺動的幅度，最大回落請說明為自前波高點下跌的幅度。\n"
        "- 開頭請用日常說法交代分析期間與資料截止日"
        "（取自 technicalAnalysis.period 與 technicalAnalysis.dataAsOf）。\n"
        "- 當移動平均線、動能與成交量訊號彼此不一致時，請直接說是「訊號分歧」，"
        "不要硬套看多或看空。\n"
        "- 關鍵價位只能來自 technicalAnalysis.keyPriceLevels。請以提供的幣別顯示價位區間，"
        "並用白話說明所列證據。這些是觀察區間而非保證轉折價。可省略弱勢區間。"
        "不可捏造、移動或強化價位。請提及 keyPriceLevels.dataAsOf。"
        "突破／守住僅能用條件句（例如：若守住／若跌破）。\n"
        "- 「基本面展望」請聚焦公司營運（營收、獲利、預估、股利），與技術線型分開陳述；"
        "線型強勢與基本面疲弱可能同時存在，反之亦然。"
        "請分別標示歷史結果、公司財測與分析師共識，且不可把你的解讀當成其中任何一種。\n"
        "- 不可捏造內在價值。若估值或基本面輸入不足，請明白說明目前資料不足以做出"
        "可靠的內在價值估算。不可把分析師目標價當成內在價值或公司指引。"
        "預估本益比較低本身並不能證明獲利會成長，預估值是會變動的。\n"
        f"- {institutional_instruction}"
        f"- {news_instruction}"
        "\n"
        "請依下列標題輸出（依上方指示省略不適用的段落）：\n"
        f"{section_block}\n\n"
        f"{base}"
    )


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
