"""Coalesce in-flight manual ranking refreshes and enforce a short cooldown."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from threading import Event, Lock
import time


_COOLDOWN_DETAIL = (
    "Rankings refresh is cooling down. Retry after a few seconds."
)


class RankingsRefreshCooldownError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(_COOLDOWN_DETAIL)
        self.detail = _COOLDOWN_DETAIL
        self.retry_after_seconds = max(1, retry_after_seconds)


@dataclass
class InFlightRefresh:
    event: Event = field(default_factory=Event)
    result: object | None = None
    error: BaseException | None = None


class RankingsRefreshGate:
    def __init__(
        self,
        cooldown_seconds: Callable[[], int],
        *,
        wait_timeout_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._wait_timeout_seconds = wait_timeout_seconds
        self._monotonic = monotonic
        self._lock = Lock()
        self._in_flight: dict[Hashable, InFlightRefresh] = {}
        self._completed_at: dict[Hashable, float] = {}

    def acquire(self, key: Hashable) -> tuple[bool, InFlightRefresh]:
        cooldown = self._cooldown_seconds()
        with self._lock:
            existing = self._in_flight.get(key)
            if existing is not None:
                return False, existing
            last = self._completed_at.get(key)
            now = self._monotonic()
            if last is not None and now - last < cooldown:
                raise RankingsRefreshCooldownError(int(cooldown - (now - last)))
            holder = InFlightRefresh()
            self._in_flight[key] = holder
            return True, holder

    def join(self, holder: InFlightRefresh) -> object:
        if not holder.event.wait(timeout=self._wait_timeout_seconds):
            raise TimeoutError("Rankings refresh is already in progress")
        if holder.error is not None:
            raise holder.error
        return holder.result

    def release(
        self,
        key: Hashable,
        holder: InFlightRefresh,
        *,
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        holder.result = result
        holder.error = error
        with self._lock:
            self._completed_at[key] = self._monotonic()
            if self._in_flight.get(key) is holder:
                del self._in_flight[key]
        holder.event.set()
