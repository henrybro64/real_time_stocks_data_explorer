"""Best-effort Redis L2 cache for Taiwan Signals tab payloads.

Process memory remains L1. When Redis is configured and healthy, successful
payloads are shared across replicas so TDCC/Yahoo cold paths are not
repeated per pod. Redis failures never break the read path.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_OWNERSHIP_KEY_PREFIX = "tw_ownership_concentration:v1:"
_PROFITABILITY_KEY_PREFIX = "tw_profitability:v1:"


def _redis_get(key: str) -> str | None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    return run_redis(
        f"tw signals get {key}",
        lambda client: client.get(key),
        kind="read",
    )


def _redis_setex(key: str, ttl_seconds: int, value: str) -> None:
    from app.services.quote_redis_cache import run_redis  # pylint: disable=import-outside-toplevel

    run_redis(
        f"tw signals setex {key}",
        lambda client: client.setex(key, ttl_seconds, value),
        kind="write",
    )


def ownership_cache_key(stock_id: str, range_name: str) -> str:
    return f"{_OWNERSHIP_KEY_PREFIX}{stock_id}:{range_name}"


def profitability_cache_key(stock_id: str, quarters: int) -> str:
    return f"{_PROFITABILITY_KEY_PREFIX}{stock_id}:{quarters}"


def get_cached_json(key: str) -> dict[str, Any] | None:
    try:
        raw = _redis_get(key)
    except Exception:  # noqa: BLE001 - cache must never break reads
        logger.debug("tw signals redis get failed key=%s", key, exc_info=True)
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def set_cached_json(key: str, *, ttl_seconds: int, payload: dict[str, Any]) -> None:
    ttl = max(int(ttl_seconds), 1)
    try:
        _redis_setex(key, ttl, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    except Exception:  # noqa: BLE001 - cache must never break reads
        logger.debug("tw signals redis set failed key=%s", key, exc_info=True)


def parse_cached_at(payload: dict[str, Any]) -> datetime | None:
    raw = payload.get("cachedAt")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
