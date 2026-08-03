from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.http_client import HttpFetchError, classify_http_failure, fetch_json


def test_classify_http_failure_maps_auth_rate_limit_and_json() -> None:
    assert classify_http_failure(401) == "auth_denied"
    assert classify_http_failure(403) == "auth_denied"
    assert classify_http_failure(429) == "rate_limited"
    assert classify_http_failure(500) == "http_error"
    assert classify_http_failure(200, json_parse_failed=True) == "unexpected_content"
    assert (
        classify_http_failure(200, content_type="text/html; charset=utf-8")
        == "unexpected_content"
    )


def test_fetch_json_raises_auth_denied_for_http_401(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 401
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "denied",
        request=MagicMock(),
        response=response,
    )

    monkeypatch.setattr(
        "app.services.http_client.httpx.request",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", retries=0)

    assert exc_info.value.status_code == 401
    assert exc_info.value.classification == "auth_denied"


def test_fetch_json_raises_rate_limited_for_http_429(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"content-type": "application/json"}

    monkeypatch.setattr(
        "app.services.http_client.httpx.request",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", retries=0)

    assert exc_info.value.status_code == 429
    assert exc_info.value.classification == "rate_limited"


def test_fetch_json_raises_unexpected_content_for_html_body(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "text/html"}
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")

    monkeypatch.setattr(
        "app.services.http_client.httpx.request",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.tpex.org.tw/www/zh-tw/margin/sbl", retries=0)

    assert exc_info.value.classification == "unexpected_content"
    assert exc_info.value.content_type == "text/html"


def test_fetch_json_raises_network_failure_for_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.http_client.httpx.request",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")),
    )

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", retries=0)

    assert exc_info.value.classification == "network_failure"
    assert exc_info.value.status_code is None
