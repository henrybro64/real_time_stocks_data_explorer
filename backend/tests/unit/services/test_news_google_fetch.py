from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services import news_service
from app.services.http_client import HttpFetchError


def test_fetch_google_news_retries_transient_http_once(monkeypatch) -> None:
    calls = {"count": 0}
    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {"content-type": "application/rss+xml"}
    ok.raise_for_status.return_value = None
    ok.text = (
        '<?xml version="1.0"?><rss><channel>'
        "<item><title>Acme rises</title>"
        "<link>https://example.com/a</link>"
        "<source>Wire</source>"
        "<pubDate>Wed, 16 Sep 2026 01:00:00 GMT</pubDate>"
        "</item></channel></rss>"
    )
    failing = MagicMock()
    failing.status_code = 503
    failing.headers = {"content-type": "text/plain"}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return failing
        return ok

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    items = news_service._fetch_google_news(
        "ACME",
        {"hl": "en-US", "gl": "US", "ceid": "US:en"},
        max_items=5,
        stock_id="US:ACME",
        window_days=1,
        enforce_source_language=False,
    )

    assert calls["count"] == 2
    assert len(items) == 1
    assert items[0].title == "Acme rises"


def test_fetch_google_news_soft_fails_after_bounded_retries(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    items = news_service._fetch_google_news(
        "ACME",
        {"hl": "en-US", "gl": "US", "ceid": "US:en"},
        max_items=5,
        stock_id="US:ACME",
    )

    assert calls["count"] == 2  # retries=1 → 2 attempts
    assert items == []


def test_fetch_google_news_malformed_rss_does_not_retry_http(monkeypatch) -> None:
    calls = {"count": 0}
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/rss+xml"}
    response.raise_for_status.return_value = None
    response.text = "not-xml<<<"

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return response

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    items = news_service._fetch_google_news(
        "ACME",
        {"hl": "en-US", "gl": "US", "ceid": "US:en"},
        max_items=5,
        stock_id="US:ACME",
    )

    assert calls["count"] == 1
    assert items == []


def test_fetch_google_news_passes_fetch_text_kwargs(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_fetch_text(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        raise HttpFetchError("boom", classification="network_failure")

    monkeypatch.setattr(news_service, "fetch_text", fake_fetch_text)

    items = news_service._fetch_google_news(
        "ACME stock",
        {"hl": "en-US", "gl": "US", "ceid": "US:en"},
        max_items=3,
        stock_id="US:ACME",
        window_days=7,
    )

    assert items == []
    assert captured["url"] == news_service._GOOGLE_NEWS_RSS_URL
    assert captured["retries"] == 1
    assert captured["provider_name"] == "google_news"
    assert captured["operation_name"] == "google_news_rss"
    assert captured["resource_id"] == "US:ACME"
    assert captured["timeout_seconds"] == news_service._REQUEST_TIMEOUT_SECONDS
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["q"] == "ACME stock when:7d"
    assert params["hl"] == "en-US"
