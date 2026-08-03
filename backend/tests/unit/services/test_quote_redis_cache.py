from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
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
    yield
    cache.clear_quote_cache_for_tests()


def test_set_and_get_cached_quote_round_trip_memory() -> None:
    quote = quote_record(
        "US:AAPL",
        last_price=187.23,
        change=1.5,
        change_percent=0.81,
        previous_close=185.73,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
        volume=100,
    )
    cache.set_cached_quote("US:AAPL", quote)

    entry = cache.get_cached_quote_entry("US:AAPL")

    assert entry is not None
    assert entry.quote.last_price == pytest.approx(187.23)
    assert entry.stale is False
    assert entry.age_seconds < 60


def test_get_cached_quote_entries_marks_stale_after_soft_ttl(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 10)
    quote = quote_record(
        "US:AAPL",
        last_price=10.0,
        change=0.0,
        change_percent=0.0,
        previous_close=10.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    cached_at = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    payload = json.dumps(cache._serialize(quote, cached_at=cached_at))
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, payload)

    now = cached_at + timedelta(seconds=15)
    entries = cache._get_entries_from_memory(["US:AAPL"], now=now)

    assert entries["US:AAPL"].stale is True
    assert entries["US:AAPL"].age_seconds == pytest.approx(15.0)


def test_mget_reads_bulk_redis_entries(monkeypatch) -> None:
    quote = quote_record(
        "US:AAPL",
        last_price=1.0,
        change=0.0,
        change_percent=0.0,
        previous_close=1.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    payload = json.dumps(
        cache._serialize(quote, cached_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC))
    )
    client = MagicMock()
    client.mget.return_value = [payload, None]
    monkeypatch.setattr(cache, "_redis", lambda: client)

    entries = cache.get_cached_quote_entries(["US:AAPL", "US:MSFT"])

    assert set(entries) == {"US:AAPL"}
    client.mget.assert_called_once_with(["quote:v1:US:AAPL", "quote:v1:US:MSFT"])


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
    quote = quote_record(
        "US:AAPL",
        last_price=1.0,
        change=0.0,
        change_percent=0.0,
        previous_close=1.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    cache.set_cached_quote("US:AAPL", quote)
    client = MagicMock()
    client.mget.side_effect = RuntimeError("redis down")
    monkeypatch.setattr(cache, "_redis", lambda: client)

    entries = cache.get_cached_quote_entries(["US:AAPL"])

    assert set(entries) == {"US:AAPL"}
    assert entries["US:AAPL"].quote.last_price == pytest.approx(1.0)


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
    # Cooldown elapsed.
    monkeypatch.setattr(cache, "_client_retry_after_monotonic", 0.0)
    client = cache._redis()
    assert client is not None
    assert attempts["count"] == 2
