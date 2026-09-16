from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.http_client import (
    HttpFetchError,
    _RETRY_JITTER_FRACTION,
    _request_with_retries,
    capped_retry_sleep_seconds,
    classify_http_failure,
    clear_last_http_fetch_error,
    fetch_json,
    get_last_http_fetch_error,
    parse_retry_after_seconds,
    set_provider_request_spacing,
)


@pytest.fixture(autouse=True)
def _clear_http_thread_state() -> None:
    clear_last_http_fetch_error()
    set_provider_request_spacing(None)
    yield
    clear_last_http_fetch_error()
    set_provider_request_spacing(None)


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


def test_capped_retry_sleep_seconds_uses_exponential_backoff_with_zero_jitter() -> None:
    zero = lambda: 0.0
    assert capped_retry_sleep_seconds(None, attempt=0, jitter_random=zero) == pytest.approx(
        0.25
    )
    assert capped_retry_sleep_seconds(None, attempt=1, jitter_random=zero) == pytest.approx(
        0.50
    )
    assert capped_retry_sleep_seconds(None, attempt=2, jitter_random=zero) == pytest.approx(
        1.00
    )
    assert capped_retry_sleep_seconds(None, attempt=3, jitter_random=zero) == pytest.approx(
        2.00
    )


def test_capped_retry_sleep_seconds_jitter_stays_within_bound() -> None:
    base = 0.25 * (2**1)
    sleep = capped_retry_sleep_seconds(None, attempt=1, jitter_random=lambda: 0.999)
    assert sleep == pytest.approx(base + base * _RETRY_JITTER_FRACTION * 0.999)
    assert base <= sleep < base * (1 + _RETRY_JITTER_FRACTION)


def test_capped_retry_sleep_seconds_retry_after_overrides_shorter_backoff() -> None:
    sleep = capped_retry_sleep_seconds(5.0, attempt=0, jitter_random=lambda: 0.5)
    assert sleep == pytest.approx(5.0)


def test_capped_retry_sleep_seconds_caps_huge_retry_after() -> None:
    sleep = capped_retry_sleep_seconds(3600.0, attempt=0, jitter_random=lambda: 0.0)
    assert sleep == pytest.approx(30.0)


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
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

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
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U", retries=1)

    assert sleeps == [pytest.approx(30.0)]
    assert exc_info.value.classification == "network_failure"
    last = get_last_http_fetch_error()
    assert last is not None
    assert last.classification == "rate_limited"
    assert last.retry_after_seconds == pytest.approx(3600.0)


def test_fetch_json_exponential_backoff_sleeps(monkeypatch) -> None:
    sleeps: list[float] = []
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr(
        "app.services.http_client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    with pytest.raises(HttpFetchError):
        fetch_json("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", retries=2)

    assert calls["count"] == 3
    assert sleeps == [pytest.approx(0.25), pytest.approx(0.50)]


def test_fetch_json_retries_transient_status_codes(monkeypatch) -> None:
    for status in (408, 425, 429, 500, 502, 503, 504):
        calls = {"count": 0}
        ok = MagicMock()
        ok.status_code = 200
        ok.headers = {"content-type": "application/json"}
        ok.raise_for_status.return_value = None
        ok.json.return_value = {"ok": True}
        failing = MagicMock()
        failing.status_code = status
        failing.headers = {"content-type": "application/json"}

        def fake_request(*_args, _failing=failing, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return _failing
            return ok

        monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
        monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
        monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

        assert fetch_json("https://example.com/api", retries=1) == {"ok": True}
        assert calls["count"] == 2


def test_fetch_json_retries_timeout_and_network_errors(monkeypatch) -> None:
    calls = {"count": 0}
    ok = MagicMock()
    ok.status_code = 200
    ok.headers = {"content-type": "application/json"}
    ok.raise_for_status.return_value = None
    ok.json.return_value = {"ok": True}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectTimeout("timed out")
        if calls["count"] == 2:
            raise httpx.ConnectError("connection reset")
        return ok

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    assert fetch_json("https://example.com/api", retries=2) == {"ok": True}
    assert calls["count"] == 3


def test_fetch_json_does_not_retry_http_400(monkeypatch) -> None:
    bad = MagicMock()
    bad.status_code = 400
    bad.headers = {"content-type": "application/json"}
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad request",
        request=MagicMock(),
        response=bad,
    )
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return bad

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://example.com/api", retries=2)

    assert calls["count"] == 1
    assert exc_info.value.status_code == 400


def test_fetch_json_does_not_retry_http_404(monkeypatch) -> None:
    missing = MagicMock()
    missing.status_code = 404
    missing.headers = {"content-type": "application/json"}
    missing.raise_for_status.side_effect = httpx.HTTPStatusError(
        "missing",
        request=MagicMock(),
        response=missing,
    )
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return missing

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://example.com/api", retries=2)

    assert calls["count"] == 1
    assert exc_info.value.status_code == 404


def test_fetch_json_does_not_retry_http_403(monkeypatch) -> None:
    denied = MagicMock()
    denied.status_code = 403
    denied.headers = {"content-type": "application/json"}
    denied.raise_for_status.side_effect = httpx.HTTPStatusError(
        "forbidden",
        request=MagicMock(),
        response=denied,
    )
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return denied

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://example.com/api", retries=2)

    assert calls["count"] == 1
    assert exc_info.value.classification == "auth_denied"
    assert exc_info.value.status_code == 403


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


def test_fetch_json_bad_json_does_not_retry_http(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("broken json")
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return response

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://example.com/api", retries=2)

    assert calls["count"] == 1
    assert exc_info.value.classification == "unexpected_content"


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
    logged: list[dict] = []

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return denied

    def capture_warning(_msg, *args, **kwargs):
        extra = kwargs.get("extra") or {}
        if extra.get("event") == "external_http.request.failed":
            logged.append(extra)

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("app.services.http_client.logger.warning", capture_warning)

    with pytest.raises(HttpFetchError) as exc_info:
        fetch_json("https://query1.finance.yahoo.com/v7/finance/quote", retries=2)

    assert calls["count"] == 1
    assert exc_info.value.classification == "auth_denied"
    assert exc_info.value.status_code == 401
    assert logged
    assert logged[0]["attempts"] == 1


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


def test_provider_spacing_hook_called_per_upstream_attempt(monkeypatch) -> None:
    spaced: list[str] = []
    set_provider_request_spacing(lambda provider: spaced.append(provider))
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)
    monkeypatch.setattr("app.services.http_client.random.random", lambda: 0.0)

    with pytest.raises(HttpFetchError):
        fetch_json(
            "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
            retries=2,
            provider_name="twse",
        )

    assert calls["count"] == 3
    assert spaced == ["twse", "twse", "twse"]


def test_fetch_json_honors_custom_retryable_status_codes(monkeypatch) -> None:
    """Stricter AI retry set should not retry 500."""
    server_error = MagicMock()
    server_error.status_code = 500
    server_error.headers = {"content-type": "application/json"}
    server_error.raise_for_status.side_effect = httpx.HTTPStatusError(
        "server error",
        request=MagicMock(),
        response=server_error,
    )
    calls = {"count": 0}

    def fake_request(*_args, **_kwargs):
        calls["count"] += 1
        return server_error

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)
    monkeypatch.setattr("app.services.http_client.time.sleep", lambda _s: None)

    with pytest.raises(HttpFetchError):
        fetch_json(
            "https://api.openai.com/v1/responses",
            retries=2,
            retryable_status_codes=frozenset({429, 502, 503, 504}),
        )

    assert calls["count"] == 1


def test_fetch_json_passes_json_body_to_httpx(monkeypatch) -> None:
    captured: dict[str, object] = {}
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["data"] = kwargs.get("data")
        return response

    monkeypatch.setattr("app.services.http_client.httpx.request", fake_request)

    payload = {"model": "gpt-test"}
    assert fetch_json(
        "https://api.openai.com/v1/responses",
        method="POST",
        json_body=payload,
        retries=0,
    ) == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["json"] == payload
    assert captured["data"] is None


def test_request_with_retries_rejects_data_and_json_body() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        _request_with_retries(
            "https://example.com",
            method="POST",
            params=None,
            data={"a": "1"},
            json_body={"b": 2},
            headers=None,
            timeout_seconds=1.0,
            retries=0,
            allow_insecure_fallback_hosts=None,
            provider_name=None,
            operation_name=None,
            resource_id=None,
        )


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
