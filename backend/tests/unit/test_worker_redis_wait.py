"""Worker waits a bounded time for Redis before starting jobs (#293)."""

from __future__ import annotations

import pytest

import app.worker as worker
from app.services import quote_redis_cache


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def redis_configured(monkeypatch):
    monkeypatch.setattr(worker.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(worker.settings, "redis_socket_connect_timeout_seconds", 2.0)


def test_no_redis_configured_does_not_wait(monkeypatch) -> None:
    monkeypatch.setattr(worker.settings, "redis_url", "")
    clock = _Clock()
    assert worker.wait_for_redis(30, sleep=clock.sleep, monotonic=clock.monotonic) is True
    assert clock.sleeps == []


def test_waits_until_redis_answers(redis_configured, monkeypatch) -> None:
    answers = iter([False, False, True])
    monkeypatch.setattr(quote_redis_cache, "redis_enabled", lambda: next(answers))
    clock = _Clock()

    assert worker.wait_for_redis(30, sleep=clock.sleep, monotonic=clock.monotonic) is True
    assert clock.sleeps == [2.0, 2.0]


def test_gives_up_at_the_bound(redis_configured, monkeypatch) -> None:
    monkeypatch.setattr(quote_redis_cache, "redis_enabled", lambda: False)
    clock = _Clock()

    assert worker.wait_for_redis(5, sleep=clock.sleep, monotonic=clock.monotonic) is False
    # With 1s left, a 2s connect attempt could overrun, so it is not started.
    assert clock.sleeps == [2.0, 2.0]
    assert clock.now <= 5.0


def test_zero_budget_makes_no_connection_attempt(redis_configured, monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(quote_redis_cache, "redis_enabled", lambda: calls.append(True) or True)
    clock = _Clock()

    assert worker.wait_for_redis(0, sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert calls == []


def test_a_blocking_connect_never_overruns_the_deadline(redis_configured, monkeypatch) -> None:
    # Each attempt blocks for the full connect timeout, as with an unreachable host.
    monkeypatch.setattr(worker.settings, "redis_socket_connect_timeout_seconds", 3.0)
    clock = _Clock()

    def _blocking_attempt() -> bool:
        clock.now += 3.0
        return False

    monkeypatch.setattr(quote_redis_cache, "redis_enabled", _blocking_attempt)

    assert worker.wait_for_redis(10, sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert clock.now <= 10.0


def test_worker_waits_for_redis_before_starting_jobs(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.core.auth_settings.apply_auth_runtime_settings", lambda s: [])
    monkeypatch.setattr(worker.settings, "worker_redis_wait_seconds", 12.0)
    monkeypatch.setattr(worker, "wait_for_redis", lambda timeout: calls.append(f"wait {timeout}"))
    monkeypatch.setattr(worker, "start_enabled_background_jobs", lambda: calls.append("start"))
    monkeypatch.setattr(worker, "stop_background_jobs", lambda: calls.append("stop"))
    monkeypatch.setattr(worker.uvicorn, "run", lambda *args, **kwargs: calls.append("serve"))

    worker.main()

    assert calls == ["wait 12.0", "start", "serve", "stop"]
