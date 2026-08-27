from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.http_client import (
    HttpFetchError,
    classify_http_failure,
    clear_last_http_fetch_error,
    fetch_json,
    get_last_http_fetch_error,
    parse_retry_after_seconds,
)


@pytest.fixture(autouse=True)
def _clear_http_thread_state() -> None:
    clear_last_http_fetch_error()
    yield
    clear_last_http_fetch_error()


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
    response.headers = {"content-type": "application/json", "retry-after": "7"}

    monkeypatch.setattr(
        "app.services.http_client.httpx.request",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", retries=0)

    assert exc_info.value.status_code == 429
    assert exc_info.value.classification == "rate_limited"
    assert exc_info.value.retry_after_seconds == pytest.approx(7.0)


def test_parse_retry_after_seconds() -> None:
    from datetime import UTC, datetime, timedelta

    from app.services.http_client import parse_retry_after_seconds

    assert parse_retry_after_seconds("12") == pytest.approx(12.0)
    assert parse_retry_after_seconds(" 3.5 ") == pytest.approx(3.5)
    assert parse_retry_after_seconds(None) is None
    assert parse_retry_after_seconds("not-a-date") is None
    future = datetime.now(UTC) + timedelta(seconds=45)
    http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after_seconds(http_date) == pytest.approx(45.0, abs=2.0)


def test_fetch_json_honors_retry_after_on_retry_sleep(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"content-type": "application/json", "retry-after": "5"}
    sleeps: list[float] = []
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return response
        raise httpx.ConnectTimeout("give up")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr(
        "app.services.http_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    with pytest.raises(HttpFetchError):
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", retries=1)

    assert sleeps == [pytest.approx(5.0)]


def test_fetch_json_caps_large_retry_after_sleep(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {"content-type": "application/json", "retry-after": "3600"}
    sleeps: list[float] = []
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return response
        raise httpx.ConnectTimeout("give up")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr(
        "app.services.http_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", retries=1)

    assert sleeps == [pytest.approx(30.0)]
    assert exc_info.value.classification == "network_failure"
    last = get_last_http_fetch_error()
    assert last is not None
    assert last.classification == "rate_limited"
    assert last.retry_after_seconds == pytest.approx(3600.0)


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


def test_fetch_json_does_not_retry_http_401(monkeypatch) -> None:
    """Auth denials are deterministic; do not burn the retry budget (#182)."""
    denied = MagicMock()
    denied.status_code = 401
    denied.headers = {"content-type": "application/json"}
    denied.raise_for_status.side_effect = httpx.HTTPStatusError(
        "denied",
        request=MagicMock(),
        response=denied,
    )
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return denied

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _seconds: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://query1.finance.yahoo.com/v7/finance/quote", retries=2)

    assert calls["count"] == 1
    assert exc_info.value.classification == "auth_denied"
    assert exc_info.value.status_code == 401


def test_fetch_json_limits_remote_protocol_error_retries(monkeypatch) -> None:
    """Abrupt disconnects get at most one retry before fallback (#183)."""
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _seconds: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json(
            "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
            retries=2,
            allow_insecure_fallback_hosts={"mis.twse.com.tw"},
        )

    # 2 protocol attempts, no insecure TLS fallback for disconnects.
    assert calls["count"] == 2
    assert exc_info.value.classification == "network_failure"
    assert exc_info.value.status_code is None


def test_fetch_json_does_not_reuse_prior_rate_limit_after_timeout(monkeypatch) -> None:
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"content-type": "application/json"}
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return rate_limited
        raise httpx.ConnectTimeout("timed out on retry")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _seconds: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", retries=1)

    assert calls["count"] == 2
    assert exc_info.value.classification == "network_failure"
    assert exc_info.value.status_code is None


def test_remember_keeps_rate_limit_across_fallback_requests() -> None:
    from app.services import http_client as http_client_module

    http_client_module.clear_last_http_fetch_error()
    rate = HttpFetchError(
        "rate limited",
        status_code=429,
        classification="rate_limited",
        retry_after_seconds=12.0,
    )
    emptyish = HttpFetchError(
        "bad payload",
        status_code=200,
        classification="unexpected_content",
    )
    http_client_module._remember_http_fetch_error(rate)
    http_client_module._remember_http_fetch_error(emptyish)
    assert http_client_module.get_last_http_fetch_error() is rate
