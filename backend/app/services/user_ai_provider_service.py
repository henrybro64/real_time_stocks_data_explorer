from __future__ import annotations

from app.services.ai_providers import (
    AIProvider,
    create_ai_provider_from_credentials,
)
from app.services.api_key_service import SUPPORTED_PROVIDERS, api_key_service


DEFAULT_PROVIDER_ORDER = ("openai", "perplexity")


def resolve_user_ai_provider(
    user_id: str | None,
    preferred_provider: str | None = None,
) -> AIProvider | None:
    if not user_id:
        return None

    if preferred_provider is not None:
        normalized = _normalize_provider(preferred_provider)
        api_key = api_key_service.get_key(user_id, normalized)
        if not api_key:
            raise ValueError(f"{normalized} API key is not configured")
        return create_ai_provider_from_credentials(
            provider_name=normalized,
            api_key=api_key,
        )

    for provider_name in DEFAULT_PROVIDER_ORDER:
        api_key = api_key_service.get_key(user_id, provider_name)
        if api_key:
            return create_ai_provider_from_credentials(
                provider_name=provider_name,
                api_key=api_key,
            )
    return None


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized
