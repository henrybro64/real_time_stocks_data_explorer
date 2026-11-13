"""Shared log-text sanitizers (query strings, auth/token redaction, tracebacks).

Used by first-pass fault logging and by the JSON/console log formatters so a
``logging`` ``exc_info`` can never leak provider URLs with query tokens or
Authorization headers into stdout/stderr.
"""

from __future__ import annotations

import re

_URL_QUERY_RE = re.compile(r"(\S+)\?[^\s]*")
_AUTHORIZATION_RE = re.compile(r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?\S+")
_TOKEN_KV_RE = re.compile(r"(?i)\b(?:token|api[_-]?key)\s*[:=]\s*\S+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")

DEFAULT_TEXT_LIMIT = 200
DEFAULT_TRACEBACK_MAX_CHARS = 4000


def sanitize_log_text(text: object, *, limit: int | None = DEFAULT_TEXT_LIMIT) -> str:
    """Strip URL query strings and redact credentials; optionally truncate.

    ``limit=None`` disables truncation (used per traceback line).
    """
    cleaned = _URL_QUERY_RE.sub(r"\1", str(text or ""))
    cleaned = _AUTHORIZATION_RE.sub("<redacted>", cleaned)
    cleaned = _TOKEN_KV_RE.sub("<redacted>", cleaned)
    cleaned = _BEARER_RE.sub("<redacted>", cleaned)
    if limit is None:
        return cleaned
    return cleaned[: max(0, int(limit))]


def sanitize_traceback(
    text: object, *, max_chars: int = DEFAULT_TRACEBACK_MAX_CHARS
) -> str:
    """Sanitize a formatted traceback line-by-line and cap total length."""
    raw = str(text or "")
    sanitized = "\n".join(
        sanitize_log_text(line, limit=None) for line in raw.split("\n")
    )
    cap = max(0, int(max_chars))
    if len(sanitized) > cap:
        return sanitized[:cap]
    return sanitized
