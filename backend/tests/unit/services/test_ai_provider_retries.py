from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.ai_providers.base import AIProviderError
from app.services.ai_providers.openai_provider import OpenAIProvider
from app.services.ai_providers.perplexity_provider import PerplexityProvider


def _ok_openai_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    response.json.return_value = {"output_text": "Market looks mixed."}
    return response


def _ok_perplexity_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": "Cautious outlook."}}],
        "citations": ["https://example.com/a", "https://example.com/b"],
    }
    return response


def _status_response(status: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.headers = {"content-type": "application/json"}
    if status not in {429, 502, 503, 504}:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status}",
            request=MagicMock(),
            response=response,
        )
    return response


@pytest.mark.parametrize(
    ("provider_cls", "status"),
    [
        (OpenAIProvider, 401),
        (OpenAIProvider, 400),
        (PerplexityProvider, 401),
        (PerplexityProvider, 400),
    ],
)
def test_ai_providers_do_not_retry_auth_or_bad_request(
    monkeypatch,
    provider_cls,
    status: int,
) -> None:
    calls = {"count": 0}
    failing = _status_response(status)

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return failing

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    provider = provider_cls(api_key="sk-test", model_name="test-model")
    with pytest.raises(AIProviderError):
        provider.generate_stock_analysis({"symbol": "AAPL"})

    assert calls["count"] == 1


@pytest.mark.parametrize(
    ("provider_cls", "ok_factory", "status"),
    [
        (OpenAIProvider, _ok_openai_response, 429),
        (OpenAIProvider, _ok_openai_response, 502),
        (OpenAIProvider, _ok_openai_response, 503),
        (OpenAIProvider, _ok_openai_response, 504),
        (PerplexityProvider, _ok_perplexity_response, 429),
        (PerplexityProvider, _ok_perplexity_response, 502),
        (PerplexityProvider, _ok_perplexity_response, 503),
        (PerplexityProvider, _ok_perplexity_response, 504),
    ],
)
def test_ai_providers_retry_transient_statuses_once(
    monkeypatch,
    provider_cls,
    ok_factory,
    status: int,
) -> None:
    calls = {"count": 0}
    failing = _status_response(status)
    ok = ok_factory()

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return failing
        return ok

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    provider = provider_cls(api_key="sk-test", model_name="test-model")
    result = provider.generate_stock_analysis({"symbol": "AAPL"})

    assert calls["count"] == 2
    assert result.model == "test-model"
    assert result.content


def test_openai_empty_content_after_200_does_not_rehttp(monkeypatch) -> None:
    calls = {"count": 0}
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    response.json.return_value = {"output_text": "   "}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return response

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    provider = OpenAIProvider(api_key="sk-test", model_name="test-model")
    with pytest.raises(AIProviderError, match="empty analysis"):
        provider.generate_stock_analysis({"symbol": "AAPL"})

    assert calls["count"] == 1


def test_perplexity_preserves_citations_after_retry(monkeypatch) -> None:
    calls = {"count": 0}
    failing = _status_response(503)
    ok = _ok_perplexity_response()

    def fake_request(method, url, **kwargs):
        calls["count"] += 1
        assert method == "POST"
        assert kwargs.get("json") is not None
        if calls["count"] == 1:
            return failing
        return ok

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    provider = PerplexityProvider(api_key="pplx-test", model_name="sonar")
    result = provider.generate_stock_analysis({"symbol": "AAPL"})

    assert calls["count"] == 2
    assert [c.url for c in result.citations] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_openai_does_not_retry_http_500_with_strict_codes(monkeypatch) -> None:
    """AI providers use a stricter retry set that excludes 500."""
    calls = {"count": 0}
    failing = _status_response(500)

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return failing

    monkeypatch.setattr("app.services.http_client._send_http_request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    provider = OpenAIProvider(api_key="sk-test", model_name="test-model")
    with pytest.raises(AIProviderError):
        provider.generate_stock_analysis({"symbol": "AAPL"})

    assert calls["count"] == 1
