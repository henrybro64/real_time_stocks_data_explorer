from __future__ import annotations

from app.core.config import settings
from app.services.ai_providers.base import AIProvider, AIProviderConfigurationError
from app.services.ai_providers.openai_provider import OpenAIProvider
from app.services.ai_providers.perplexity_provider import PerplexityProvider

SUPPORTED_PROVIDER_NAMES = {"openai", "anthropic", "gemini", "perplexity", "ollama"}


def create_ai_provider() -> AIProvider | None:
    provider_name = settings.ai_provider.strip().lower()
    if not provider_name:
        return None
    if provider_name not in SUPPORTED_PROVIDER_NAMES:
        raise AIProviderConfigurationError(
            f"Unsupported AI provider configured: {settings.ai_provider}.",
        )
    if provider_name == "openai":
        if not settings.openai_api_key.strip():
            raise AIProviderConfigurationError(
                "AI_PROVIDER=openai is configured but OPENAI_API_KEY is missing.",
            )
        return OpenAIProvider(
            api_key=settings.openai_api_key.strip(),
            model_name=settings.ai_model.strip() or "gpt-4o-mini",
        )
    if provider_name == "perplexity":
        if not settings.perplexity_api_key.strip():
            raise AIProviderConfigurationError(
                "AI_PROVIDER=perplexity is configured but PERPLEXITY_API_KEY is missing.",
            )
        return PerplexityProvider(
            api_key=settings.perplexity_api_key.strip(),
            model_name=settings.ai_model.strip() or "sonar",
        )
    raise AIProviderConfigurationError(
        f"AI provider '{provider_name}' is not implemented yet in this build.",
    )
