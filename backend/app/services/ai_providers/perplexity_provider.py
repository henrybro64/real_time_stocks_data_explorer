from __future__ import annotations

import logging
from time import perf_counter

from app.core.diagnostics import elapsed_ms
from app.services.ai_providers.base import (
    AIAnalysisResult,
    AIProvider,
    AIProviderError,
    AnalysisCitation,
    SYSTEM_PROMPT,
    build_portfolio_analysis_user_prompt,
    build_stock_analysis_user_prompt,
)
from app.services.http_client import HttpFetchError, fetch_json

logger = logging.getLogger(__name__)

_AI_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


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
        started_at = perf_counter()
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
        try:
            data = fetch_json(
                "https://api.perplexity.ai/chat/completions",
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
                operation_name="perplexity_chat_completions",
                resource_id=self.model_name,
            )
        except HttpFetchError as exc:
            logger.warning(
                "Perplexity request failed",
                extra={
                    "event": "ai_provider.request.failed",
                    "provider": self.provider_name,
                    "model": self.model_name,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise AIProviderError("Perplexity request failed.") from exc

        if not isinstance(data, dict):
            raise AIProviderError("Perplexity returned no analysis.")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIProviderError("Perplexity returned no analysis.")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("Perplexity returned an empty analysis.")

        citations = tuple(
            AnalysisCitation(url=url)
            for url in data.get("citations", [])
            if isinstance(url, str) and url.strip()
        )
        logger.info(
            "Perplexity request completed",
            extra={
                "event": "ai_provider.request.completed",
                "provider": self.provider_name,
                "model": self.model_name,
                "duration_ms": elapsed_ms(started_at),
                "citation_count": len(citations),
            },
        )
        return AIAnalysisResult(
            provider=self.provider_name,
            model=self.model_name,
            content=content.strip(),
            citations=citations,
        )
