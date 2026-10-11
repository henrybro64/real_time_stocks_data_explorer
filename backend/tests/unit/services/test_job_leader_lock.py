"""Redis job leader locks (#276)."""

from __future__ import annotations

from app.services.job_leader_lock import (
    clear_daily_job_markers_for_tests,
    daily_job_already_ran,
    release_job_leadership,
    remember_daily_job,
    try_acquire_job_leadership,
)


def test_no_redis_returns_local_token(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "redis_url", "")
    token = try_acquire_job_leadership("unit-test-job")
    assert token is not None
    assert token.startswith("local-")
    release_job_leadership("unit-test-job", token)


def test_release_local_token_is_noop() -> None:
    release_job_leadership("unit-test-job", "local-abc")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def eval(self, script, numkeys, key, token):
        del script, numkeys
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0


def test_second_worker_does_not_acquire_until_the_lock_is_released(monkeypatch) -> None:
    from app.core import config

    fake = _FakeRedis()
    monkeypatch.setattr(config.settings, "redis_url", "redis://unit")
    monkeypatch.setattr(
        "app.services.quote_redis_cache.run_redis",
        lambda _label, fn, kind="read": fn(fake),
    )
    first = try_acquire_job_leadership("alerts", ttl_seconds=60)
    second = try_acquire_job_leadership("alerts", ttl_seconds=60)
    assert first is not None
    assert second is None
    release_job_leadership("alerts", first)
    third = try_acquire_job_leadership("alerts", ttl_seconds=60)
    assert third is not None


def test_daily_marker_is_shared_across_callers(monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "redis_url", "")
    clear_daily_job_markers_for_tests()
    assert daily_job_already_ran("taiwan-market-data-refresh", "2026-07-10") is False
    remember_daily_job("taiwan-market-data-refresh", "2026-07-10")
    assert daily_job_already_ran("taiwan-market-data-refresh", "2026-07-10") is True
    assert daily_job_already_ran("taiwan-market-data-refresh", "2026-07-11") is False
    clear_daily_job_markers_for_tests()
