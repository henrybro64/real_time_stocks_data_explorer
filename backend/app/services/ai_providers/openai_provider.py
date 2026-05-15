from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.ai_providers.base import (
    AIAnalysisResult,
    AIProvider,
    AIProviderError,
    SYSTEM_PROMPT,
    build_portfolio_analysis_user_prompt,
    build_stock_analysis_user_prompt,
)


class OpenAIProvider(AIProvider):
    provider_name = "openai"

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
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_prompt,
                        }
                    ],
                },
            ],
        }
        request = Request("https://api.openai.com/v1/responses", method="POST")
        request.add_header("Authorization", f"Bearer {self._api_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        body = json.dumps(payload).encode("utf-8")
        try:
            with urlopen(request, data=body, timeout=20) as response:
                data = json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AIProviderError("OpenAI request failed.") from exc
        content = data.get("output_text")
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("OpenAI returned an empty analysis.")
        return AIAnalysisResult(
            provider=self.provider_name,
            model=self.model_name,
            content=content.strip(),
        )
