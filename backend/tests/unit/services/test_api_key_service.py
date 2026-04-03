from types import SimpleNamespace

import pytest

from app.services.api_key_service import ApiKeyService


def test_normalize_provider_accepts_case_and_whitespace() -> None:
    assert ApiKeyService._normalize_provider("  OpenAI ") == "openai"


def test_normalize_provider_rejects_unsupported_values() -> None:
    with pytest.raises(ValueError, match="Unsupported provider"):
        ApiKeyService._normalize_provider("anthropic")


def test_get_status_masks_last_four_characters(monkeypatch) -> None:
    service = ApiKeyService()
    monkeypatch.setattr(
        service,
        "_get_row",
        lambda provider: SimpleNamespace(api_key="sk-test-1234"),
    )

    assert service.get_status("openai") == (True, "1234")


def test_get_status_returns_not_configured_when_row_missing(monkeypatch) -> None:
    service = ApiKeyService()
    monkeypatch.setattr(service, "_get_row", lambda provider: None)

    assert service.get_status("perplexity") == (False, None)
