"""Shared cache helper for Market Snapshot tiles (FX/commodities).

Uses the quote Redis client wrapper so Redis connection failures update
`/health` quote-cache state and reuse the #132 reconnect/backoff logic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import threading
import time
from typing import Any, TypeVar

from app.services.quote_redis_cache import run_redis

_KEY_PREFIX = "market:snapshot:v1:"

_memory_lock = threading.Lock()
_memory_payloads: dict[str, tuple[float, str]] = {}

_gates_lock = threading.Lock()

T = TypeVar("T")


class _RefreshGate:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.refreshing = False
        self.result: tuple[Any] | None = None
        self.error: BaseException | None = None


_refresh_gates: dict[str, _RefreshGate] = {}


def clear_market_snapshot_cache_for_tests() -> None:
    with _memory_lock:
        _memory_payloads.clear()
    with _gates_lock:
        _refresh_gates.clear()


def get_cached_snapshot(key: str) -> dict[str, Any] | None:
    redis_key = _redis_key(key)
    raw = run_redis(
        f"market_snapshot:get:{key}",
        lambda client: client.get(redis_key),
        kind="read",
    )
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
        _save_memory_copy(redis_key, text)
        return _decode_payload(text)
    if isinstance(raw, str):
        _save_memory_copy(redis_key, raw)
        return _decode_payload(raw)
    return _read_memory_copy(redis_key)


def set_cached_snapshot(key: str, payload: dict[str, Any], *, ttl_seconds: int) -> None:
    redis_key = _redis_key(key)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    _save_memory_copy(redis_key, text)
    run_redis(
        f"market_snapshot:set:{key}",
        lambda client: client.setex(redis_key, ttl_seconds, text),
        kind="write",
    )


def make_cache_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "cachedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "snapshot": snapshot,
    }


def payload_age_seconds(payload: dict[str, Any]) -> float | None:
    cached_at = payload.get("cachedAt")
    if not isinstance(cached_at, str):
        return None
    try:
        instant = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(UTC) - instant).total_seconds()


def run_snapshot_refresh_singleflight(key: str, refresh_fn: Callable[[], T]) -> T:
    """Run one refresh per concurrent wave; waiters reuse the leader result."""
    gate = _gate_for(key)
    with gate.cond:
        while gate.refreshing:
            gate.cond.wait(timeout=35.0)
            if not gate.refreshing:
                if gate.error is not None:
                    raise gate.error
                if gate.result is not None:
                    return gate.result[0]  # type: ignore[no-any-return]
                break
        gate.refreshing = True
        gate.result = None
        gate.error = None

    try:
        value = refresh_fn()
        with gate.cond:
            gate.result = (value,)
            return value
    except BaseException as exc:
        with gate.cond:
            gate.error = exc
        raise
    finally:
        with gate.cond:
            gate.refreshing = False
            gate.cond.notify_all()


def _gate_for(key: str) -> _RefreshGate:
    with _gates_lock:
        gate = _refresh_gates.get(key)
        if gate is None:
            gate = _RefreshGate()
            _refresh_gates[key] = gate
        return gate


def _redis_key(key: str) -> str:
    return f"{_KEY_PREFIX}{key}"


def _save_memory_copy(redis_key: str, text: str) -> None:
    with _memory_lock:
        _memory_payloads[redis_key] = (time.monotonic(), text)


def _read_memory_copy(redis_key: str) -> dict[str, Any] | None:
    with _memory_lock:
        entry = _memory_payloads.get(redis_key)
    if entry is None:
        return None
    _cached_at, text = entry
    return _decode_payload(text)


def _decode_payload(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed
