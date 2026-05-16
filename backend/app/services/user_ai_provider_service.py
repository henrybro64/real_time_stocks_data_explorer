from __future__ import annotations

from app.services.ai_providers import (
    AIProvider,
    create_ai_provider_from_credentials,
)
from app.services.api_key_service import SUPPORTED_PROVIDERS, api_key_service
from app.services.user_ai_preference_service import user_ai_preference_service


DEFAULT_PROVIDER_ORDER = ("openai", "perplexity")


def resolve_user_ai_provider(
    user_id: str | None,
    preferred_provider: str | None = None,
) -> AIProvider | None:
    if not user_id:
        return None

    if preferred_provider is not None:
        return _provider_from_user_key(
            user_id,
            _normalize_provider(preferred_provider),
            require_configured=True,
        )

    stored_preference = user_ai_preference_service.get_preferred_provider(user_id)
    if stored_preference is not None:
        provider = _provider_from_user_key(
            user_id,
            stored_preference,
            require_configured=False,
        )
        if provider is not None:
            return provider

    for provider_name in DEFAULT_PROVIDER_ORDER:
        provider = _provider_from_user_key(
            user_id,
            provider_name,
            require_configured=False,
        )
        if provider is not None:
            return provider
    return None


def _provider_from_user_key(
    user_id: str,
    provider_name: str,
    *,
    require_configured: bool,
) -> AIProvider | None:
    api_key = api_key_service.get_key(user_id, provider_name)
    if not api_key:
        if require_configured:
            raise ValueError(f"{provider_name} API key is not configured")
        return None
    return create_ai_provider_from_credentials(
        provider_name=provider_name,
        api_key=api_key,
    )


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized
