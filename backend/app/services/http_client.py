from __future__ import annotations

import logging
import time
from time import perf_counter
from urllib.parse import urlparse

import httpx
from app.core.diagnostics import elapsed_ms


class HttpFetchError(RuntimeError):
    """Raised when an upstream HTTP fetch fails after retries."""


_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_RETRIES = 2
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
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
    "www.twse.com.tw": "twse",
}

logger = logging.getLogger(__name__)


def fetch_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
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
        params=params,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        allow_insecure_fallback_hosts=allow_insecure_fallback_hosts,
        provider_name=provider_name,
        operation_name=operation_name,
        resource_id=resource_id,
    )
    try:
        return response.json()
    except ValueError as exc:
        raise HttpFetchError(f"Failed to parse JSON from {url}") from exc


def fetch_text(
    url: str,
    *,
    params: dict[str, str] | None = None,
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
        params=params,
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
    params: dict[str, str] | None,
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
    parsed = urlparse(url)
    host = parsed.hostname or ""
    effective_provider = provider_name or _PROVIDER_NAMES_BY_HOST.get(host, host or "unknown")
    upstream_path = parsed.path or "/"

    for attempt in range(attempts):
        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            last_status_code = response.status_code
            if response.status_code in _RETRYABLE_STATUS_CODES:
                raise HttpFetchError(
                    f"Retryable HTTP {response.status_code} from {url}"
                )
            response.raise_for_status()
            logger.debug(
                "External HTTP request completed",
                extra={
                    "event": "external_http.request.completed",
                    "provider": effective_provider,
                    "operation": operation_name or "http_get",
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": response.status_code,
                    "attempts": attempt + 1,
                    "duration_ms": elapsed_ms(started_at),
                },
            )
            return response
        except (httpx.HTTPError, HttpFetchError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                logger.warning(
                    "External HTTP request attempt failed",
                    extra={
                        "event": "external_http.request.retry",
                        "provider": effective_provider,
                        "operation": operation_name or "http_get",
                        "resource_id": resource_id,
                        "upstream_host": host,
                        "upstream_path": upstream_path,
                        "status_code": last_status_code,
                        "attempt": attempt + 1,
                        "max_attempts": attempts,
                        "duration_ms": elapsed_ms(started_at),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                time.sleep(0.25 * (attempt + 1))
                continue

    if (
        allow_insecure_fallback_hosts
        and host in allow_insecure_fallback_hosts
    ):
        try:
            response = httpx.get(
                url,
                params=params,
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
                    "operation": operation_name or "http_get",
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": response.status_code,
                    "attempts": attempts,
                    "duration_ms": elapsed_ms(started_at),
                    "tls_verification_disabled": True,
                },
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            logger.warning(
                "External HTTP request failed after insecure fallback",
                extra={
                    "event": "external_http.request.failed",
                    "provider": effective_provider,
                    "operation": operation_name or "http_get",
                    "resource_id": resource_id,
                    "upstream_host": host,
                    "upstream_path": upstream_path,
                    "status_code": last_status_code,
                    "attempts": attempts,
                    "duration_ms": elapsed_ms(started_at),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "tls_verification_disabled": True,
                },
            )
            raise HttpFetchError(f"HTTP request failed for {url}") from exc

    if last_error is not None:
        logger.warning(
            "External HTTP request failed",
            extra={
                "event": "external_http.request.failed",
                "provider": effective_provider,
                "operation": operation_name or "http_get",
                "resource_id": resource_id,
                "upstream_host": host,
                "upstream_path": upstream_path,
                "status_code": last_status_code,
                "attempts": attempts,
                "duration_ms": elapsed_ms(started_at),
                "error_type": type(last_error).__name__,
                "error": str(last_error),
            },
        )
        raise HttpFetchError(f"HTTP request failed for {url}") from last_error
    logger.warning(
        "External HTTP request failed",
        extra={
            "event": "external_http.request.failed",
            "provider": effective_provider,
            "operation": operation_name or "http_get",
            "resource_id": resource_id,
            "upstream_host": host,
            "upstream_path": upstream_path,
            "attempts": attempts,
            "duration_ms": elapsed_ms(started_at),
        },
    )
    raise HttpFetchError(f"HTTP request failed for {url}")
