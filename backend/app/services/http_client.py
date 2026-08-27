from __future__ import annotations

import logging
import threading
import time
from time import perf_counter
from typing import Callable, Literal
from urllib.parse import urlparse

import httpx
from app.core.diagnostics import elapsed_ms


HttpFailureClassification = Literal[
    "auth_denied",
    "rate_limited",
    "network_failure",
    "unexpected_content",
    "http_error",
]


class HttpFetchError(RuntimeError):
    """Raised when an upstream HTTP fetch fails after retries."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        content_type: str | None = None,
        classification: HttpFailureClassification = "network_failure",
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.content_type = content_type
        self.classification = classification
        self.retry_after_seconds = retry_after_seconds


_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_RETRIES = 2
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
# Deterministic client auth denials must not burn the retry budget (#182).
_NON_RETRYABLE_STATUS_CODES = {401, 403}
# Cap Retry-After sleeps so a hostile/large header cannot stall worker threads.
_MAX_RETRY_AFTER_SLEEP_SECONDS = 30.0
# Abrupt upstream disconnects: at most one retry (2 attempts total) (#183).
_MAX_REMOTE_PROTOCOL_ATTEMPTS = 2
_PROVIDER_NAMES_BY_HOST = {
    "api.openai.com": "openai",
    "api.perplexity.ai": "perplexity",
    "isin.twse.com.tw": "twse",
    "mis.twse.com.tw": "twse",
    "news.google.com": "google_news",
    "openapi.twse.com.tw": "twse_openapi",
    "query1.finance.yahoo.com": "yahoo_finance",
    "stooq.com": "stooq",
    "www.sec.gov": "sec",
    "www.tpex.org.tw": "tpex",
    "www.twse.com.tw": "twse",
}

logger = logging.getLogger(__name__)
HttpMethod = Literal["GET", "POST"]
_thread_state = threading.local()


def clear_last_http_fetch_error() -> None:
    """Clear per-thread last upstream failure (for refresh diagnostics)."""
    _thread_state.last_error = None


def get_last_http_fetch_error() -> HttpFetchError | None:
    """Return the last HttpFetchError observed on this thread, if any."""
    return getattr(_thread_state, "last_error", None)


def _remember_http_fetch_error(exc: HttpFetchError) -> None:
    """Track actionable failures for refresh diagnostics.

    Rate-limit / auth failures stay sticky in thread-local storage across
    fallback requests so ``get_last_http_fetch_error`` can still see Retry-After
    after a later empty/unusable response.
    """
    previous = getattr(_thread_state, "last_error", None)
    if (
        previous is not None
        and previous.classification in {"rate_limited", "auth_denied"}
        and exc.classification not in {"rate_limited", "auth_denied"}
    ):
        return
    _thread_state.last_error = exc


def set_provider_request_spacing(hook: Callable[[str], None] | None) -> None:
    """Install a per-thread hook invoked before each upstream HTTP attempt."""
    _thread_state.provider_spacing_hook = hook


def get_provider_request_spacing() -> Callable[[str], None] | None:
    return getattr(_thread_state, "provider_spacing_hook", None)


def classify_http_failure(
    status_code: int | None,
    *,
    content_type: str | None = None,
    json_parse_failed: bool = False,
) -> HttpFailureClassification:
    if json_parse_failed:
        return "unexpected_content"
    if status_code in {401, 403}:
        return "auth_denied"
    if status_code == 429:
        return "rate_limited"
    if content_type and "json" not in content_type.lower() and "javascript" not in content_type.lower():
        if status_code is not None and status_code >= 400:
            return "http_error"
        return "unexpected_content"
    if status_code is not None and status_code >= 400:
        return "http_error"
    return "network_failure"


def parse_retry_after_seconds(value: str | None) -> float | None:
    """Parse an HTTP Retry-After header (delay-seconds or HTTP-date) into seconds."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    if seconds is not None:
        if seconds < 0:
            return None
        return seconds
    try:
        from datetime import UTC, datetime
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        delta = (when - datetime.now(UTC)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def capped_retry_sleep_seconds(retry_after: float | None, *, attempt: int) -> float:
    """Backoff sleep for retries, honoring Retry-After with a hard ceiling."""
    base = 0.25 * (attempt + 1)
    if retry_after is None:
        return base
    return min(max(base, float(retry_after)), _MAX_RETRY_AFTER_SLEEP_SECONDS)


def _failure_status_code(exc: Exception, *, last_status_code: int | None) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    if isinstance(exc, HttpFetchError):
        return exc.status_code
    return last_status_code


def _should_retry_http_failure(
    exc: Exception,
    *,
    status_code: int | None,
    attempt: int,
    attempts: int,
    remote_protocol_failures: int,
) -> bool:
    """Decide whether another attempt is useful before falling back/raising."""
    if attempt >= attempts - 1:
        return False
    if status_code in _NON_RETRYABLE_STATUS_CODES:
        return False
    if isinstance(exc, HttpFetchError) and exc.classification == "auth_denied":
        return False
    if isinstance(exc, httpx.RemoteProtocolError):
        # Server disconnected without a response: one quick retry, then fallback.
        return remote_protocol_failures < _MAX_REMOTE_PROTOCOL_ATTEMPTS
    return True


def _should_attempt_insecure_tls_fallback(exc: Exception | None) -> bool:
    """Insecure TLS only helps certificate/TLS problems, not auth or disconnects."""
    if exc is None:
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return False
    if isinstance(exc, HttpFetchError) and exc.classification in {
        "auth_denied",
        "rate_limited",
    }:
        return False
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
        401,
        403,
        429,
    }:
        return False
    return True


def fetch_json(
    url: str,
    *,
    method: HttpMethod = "GET",
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = _DEFAULT_RETRIES,
    allow_insecure_fallback_hosts: set[str] | None = None,
    provider_name: str | None = None,
    operation_name: str | None = None,
    resource_id: str | None = None,
) -> object:
    response = _request_with_retries(
        url,
        method=method,
        params=params,
        data=data,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        allow_insecure_fallback_hosts=allow_insecure_fallback_hosts,
        provider_name=provider_name,
        operation_name=operation_name,
        resource_id=resource_id,
    )
    content_type = response.headers.get("content-type")
    try:
        return response.json()
    except ValueError as exc:
        raise HttpFetchError(
            f"Failed to parse JSON from {url}",
            status_code=response.status_code,
            content_type=content_type,
            classification=classify_http_failure(
                response.status_code,
                content_type=content_type,
                json_parse_failed=True,
            ),
        ) from exc


def fetch_text(
    url: str,
    *,
    method: HttpMethod = "GET",
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = _DEFAULT_RETRIES,
    encoding: str | None = None,
    allow_insecure_fallback_hosts: set[str] | None = None,
    provider_name: str | None = None,
    operation_name: str | None = None,
    resource_id: str | None = None,
) -> str:
    response = _request_with_retries(
        url,
        method=method,
        params=params,
        data=data,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        allow_insecure_fallback_hosts=allow_insecure_fallback_hosts,
        provider_name=provider_name,
        operation_name=operation_name,
        resource_id=resource_id,
    )
    if encoding:
        response.encoding = encoding
    return response.text


def _request_with_retries(
    url: str,
    *,
    method: HttpMethod,
    params: dict[str, str] | None,
    data: dict[str, str] | None,
    headers: dict[str, str] | None,
    timeout_seconds: float,
    retries: int,
    allow_insecure_fallback_hosts: set[str] | None,
    provider_name: str | None,
    operation_name: str | None,
    resource_id: str | None,
) -> httpx.Response:
    started_at = perf_counter()
    timeout = httpx.Timeout(timeout_seconds)
    attempts = max(retries + 1, 1)
    last_error: Exception | None = None
    last_status_code: int | None = None
    last_content_type: str | None = None
    remote_protocol_failures = 0
    parsed = urlparse(url)
    host = parsed.hostname or ""
    effective_provider = provider_name or _PROVIDER_NAMES_BY_HOST.get(host, host or "unknown")
    upstream_path = parsed.path or "/"
    operation = operation_name or f"http_{method.lower()}"
    # Do not clear thread-local last_error here: a provider operation may issue
    # several fallback requests and needs the first rate-limit/auth failure kept.

    for attempt in range(attempts):
        # Clear prior-attempt response metadata so a later network timeout cannot
        # inherit an earlier 401/429 and misclassify the final failure.
        last_status_code = None
        last_content_type = None
        spacing_hook = get_provider_request_spacing()
        if spacing_hook is not None:
            try:
                spacing_hook(effective_provider)
            except Exception as spacing_exc:  # noqa: BLE001 - never block HTTP
                logger.debug("Provider spacing hook failed: %s", spacing_exc)
        try:
            response = httpx.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            last_status_code = response.status_code
            last_content_type = response.headers.get("content-type")
            if response.status_code in _RETRYABLE_STATUS_CODES:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("retry-after")
                )
                raise HttpFetchError(
                    f"Retryable HTTP {response.status_code} from {url}",
                    status_code=response.status_code,
                    content_type=last_content_type,
                    classification=classify_http_failure(
                        response.status_code,
                        content_type=last_content_type,
                    ),
                    retry_after_seconds=retry_after,
                )
            response.raise_for_status()
            logger.debug(
                "External HTTP request completed",
                extra={
                    "event": "external_http.request.completed",
                    "provider": effective_provider,
                    "operation": operation,
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": response.status_code,
                    "content_type": last_content_type,
                    "attempts": attempt + 1,
                    "duration_ms": elapsed_ms(started_at),
                },
            )
            return response
        except (httpx.HTTPError, HttpFetchError) as exc:
            last_error = exc
            if isinstance(exc, httpx.RemoteProtocolError):
                remote_protocol_failures += 1
            if isinstance(exc, httpx.HTTPStatusError):
                last_status_code = exc.response.status_code
                last_content_type = exc.response.headers.get("content-type")
            elif isinstance(exc, HttpFetchError):
                last_status_code = exc.status_code
                last_content_type = exc.content_type
                _remember_http_fetch_error(exc)
            status_for_retry = _failure_status_code(
                exc,
                last_status_code=last_status_code,
            )
            if not _should_retry_http_failure(
                exc,
                status_code=status_for_retry,
                attempt=attempt,
                attempts=attempts,
                remote_protocol_failures=remote_protocol_failures,
            ):
                break
            retry_after = None
            if isinstance(exc, HttpFetchError):
                retry_after = exc.retry_after_seconds
            sleep_seconds = capped_retry_sleep_seconds(retry_after, attempt=attempt)
            logger.warning(
                "External HTTP request attempt failed",
                extra={
                    "event": "external_http.request.retry",
                    "provider": effective_provider,
                    "operation": operation,
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": last_status_code,
                    "content_type": last_content_type,
                    "attempt": attempt + 1,
                    "max_attempts": attempts,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "retry_after_seconds": retry_after,
                    "sleep_seconds": sleep_seconds,
                    "response_classification": (
                        exc.classification
                        if isinstance(exc, HttpFetchError)
                        else classify_http_failure(
                            status_for_retry,
                            content_type=last_content_type,
                        )
                    ),
                },
            )
            time.sleep(sleep_seconds)
            continue

    if (
        allow_insecure_fallback_hosts
        and host in allow_insecure_fallback_hosts
        and _should_attempt_insecure_tls_fallback(last_error)
    ):
        try:
            response = httpx.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
                verify=False,
            )
            logger.warning(
                "External HTTP request used insecure TLS fallback",
                extra={
                    "event": "external_http.request.insecure_fallback",
                    "provider": effective_provider,
                    "operation": operation,
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "attempts": attempts,
                    "duration_ms": elapsed_ms(started_at),
                    "tls_verification_disabled": True,
                },
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            status_code = last_status_code
            content_type = last_content_type
            if isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                content_type = exc.response.headers.get("content-type")
            classification = classify_http_failure(status_code, content_type=content_type)
            logger.warning(
                "External HTTP request failed after insecure fallback",
                extra={
                    "event": "external_http.request.failed",
                    "provider": effective_provider,
                    "operation": operation,
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": status_code,
                    "content_type": content_type,
                    "response_classification": classification,
                    "attempts": attempts,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "tls_verification_disabled": True,
                },
            )
            err = HttpFetchError(
                f"HTTP request failed for {url}",
                status_code=status_code,
                content_type=content_type,
                classification=classification,
            )
            _remember_http_fetch_error(err)
            raise err from exc

    if last_error is not None:
        if isinstance(last_error, HttpFetchError):
            # Prefer the final exception's own metadata — never fall back to an
            # earlier attempt's status after a no-response network failure.
            classification = last_error.classification
            status_code = last_error.status_code
            content_type = last_error.content_type
        elif isinstance(last_error, httpx.HTTPStatusError):
            status_code = last_error.response.status_code
            content_type = last_error.response.headers.get("content-type")
            classification = classify_http_failure(status_code, content_type=content_type)
        else:
            status_code = last_status_code
            content_type = last_content_type
            classification = classify_http_failure(status_code, content_type=content_type)
        logger.warning(
            "External HTTP request failed",
            extra={
                "event": "external_http.request.failed",
                "provider": effective_provider,
                "operation": operation,
                "resource_id": resource_id,
                "upstream_host": host,
                "upstream_path": upstream_path,
                "status_code": status_code,
                "content_type": content_type,
                "response_classification": classification,
                "attempts": attempts,
                "duration_ms": elapsed_ms(started_at),
                "error_type": type(last_error).__name__,
                "error": str(last_error),
            },
        )
        if isinstance(last_error, HttpFetchError):
            _remember_http_fetch_error(last_error)
            raise last_error
        err = HttpFetchError(
            f"HTTP request failed for {url}",
            status_code=status_code,
            content_type=content_type,
            classification=classification,
        )
        _remember_http_fetch_error(err)
        raise err from last_error
    logger.warning(
        "External HTTP request failed",
        extra={
            "event": "external_http.request.failed",
            "provider": effective_provider,
            "operation": operation,
            "resource_id": resource_id,
            "upstream_host": host,
            "upstream_path": upstream_path,
            "attempts": attempts,
            "duration_ms": elapsed_ms(started_at),
            "response_classification": "network_failure",
        },
    )
    err = HttpFetchError(
        f"HTTP request failed for {url}",
        classification="network_failure",
    )
    _remember_http_fetch_error(err)
    raise err
