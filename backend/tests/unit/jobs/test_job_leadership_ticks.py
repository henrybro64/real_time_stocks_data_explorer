"""Leadership-skipped ticks are not successes and retry soon (#292)."""

from __future__ import annotations

import threading

import pytest

from app.jobs.health import (
    JOB_TICK_INCOMPLETE,
    get_background_jobs_health,
    register_background_job,
    reset_job_health_for_tests,
)
from app.services import job_leader_lock
from app.services import quote_redis_cache as cache


class _HeldElsewhereRedis:
    """Every leader key is already held by another worker."""

    def __init__(self) -> None:
        self.acquire_attempts = 0

    def set(self, key, value, nx=False, ex=None):
        del key, value, ex
        self.acquire_attempts += 1
        return False if nx else True


class _OwnableRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script, numkeys, *args):
        del script, numkeys
        key, token = args[0], args[1]
        if self.values.get(key) == token:
            self.values.pop(key)
            return 1
        return 0


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(job_leader_lock.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(job_leader_lock.settings, "job_leader_retry_seconds", 10.0)
    job_leader_lock.clear_held_job_leadership_for_tests()
    reset_job_health_for_tests()
    yield
    job_leader_lock.clear_held_job_leadership_for_tests()
    reset_job_health_for_tests()


def test_run_leader_tick_maps_a_skip_to_incomplete_and_keeps_other_results(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: _HeldElsewhereRedis())

    def _work() -> int:
        return 0 if job_leader_lock.try_acquire_job_leadership("demo") is None else 7

    assert job_leader_lock.run_leader_tick("demo", _work) is JOB_TICK_INCOMPLETE
    assert job_leader_lock.next_tick_wait_seconds("demo", 1800) <= 12.0

    monkeypatch.setattr(cache, "_redis", lambda: _OwnableRedis())
    assert job_leader_lock.run_leader_tick("demo", _work) == 7
    assert job_leader_lock.next_tick_wait_seconds("demo", 1800) == 1800.0


def test_redis_down_is_a_skip_too(monkeypatch) -> None:
    monkeypatch.setattr(cache, "_redis", lambda: None)
    result = job_leader_lock.run_leader_tick(
        "demo", lambda: job_leader_lock.try_acquire_job_leadership("demo")
    )
    assert result is JOB_TICK_INCOMPLETE


def test_retry_wait_is_capped_at_the_poll() -> None:
    assert job_leader_lock.leader_retry_wait_seconds(5) == 5.0
    assert 10.0 <= job_leader_lock.leader_retry_wait_seconds(3600) <= 12.0


def test_skip_flag_is_per_thread(monkeypatch) -> None:
    # A capture endpoint skipping on a request thread must not mark the sampler's tick.
    monkeypatch.setattr(cache, "_redis", lambda: _HeldElsewhereRedis())
    other = threading.Thread(target=lambda: job_leader_lock.try_acquire_job_leadership("demo"))
    other.start()
    other.join()
    assert job_leader_lock.leadership_skipped("demo") is False


def test_release_all_removes_owned_keys_and_leaves_foreign_ones(monkeypatch) -> None:
    client = _OwnableRedis()
    monkeypatch.setattr(cache, "_redis", lambda: client)
    assert job_leader_lock.try_acquire_job_leadership("alert-rule-evaluator") is not None
    assert job_leader_lock.try_acquire_job_leadership("reference-refresh") is not None
    # Our reference-refresh lease expired and another worker took the key.
    client.values["job:leader:v1:reference-refresh"] = "other-worker-token"

    job_leader_lock.release_all_job_leadership()

    assert "job:leader:v1:alert-rule-evaluator" not in client.values
    assert client.values["job:leader:v1:reference-refresh"] == "other-worker-token"


def test_released_token_is_no_longer_tracked(monkeypatch) -> None:
    client = _OwnableRedis()
    monkeypatch.setattr(cache, "_redis", lambda: client)
    token = job_leader_lock.try_acquire_job_leadership("demo")
    job_leader_lock.release_job_leadership("demo", token)
    client.values["job:leader:v1:demo"] = "other-worker-token"

    job_leader_lock.release_all_job_leadership()

    assert client.values["job:leader:v1:demo"] == "other-worker-token"


def _alert(monkeypatch):
    from app.services import alert_service as module

    monkeypatch.setattr(module, "_sync_repository_sessionlocal", lambda: None)
    monkeypatch.setattr(module.alert_repository, "is_available", lambda: True)
    service = module.alert_service
    return service, lambda: service._run_evaluator(60)


def _portfolio(monkeypatch):
    from app.services.portfolio_history_service import portfolio_history_service as service

    return service, lambda: service._run_sampler(300)


def _rankings(monkeypatch):
    from app.services.rankings_snapshot_service import rankings_snapshot_service as service

    return service, lambda: service._run_sampler(1800)


def _reference(monkeypatch):
    from app.services.reference_refresh_service import reference_refresh_service as service

    return service, lambda: service._run(900)


def _taiwan(monkeypatch):
    from app.services.taiwan_market_data_refresh_service import (
        taiwan_market_data_refresh_service as service,
    )

    return service, lambda: service._run_sampler(3600, 18, 7)


def _archive(monkeypatch):
    from app.services.rankings_archive_service import rankings_archive_service as service

    monkeypatch.setattr(job_leader_lock.settings, "rankings_archive_eod_enabled", True)
    monkeypatch.setattr(service, "is_configured", lambda: True)
    monkeypatch.setattr(service, "any_active_capture_window", lambda now: True)
    return service, lambda: service._run_sampler(900)


@pytest.mark.parametrize(
    ("job_name", "factory"),
    [
        ("alert-rule-evaluator", _alert),
        ("portfolio-history-sampler", _portfolio),
        ("rankings-snapshot-sampler", _rankings),
        ("reference-refresh", _reference),
        ("taiwan-market-data-refresh", _taiwan),
        ("rankings-archive-eod", _archive),
    ],
)
def test_each_job_skipped_for_leadership_records_no_success_and_retries_soon(
    monkeypatch, job_name, factory
) -> None:
    client = _HeldElsewhereRedis()
    monkeypatch.setattr(cache, "_redis", lambda: client)
    service, run_loop = factory(monkeypatch)
    stop = threading.Event()
    monkeypatch.setattr(service, "_stop_event", stop)
    waits: list[float] = []

    def _wait(timeout=None):
        # Let the loop reach its first tick, then stop at the next wait.
        waits.append(float(timeout or 0.0))
        if client.acquire_attempts == 0:
            return False
        stop.set()
        return True

    monkeypatch.setattr(stop, "wait", _wait)
    register_background_job(job_name, enabled=True)

    run_loop()

    assert client.acquire_attempts == 1
    health = get_background_jobs_health()[job_name]
    assert health["last_success_at"] is None
    assert health["last_tick_at"] is not None
    # The wait after the skipped tick is the short retry (10s + up to 20%), not the poll.
    assert 0 < waits[-1] <= 12.0, waits


def test_release_all_can_be_limited_to_some_jobs(monkeypatch) -> None:
    client = _OwnableRedis()
    monkeypatch.setattr(cache, "_redis", lambda: client)
    job_leader_lock.try_acquire_job_leadership("alert-rule-evaluator")
    job_leader_lock.try_acquire_job_leadership("reference-refresh")

    job_leader_lock.release_all_job_leadership(job_names=["reference-refresh"])

    # A still-running tick keeps its lease so the next worker cannot overlap it.
    assert "job:leader:v1:alert-rule-evaluator" in client.values
    assert "job:leader:v1:reference-refresh" not in client.values
