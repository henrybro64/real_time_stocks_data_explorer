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
    assert clock.now == 5.0  # never sleeps past the bound
    assert clock.sleeps == [2.0, 2.0, 1.0]


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
