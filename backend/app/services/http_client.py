from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx


class HttpFetchError(RuntimeError):
    """Raised when an upstream HTTP fetch fails after retries."""


_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_RETRIES = 2
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def fetch_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = _DEFAULT_RETRIES,
    allow_insecure_fallback_hosts: set[str] | None = None,
) -> object:
    response = _request_with_retries(
        url,
        params=params,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        allow_insecure_fallback_hosts=allow_insecure_fallback_hosts,
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
) -> str:
    response = _request_with_retries(
        url,
        params=params,
        headers=headers,
        timeout_seconds=timeout_seconds,
        retries=retries,
        allow_insecure_fallback_hosts=allow_insecure_fallback_hosts,
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
) -> httpx.Response:
    timeout = httpx.Timeout(timeout_seconds)
    attempts = max(retries + 1, 1)
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            if response.status_code in _RETRYABLE_STATUS_CODES:
                raise HttpFetchError(
                    f"Retryable HTTP {response.status_code} from {url}"
                )
            response.raise_for_status()
            return response
        except (httpx.HTTPError, HttpFetchError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.25 * (attempt + 1))
                continue

    host = urlparse(url).hostname or ""
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
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise HttpFetchError(f"HTTP request failed for {url}") from exc

    if last_error is not None:
        raise HttpFetchError(f"HTTP request failed for {url}") from last_error
    raise HttpFetchError(f"HTTP request failed for {url}")
