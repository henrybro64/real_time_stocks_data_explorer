import pytest

from app.services import user_ai_provider_service as service


def test_resolve_user_ai_provider_returns_none_without_user() -> None:
    assert service.resolve_user_ai_provider(None) is None


def test_resolve_user_ai_provider_uses_preferred_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        service.api_key_service,
        "get_key",
        lambda user_id, provider: "sk-test",
    )

    captured: dict[str, str] = {}

    def fake_create_ai_provider_from_credentials(
        provider_name: str,
        api_key: str,
    ):
        captured["provider"] = provider_name
        captured["api_key"] = api_key
        return object()

    monkeypatch.setattr(
        service,
        "create_ai_provider_from_credentials",
        fake_create_ai_provider_from_credentials,
    )

    result = service.resolve_user_ai_provider("user-1", "openai")

    assert result is not None
    assert captured == {"provider": "openai", "api_key": "sk-test"}


def test_resolve_user_ai_provider_raises_when_preferred_key_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service.api_key_service,
        "get_key",
        lambda user_id, provider: None,
    )

    with pytest.raises(ValueError, match="openai API key is not configured"):
        service.resolve_user_ai_provider("user-1", "openai")


def test_resolve_user_ai_provider_falls_back_to_first_available(monkeypatch) -> None:
    def fake_get_key(user_id: str, provider: str) -> str | None:
        return "pplx-key" if provider == "perplexity" else None

    monkeypatch.setattr(service.api_key_service, "get_key", fake_get_key)
    monkeypatch.setattr(
        service,
        "create_ai_provider_from_credentials",
        lambda provider_name, api_key: {
            "provider": provider_name,
            "api_key": api_key,
        },
    )

    result = service.resolve_user_ai_provider("user-1")

    assert result == {"provider": "perplexity", "api_key": "pplx-key"}


def test_resolve_user_ai_provider_uses_stored_preference_over_default_order(
    monkeypatch,
) -> None:
    def fake_get_key(user_id: str, provider: str) -> str | None:
        if provider in {"openai", "perplexity"}:
            return f"key-{provider}"
        return None

    monkeypatch.setattr(service.api_key_service, "get_key", fake_get_key)
    monkeypatch.setattr(
        service.user_ai_preference_service,
        "get_preferred_provider",
        lambda user_id: "perplexity",
    )
    monkeypatch.setattr(
        service,
        "create_ai_provider_from_credentials",
        lambda provider_name, api_key: {
            "provider": provider_name,
            "api_key": api_key,
        },
    )

    result = service.resolve_user_ai_provider("user-1")

    assert result == {"provider": "perplexity", "api_key": "key-perplexity"}
