from __future__ import annotations

import logging
from time import perf_counter

from app.core.diagnostics import elapsed_ms
from app.services.ai_providers.base import (
    AIAnalysisResult,
    AIProvider,
    AIProviderError,
    SYSTEM_PROMPT,
    build_portfolio_analysis_user_prompt,
    build_stock_analysis_user_prompt,
)
from app.services.http_client import HttpFetchError, fetch_json

logger = logging.getLogger(__name__)

_AI_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


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
        started_at = perf_counter()
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
        try:
            data = fetch_json(
                "https://api.openai.com/v1/responses",
                method="POST",
                json_body=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout_seconds=20,
                retries=1,
                retryable_status_codes=_AI_RETRYABLE_STATUS_CODES,
                provider_name=self.provider_name,
                operation_name="openai_responses",
                resource_id=self.model_name,
            )
        except HttpFetchError as exc:
            logger.warning(
                "OpenAI request failed",
                extra={
                    "event": "ai_provider.request.failed",
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise AIProviderError("OpenAI request failed.") from exc
        if not isinstance(data, dict):
            raise AIProviderError("OpenAI returned an empty analysis.")
        content = data.get("output_text")
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("OpenAI returned an empty analysis.")
        logger.info(
            "OpenAI request completed",
            extra={
                "event": "ai_provider.request.completed",
                "provider": self.provider_name,
                "model": self.model_name,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        return AIAnalysisResult(
            provider=self.provider_name,
            model=self.model_name,
            content=content.strip(),
        )
