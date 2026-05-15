from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.ai_providers.base import (
    AIAnalysisResult,
    AIProvider,
    AIProviderError,
    AnalysisCitation,
    SYSTEM_PROMPT,
    build_portfolio_analysis_user_prompt,
    build_stock_analysis_user_prompt,
)


class PerplexityProvider(AIProvider):
    provider_name = "perplexity"

    def __init__(self, api_key: str, model_name: str) -> None:
        self._api_key = api_key
        self.model_name = model_name

    def generate_stock_analysis(
        self,
        input_data: dict[str, object],
    ) -> AIAnalysisResult:
        return self._generate_analysis(build_stock_analysis_user_prompt(input_data))

    def generate_portfolio_analysis(
        self,
        input_data: dict[str, object],
    ) -> AIAnalysisResult:
        return self._generate_analysis(build_portfolio_analysis_user_prompt(input_data))

    def _generate_analysis(self, user_prompt: str) -> AIAnalysisResult:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        }
        request = Request(
            "https://api.perplexity.ai/chat/completions",
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self._api_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        body = json.dumps(payload).encode("utf-8")
        try:
            with urlopen(request, data=body, timeout=20) as response:
                data = json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AIProviderError("Perplexity request failed.") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIProviderError("Perplexity returned no analysis.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("Perplexity returned an empty analysis.")

        citations = tuple(
            AnalysisCitation(url=url)
            for url in data.get("citations", [])
            if isinstance(url, str) and url.strip()
        )
        return AIAnalysisResult(
            provider=self.provider_name,
            model=self.model_name,
            content=content.strip(),
            citations=citations,
        )
