"""Lightweight per-request timing logs for TW research / Profile read paths (#346).

Does not alter response bodies. Emits one INFO line with structured ``extra`` fields
compatible with ``docs/backend-logging.md``.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.diagnostics import elapsed_ms

logger = logging.getLogger(__name__)

PERF_LOG_MARKER = "endpoint.timing.completed"


@contextmanager
def endpoint_timing(
    *,
    endpoint: str,
    snapshot_id: str | None = None,
    stock_id: str | None = None,
    record_count: int = 0,
    calendar_preload_attempted: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield a mutable bag; set ``result`` / ``rows_returned`` before exit to log sizes."""
    started = time.perf_counter()
    state: dict[str, Any] = {
        "rows_returned": 0,
        "result": None,
        "record_count": int(record_count),
        "calendar_preload_attempted": bool(calendar_preload_attempted),
    }
    try:
        yield state
    finally:
        result = state.get("result")
        response_bytes = _json_byte_len(result)
        logger.info(
            PERF_LOG_MARKER,
            extra={
                "event": PERF_LOG_MARKER,
                "endpoint": endpoint,
                "snapshot_id": snapshot_id,
                "stock_id": stock_id,
                "record_count": int(state.get("record_count") or 0),
                "rows_returned": int(state.get("rows_returned") or 0),
                "response_bytes": response_bytes,
                "duration_ms": elapsed_ms(started),
                "calendar_preload_attempted": bool(
                    state.get("calendar_preload_attempted")
                ),
            },
        )


def _json_byte_len(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0
