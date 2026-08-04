from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.services import quote_redis_cache as cache
from tests.support import quote_record


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    cache.clear_quote_cache_for_tests()
    monkeypatch.setattr(cache.settings, "redis_url", "")
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 60)
    monkeypatch.setattr(cache.settings, "quote_cache_hard_ttl_seconds", 900)
    monkeypatch.setattr(cache.settings, "quote_refresh_lock_seconds", 30)
    monkeypatch.setattr(cache.settings, "quote_redis_retry_base_seconds", 5.0)
    monkeypatch.setattr(cache.settings, "quote_redis_retry_max_seconds", 60.0)
    monkeypatch.setattr(cache.settings, "quote_redis_retry_jitter_seconds", 0.0)
    yield
    cache.clear_quote_cache_for_tests()


def _sample_quote(stock_id: str = "US:AAPL", *, last_price: float = 187.23):
    return quote_record(
        stock_id,
        last_price=last_price,
        change=1.5,
        change_percent=0.81,
        previous_close=185.73,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
        volume=100,
    )


def test_set_and_get_cached_quote_round_trip_memory() -> None:
    quote = _sample_quote()
    cache.set_cached_quote("US:AAPL", quote)

    entry = cache.get_cached_quote_entry("US:AAPL")

    assert entry is not None
    assert entry.quote.last_price == pytest.approx(187.23)
    assert entry.stale is False
    assert entry.age_seconds < 60


def test_get_cached_quote_entries_marks_stale_after_soft_ttl(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 10)
    quote = _sample_quote(last_price=10.0)
    cached_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    payload = json.dumps(cache._serialize(quote, cached_at=cached_at))
    with cache._memory_lock:
        # Hard retention still holds the payload after soft TTL.
        cache._memory_quotes["US:AAPL"] = (1e18, payload)

    now = cached_at + timedelta(seconds=15)
    entries = cache._get_entries_from_memory(["US:AAPL"], now=now)

    assert entries["US:AAPL"].stale is True
    assert entries["US:AAPL"].age_seconds == pytest.approx(15.0)


def test_stale_value_remains_readable_after_soft_ttl_via_public_api(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 10)
    monkeypatch.setattr(cache.settings, "quote_cache_hard_ttl_seconds", 900)
    quote = _sample_quote(last_price=11.0)
    cached_at = datetime.now(UTC) - timedelta(seconds=30)
    payload = json.dumps(cache._serialize(quote, cached_at=cached_at))
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, payload)

    entry = cache.get_cached_quote_entry("US:AAPL")

    assert entry is not None
    assert entry.stale is True
    assert entry.quote.last_price == pytest.approx(11.0)


def test_mget_reads_bulk_redis_entries(monkeypatch) -> None:
    quote = _sample_quote(last_price=1.0)
    payload = json.dumps(
        cache._serialize(quote, cached_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC))
    )
    client = MagicMock()
    client.mget.return_value = [payload, None]
    monkeypatch.setattr(cache, "_redis", lambda: client)

    entries = cache.get_cached_quote_entries(["US:AAPL", "US:MSFT"])

    assert set(entries) == {"US:AAPL"}
    client.mget.assert_called_once_with(["quote:v1:US:AAPL", "quote:v1:US:MSFT"])
    health = cache.get_quote_cache_health()
    assert health["last_success_read_at"] is not None


def test_redis_batch_skips_malformed_values_without_failing_batch(monkeypatch) -> None:
    good = _sample_quote("US:AAPL", last_price=1.0)
    good_payload = json.dumps(
        cache._serialize(good, cached_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC))
    )
    client = MagicMock()
    client.mget.return_value = [
        good_payload,
        json.dumps({"quote": {"id": "US:MSFT"}}),
        None,
    ]
    monkeypatch.setattr(cache, "_redis", lambda: client)

    entries = cache.get_cached_quote_entries(["US:AAPL", "US:MSFT", "US:GOOG"])

    assert set(entries) == {"US:AAPL"}
    assert entries["US:AAPL"].quote.last_price == pytest.approx(1.0)


def test_set_cached_quotes_uses_redis_pipeline(monkeypatch) -> None:
    pipe = MagicMock()
    client = MagicMock()
    client.pipeline.return_value = pipe
    monkeypatch.setattr(cache, "_redis", lambda: client)

    cache.set_cached_quotes(
        {
            "US:AAPL": _sample_quote("US:AAPL", last_price=1.0),
            "US:MSFT": _sample_quote("US:MSFT", last_price=2.0),
        }
    )

    client.pipeline.assert_called_once_with(transaction=False)
    assert pipe.setex.call_count == 2
    pipe.execute.assert_called_once()
    assert cache.get_quote_cache_health()["last_success_write_at"] is not None
    # Writes always mirror into the in-process cache.
    monkeypatch.setattr(cache, "_redis", lambda: None)
    assert cache.get_cached_quote("US:AAPL") is not None
    assert cache.get_cached_quote("US:MSFT") is not None


def test_refresh_lock_deduplicates_memory() -> None:
    token = cache.try_acquire_refresh_lock("US:AAPL")
    assert token is not None
    assert cache.try_acquire_refresh_lock("US:AAPL") is None
    cache.release_refresh_lock("US:AAPL", token)
    assert cache.try_acquire_refresh_lock("US:AAPL") is not None


def test_release_refresh_lock_ignores_foreign_owner_token() -> None:
    token = cache.try_acquire_refresh_lock("US:AAPL")
    assert token is not None
    cache.release_refresh_lock("US:AAPL", "not-the-owner")
    assert cache.try_acquire_refresh_lock("US:AAPL") is None
    cache.release_refresh_lock("US:AAPL", token)
    assert cache.try_acquire_refresh_lock("US:AAPL") is not None


def test_redis_mget_failure_falls_back_to_memory(monkeypatch) -> None:
    quote = _sample_quote(last_price=1.0)
    cache.set_cached_quote("US:AAPL", quote)
    client = MagicMock()
    client.mget.side_effect = RuntimeError("redis down")
    monkeypatch.setattr(cache, "_redis", lambda: client)

    entries = cache.get_cached_quote_entries(["US:AAPL"])

    assert set(entries) == {"US:AAPL"}
    assert entries["US:AAPL"].quote.last_price == pytest.approx(1.0)


def test_application_operates_when_redis_absent() -> None:
    health = cache.get_quote_cache_health()
    assert health["configured"] is False
    assert health["backend"] == "memory"
    assert health["connected"] is False
    assert health["last_success_ping_at"] is None
    assert health["last_success_read_at"] is None
    assert health["last_success_write_at"] is None

    cache.set_cached_quote("US:AAPL", _sample_quote())
    assert cache.get_cached_quote("US:AAPL") is not None
    token = cache.try_acquire_refresh_lock("US:AAPL")
    assert token is not None


def test_corrupt_payload_is_treated_as_miss() -> None:
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (
            1e18,
            json.dumps({"quote": {"id": "US:AAPL"}}),
        )

    entries = cache.get_cached_quote_entries(["US:AAPL"])

    assert entries == {}


def test_legacy_payload_without_cached_at_still_deserializes() -> None:
    legacy = {
        "id": "US:AAPL",
        "last_price": 10.0,
        "change": 0.0,
        "change_percent": 0.0,
        "previous_close": 10.0,
        "timestamp": "2026-04-01T09:30:00-04:00",
        "is_market_open": True,
        "open": None,
        "high": None,
        "low": None,
        "volume": None,
    }
    entry = cache._entry_from_payload(json.dumps(legacy))
    assert entry is not None
    assert entry.quote.id == "US:AAPL"
    assert entry.stale is True
    assert entry.age_seconds >= 60


def test_redis_reconnects_after_initial_connect_failure(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(cache, "_client", False)
    monkeypatch.setattr(cache, "_client_retry_after_monotonic", 0.0)
    attempts = {"count": 0}

    class FakeRedis:
        def ping(self):
            return True

    def fake_from_url(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("down")
        return FakeRedis()

    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url = fake_from_url
    monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis_lib)

    assert cache._redis() is None
    assert cache._client is False
    health = cache.get_quote_cache_health()
    assert health["configured"] is True
    assert health["connected"] is False
    assert health["state"] == "retry_wait"
    assert health["last_error_category"] == "connect"
    assert "redis://" not in json.dumps(health)

    # Cooldown elapsed.
    monkeypatch.setattr(cache, "_client_retry_after_monotonic", 0.0)
    client = cache._redis()
    assert client is not None
    assert attempts["count"] == 2
    health = cache.get_quote_cache_health()
    assert health["state"] == "connected"
    assert health["connected"] is True
    assert health["consecutive_failures"] == 0
    assert health["last_success_ping_at"] is not None


def test_repeated_failures_respect_retry_cooldown_and_backoff(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(cache.settings, "quote_redis_retry_base_seconds", 5.0)
    monkeypatch.setattr(cache.settings, "quote_redis_retry_max_seconds", 40.0)
    monkeypatch.setattr(cache.settings, "quote_redis_retry_jitter_seconds", 0.0)
    monkeypatch.setattr(cache.random, "uniform", lambda _a, _b: 0.0)
    attempts = {"count": 0}

    def fake_from_url(*_args, **_kwargs):
        attempts["count"] += 1
        raise ConnectionError("still down")

    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url = fake_from_url
    monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis_lib)

    assert cache._redis() is None
    assert attempts["count"] == 1
    assert cache._consecutive_failures == 1
    assert cache._next_retry_delay_seconds(1) == pytest.approx(5.0)

    # Still within cooldown: no extra connect attempt.
    assert cache._redis() is None
    assert attempts["count"] == 1

    monkeypatch.setattr(cache, "_client_retry_after_monotonic", 0.0)
    assert cache._redis() is None
    assert attempts["count"] == 2
    assert cache._consecutive_failures == 2
    assert cache._next_retry_delay_seconds(2) == pytest.approx(10.0)
    assert cache._next_retry_delay_seconds(4) == pytest.approx(40.0)  # capped


def test_runtime_connection_drop_invalidates_and_recreates_client(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "redis_url", "redis://localhost:6379/0")
    creations = {"count": 0}

    class FlakyRedis:
        def __init__(self):
            creations["count"] += 1
            self.generation = creations["count"]

        def ping(self):
            return True

        def mget(self, keys):
            if self.generation == 1:
                raise ConnectionError("connection reset")
            payload = json.dumps(
                cache._serialize(
                    _sample_quote(last_price=9.0),
                    cached_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
                )
            )
            return [payload]

    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url = lambda *_a, **_k: FlakyRedis()
    monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis_lib)

    first = cache._redis()
    assert first is not None
    assert first.generation == 1

    entries = cache.get_cached_quote_entries(["US:AAPL"])
    assert entries == {}
    assert cache._client is False
    health = cache.get_quote_cache_health()
    assert health["state"] == "retry_wait"
    assert health["last_error_category"] == "connect"

    monkeypatch.setattr(cache, "_client_retry_after_monotonic", 0.0)
    entries = cache.get_cached_quote_entries(["US:AAPL"])
    assert set(entries) == {"US:AAPL"}
    assert creations["count"] == 2
    assert cache.get_quote_cache_health()["state"] == "connected"


def test_reconnect_is_singleflight_across_threads(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(cache, "_client", False)
    monkeypatch.setattr(cache, "_client_retry_after_monotonic", 0.0)
    creations = {"count": 0}
    started = threading.Barrier(4)

    class SlowRedis:
        def __init__(self):
            creations["count"] += 1
            time.sleep(0.15)

        def ping(self):
            return True

    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url = lambda *_a, **_k: SlowRedis()
    monkeypatch.setitem(__import__("sys").modules, "redis", fake_redis_lib)

    results: list[object] = []

    def worker() -> None:
        started.wait()
        results.append(cache._redis())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert creations["count"] == 1
    assert all(item is not None for item in results)
    assert len({id(item) for item in results}) == 1


def test_quote_cache_health_omits_secrets_and_reflects_retry_state(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "redis_url", "redis://:super-secret@localhost:6379/0")
    monkeypatch.setattr(cache, "_client", False)
    monkeypatch.setattr(cache, "_client_retry_after_monotonic", time.monotonic() + 30.0)
    monkeypatch.setattr(cache, "_last_error_category", "timeout")
    monkeypatch.setattr(cache, "_consecutive_failures", 3)

    quote_cache = cache.get_quote_cache_health()
    assert quote_cache["configured"] is True
    assert quote_cache["connected"] is False
    assert quote_cache["state"] == "retry_wait"
    assert quote_cache["last_error_category"] == "timeout"
    assert quote_cache["consecutive_failures"] == 3
    assert quote_cache["retry_after_seconds"] is not None
    assert quote_cache["retry_after_seconds"] > 0
    assert "last_success_ping_at" in quote_cache
    assert "last_success_read_at" in quote_cache
    assert "last_success_write_at" in quote_cache
    serialized = json.dumps(quote_cache)
    assert "super-secret" not in serialized
    assert "redis://" not in serialized


def test_health_payload_exposes_fields_expected_by_http_surface() -> None:
    """`/health` nests this dict; keep the contract testable without importing app.main."""
    payload = cache.get_quote_cache_health()
    expected = {
        "configured",
        "state",
        "connected",
        "backend",
        "last_success_at",
        "last_success_ping_at",
        "last_success_read_at",
        "last_success_write_at",
        "last_error_at",
        "last_error_category",
        "consecutive_failures",
        "retry_after_seconds",
    }
    assert expected <= set(payload)
