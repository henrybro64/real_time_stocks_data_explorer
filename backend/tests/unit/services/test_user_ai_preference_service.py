import pytest

from app.services.user_ai_preference_service import (
    UserAiPreferenceService,
    _normalize_provider,
)


def test_normalize_provider_accepts_supported_names() -> None:
    assert _normalize_provider("Perplexity") == "perplexity"
    assert _normalize_provider("openai") == "openai"


def test_normalize_provider_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported provider"):
        _normalize_provider("anthropic")
