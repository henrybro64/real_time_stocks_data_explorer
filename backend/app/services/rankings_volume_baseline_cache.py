"""Optional Redis-backed volume baselines for rankings volume-change types."""

from __future__ import annotations

import logging
from typing import Protocol

from app.services.quote_redis_cache import run_redis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "rankings:volume_baseline:v1:"


class VolumeBaselineStore(Protocol):
    def get(self, stock_id: str) -> int | None: ...

    def set(self, stock_id: str, volume: int) -> None: ...


class InMemoryVolumeBaselineStore:
    def __init__(self) -> None:
        self._values: dict[str, int] = {}

    def get(self, stock_id: str) -> int | None:
        return self._values.get(stock_id)

    def set(self, stock_id: str, volume: int) -> None:
        self._values[stock_id] = volume


class CompositeVolumeBaselineStore:
    def __init__(
        self,
        memory_store: InMemoryVolumeBaselineStore | None = None,
    ) -> None:
        self._memory = memory_store or InMemoryVolumeBaselineStore()

    def get(self, stock_id: str) -> int | None:
        cached = _get_redis_baseline(stock_id)
        if cached is not None:
            return cached
        return self._memory.get(stock_id)

    def set(self, stock_id: str, volume: int) -> None:
        self._memory.set(stock_id, volume)
        _set_redis_baseline(stock_id, volume)


def _get_redis_baseline(stock_id: str) -> int | None:
    def _read(client):
        raw = client.get(_KEY_PREFIX + stock_id)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return run_redis(
        f"rankings baseline get {stock_id}",
        _read,
        kind="read",
    )


def _set_redis_baseline(stock_id: str, volume: int) -> None:
    run_redis(
        f"rankings baseline set {stock_id}",
        lambda client: client.set(_KEY_PREFIX + stock_id, str(volume)),
        kind="write",
    )


default_volume_baseline_store = CompositeVolumeBaselineStore()
