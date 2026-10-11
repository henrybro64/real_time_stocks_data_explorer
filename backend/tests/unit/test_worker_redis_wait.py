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
    monkeypatch.setattr(worker.settings, "redis_socket_timeout_seconds", 2.0)


def test_no_redis_configured_does_not_wait(monkeypatch) -> None:
    monkeypatch.setattr(worker.settings, "redis_url", "")
    clock = _Clock()
    assert worker.wait_for_redis(30, sleep=clock.sleep, monotonic=clock.monotonic) is True
    assert clock.sleeps == []


def test_waits_until_redis_answers(redis_configured, monkeypatch) -> None:
    answers = iter([False, False, True])
    monkeypatch.setattr(quote_redis_cache, "probe_redis", lambda **_kwargs: next(answers))
    clock = _Clock()

    assert worker.wait_for_redis(30, sleep=clock.sleep, monotonic=clock.monotonic) is True
    assert clock.sleeps == [2.0, 2.0]


def test_gives_up_at_the_bound(redis_configured, monkeypatch) -> None:
    monkeypatch.setattr(quote_redis_cache, "probe_redis", lambda **_kwargs: False)
    clock = _Clock()

    assert worker.wait_for_redis(5, sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert clock.sleeps == [2.0, 2.0, 1.0]
    assert clock.now <= 5.0


def test_zero_budget_makes_no_connection_attempt(redis_configured, monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        quote_redis_cache,
        "probe_redis",
        lambda **_kwargs: calls.append(True) or True,
    )
    clock = _Clock()

    assert worker.wait_for_redis(0, sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert calls == []


def test_a_blocking_connect_never_overruns_the_deadline(redis_configured, monkeypatch) -> None:
    # Each attempt blocks for the timeouts it was given, as with an unreachable host.
    monkeypatch.setattr(worker.settings, "redis_socket_connect_timeout_seconds", 30.0)
    monkeypatch.setattr(worker.settings, "redis_socket_timeout_seconds", 30.0)
    clock = _Clock()
    seen: list[tuple[float, float]] = []

    def _blocking_attempt(*, connect_timeout: float, socket_timeout: float) -> bool:
        seen.append((connect_timeout, socket_timeout))
        clock.now += connect_timeout + socket_timeout
        return False

    monkeypatch.setattr(quote_redis_cache, "probe_redis", _blocking_attempt)

    assert worker.wait_for_redis(10, sleep=clock.sleep, monotonic=clock.monotonic) is False
    assert clock.now <= 10.0
    assert seen
    assert all(connect + socket <= 10.0 for connect, socket in seen)


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
