from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.key_stats_service import get_key_statistics
from app.services.api_key_service import api_key_service
from app.services.quote_service import quote_service


class AiInsightService:
    def generate_insight(self, user_id: str, stock_id: str, provider: str) -> str:
        stock = quote_service.get_stock(stock_id)
        quote = quote_service.get_quote(stock_id)
        if stock is None or quote is None:
            raise ValueError("Stock not found")

        api_key = api_key_service.get_key(user_id, provider)
        if not api_key:
            raise ValueError(f"{provider} API key is not configured")

        key_stats = get_key_statistics(stock_id) or {}
        prompt = _build_prompt(
            stock_id=stock_id,
            market=stock.market,
            symbol=stock.symbol,
            exchange=stock.exchange,
            name=stock.name,
            currency=stock.currency,
            last_price=quote.last_price,
            change=quote.change,
            change_percent=quote.change_percent,
            volume=quote.volume,
            key_stats=key_stats,
        )

        normalized = provider.strip().lower()
        if normalized == "openai":
            return _call_openai(prompt, api_key)
        if normalized == "perplexity":
            return _call_perplexity(prompt, api_key)
        raise ValueError(f"Unsupported provider: {provider}")


def _call_openai(prompt: str, api_key: str) -> str:
    payload = {
        "model": "gpt-4o-mini",
        "input": prompt,
    }
    request = Request("https://api.openai.com/v1/responses", method="POST")
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    body = json.dumps(payload).encode("utf-8")
    try:
        with urlopen(request, data=body, timeout=20) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("OpenAI request failed") from exc
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return "No insight returned."


def _call_perplexity(prompt: str, api_key: str) -> str:
    payload = {
        "model": "sonar",
        "messages": [
            {
                "role": "system",
                "content": "You are a concise financial assistant.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    request = Request(
        "https://api.perplexity.ai/chat/completions",
        method="POST",
    )
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    body = json.dumps(payload).encode("utf-8")
    try:
        with urlopen(request, data=body, timeout=20) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("Perplexity request failed") from exc
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return "No insight returned."


ai_insight_service = AiInsightService()


def _build_prompt(
    *,
    stock_id: str,
    market: str,
    symbol: str,
    exchange: str,
    name: str,
    currency: str,
    last_price: float,
    change: float,
    change_percent: float,
    volume: int | None,
    key_stats: dict[str, object],
) -> str:
    base = (
        f"Stock ID: {stock_id}\n"
        f"Symbol: {symbol}\n"
        f"Exchange: {exchange}\n"
        f"Name: {name}\n"
        f"Currency: {currency}\n"
        f"Last price: {last_price}\n"
        f"Change: {change} ({change_percent}%)\n"
        f"Volume: {volume}\n"
        f"Key stats JSON: {json.dumps(key_stats, ensure_ascii=False)}\n"
    )

    if market == "TW":
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
