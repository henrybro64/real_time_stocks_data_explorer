"""Intraday history single-flight when Redis is the lock (#269)."""

from __future__ import annotations

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
