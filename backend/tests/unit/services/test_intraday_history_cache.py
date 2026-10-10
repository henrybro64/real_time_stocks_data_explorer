"""Intraday history single-flight when Redis is the lock (#269)."""

from __future__ import annotations

from threading import Thread
import time

from app.services import intraday_history_cache as cache


def test_held_redis_lock_does_not_take_the_memory_lock(monkeypatch) -> None:
    cache._memory_locks.clear()
    monkeypatch.setattr(cache, "redis_configured", lambda: True)
    monkeypatch.setattr(cache, "_try_redis_lock", lambda _key, _token: False)

    assert cache.try_acquire_intraday_lock("TW:2330", "1D") is None
    assert cache._memory_locks == {}


def test_redis_lock_error_falls_back_to_memory(monkeypatch) -> None:
    cache._memory_locks.clear()
    monkeypatch.setattr(cache, "redis_configured", lambda: True)
    monkeypatch.setattr(cache, "_try_redis_lock", lambda _key, _token: None)

    token = cache.try_acquire_intraday_lock("TW:2330", "1D")
    assert token is not None
    assert cache._memory_locks
    cache.release_intraday_lock("TW:2330", "1D", token)


def test_wait_returns_when_the_lock_is_released(monkeypatch) -> None:
    cache._memory_locks.clear()
    monkeypatch.setattr(cache, "redis_configured", lambda: False)
    token = cache.try_acquire_intraday_lock("TW:2330", "1D")
    assert token is not None
    result: dict[str, object] = {}

    def waiter() -> None:
        result["candles"] = cache.wait_for_cached_intraday(
            "TW:2330",
            "1D",
            timeout_seconds=5,
        )

    thread = Thread(target=waiter)
    started = time.monotonic()
    thread.start()
    time.sleep(0.1)
    cache.release_intraday_lock("TW:2330", "1D", token)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["candles"] is None
    assert time.monotonic() - started < 2
