from __future__ import annotations

from datetime import UTC, datetime
import threading
import time

import pytest

from app.services import quote_redis_cache as cache
from app.services.http_client import HttpFetchError
from app.services.quote_refresher_service import (
    ProviderCircuitState,
    QuoteRefresherService,
)
from app.services.quote_service import QuoteService
from tests.support import (
    FakeTrackedIdsRepo,
    StubUSProvider,
    age_memory_cached_quote,
    quote_record,
    stock_record,
)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    cache.clear_quote_cache_for_tests()
    monkeypatch.setattr(cache.settings, "redis_url", "")
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 60)
    monkeypatch.setattr(cache.settings, "quote_cache_hard_ttl_seconds", 3600)
    monkeypatch.setattr(cache.settings, "quote_refresh_lock_seconds", 30)
    monkeypatch.setattr(cache.settings, "quote_refresher_enabled", True)
    monkeypatch.setattr(cache.settings, "quote_refresher_open_poll_seconds", 45)
    monkeypatch.setattr(cache.settings, "quote_refresher_closed_poll_seconds", 900)
    monkeypatch.setattr(cache.settings, "quote_refresher_max_concurrency", 2)
    monkeypatch.setattr(cache.settings, "quote_refresher_min_interval_seconds", 0.0)
    monkeypatch.setattr(cache.settings, "quote_refresher_cycle_timeout_seconds", 40)
    monkeypatch.setattr(cache.settings, "quote_refresher_jitter_seconds", 0.0)
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_failure_threshold", 3)
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_open_seconds", 60)
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_market_open",
        lambda market, now=None: market == "US",
    )
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_within_hour_before_market_open",
        lambda market, now=None: False,
    )
    yield
    cache.clear_quote_cache_for_tests()


def _service(
    *,
    watchlist: list[str] | None = None,
    portfolio: list[str] | None = None,
    provider: StubUSProvider | None = None,
    now: datetime | None = None,
    random_values: list[float] | None = None,
) -> tuple[QuoteRefresherService, StubUSProvider]:
    stub = provider or StubUSProvider()
    quote_source = QuoteService()
    quote_source.providers = {"US": stub}
    randoms = list(random_values or [])

    def fake_random(low: float, high: float) -> float:
        if randoms:
            return randoms.pop(0)
        return low

    refresher = QuoteRefresherService(
        watchlist_repo=FakeTrackedIdsRepo(watchlist),
        portfolio_repo=FakeTrackedIdsRepo(portfolio),
        quote_source=quote_source,
        now_provider=lambda: now or datetime(2026, 5, 12, 14, 0, tzinfo=UTC),
        sleep_fn=lambda _seconds: None,
        random_fn=fake_random,
    )
    return refresher, stub


def test_collect_tracked_stock_ids_deduplicates_across_users_and_portfolios() -> None:
    refresher, _stub = _service(
        watchlist=["US:AAPL", "US:MSFT", "US:AAPL"],
        portfolio=["US:MSFT", "US:AAPL"],
    )

    ids, duplicates = refresher.collect_tracked_stock_ids()
    assert ids == ["US:AAPL", "US:MSFT"]
    assert duplicates == 3


def test_select_poll_seconds_uses_open_and_closed_cadence(monkeypatch) -> None:
    open_now = datetime(2026, 5, 12, 14, 0, tzinfo=UTC)
    closed_now = datetime(2026, 5, 12, 22, 0, tzinfo=UTC)
    refresher, _ = _service()

    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_market_open",
        lambda market, now=None: now == open_now,
    )
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_within_hour_before_market_open",
        lambda market, now=None: False,
    )
    assert refresher.select_poll_seconds(now=open_now) == 45
    assert refresher.select_poll_seconds(now=closed_now) == 900


def test_run_cycle_refreshes_stale_and_missing_only() -> None:
    refresher, stub = _service(watchlist=["US:AAPL", "US:MSFT"])
    cache.set_cached_quote("US:AAPL", stub._quotes["US:AAPL"])

    stats = refresher.run_cycle()

    assert stats.skipped_fresh == 1
    assert stats.refreshed == 1
    assert stub.quote_calls == ["US:MSFT"]
    assert cache.get_cached_quote("US:MSFT") is not None


def test_run_cycle_continues_after_one_symbol_failure() -> None:
    stub = StubUSProvider()
    stub.fail_ids.add("US:AAPL")
    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"], provider=stub)

    stats = refresher.run_cycle()

    assert stats.failed == 1
    assert stats.refreshed == 1
    assert set(stub.quote_calls) == {"US:AAPL", "US:MSFT"}
    assert cache.get_cached_quote("US:MSFT") is not None
    assert cache.get_cached_quote("US:AAPL") is None


def test_run_cycle_continues_when_failure_finishes_first() -> None:
    """Regression: soft backoff must not abort sibling symbols mid-cycle."""
    stub = StubUSProvider()
    stub.fail_ids.add("US:AAPL")
    fail_done = threading.Event()
    original_get_quote = stub.get_quote

    def gated_get_quote(stock_id: str):
        if stock_id == "US:MSFT":
            fail_done.wait(timeout=2.0)
        try:
            return original_get_quote(stock_id)
        finally:
            if stock_id == "US:AAPL":
                fail_done.set()

    stub.get_quote = gated_get_quote  # type: ignore[method-assign]
    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"], provider=stub)

    stats = refresher.run_cycle()

    assert stats.failed == 1
    assert stats.refreshed == 1
    assert stats.skipped_circuit == 0
    assert set(stub.quote_calls) == {"US:AAPL", "US:MSFT"}


def test_circuit_skip_does_not_count_as_failure(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_failure_threshold", 5)
    stub = StubUSProvider()
    stub.fail_ids.add("US:AAPL")
    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"], provider=stub)
    original = refresher._refresh_one

    def refresh_with_injected_circuit(stock_id: str) -> str:
        if stock_id == "US:MSFT":
            return "circuit"
        return original(stock_id)

    refresher._refresh_one = refresh_with_injected_circuit  # type: ignore[method-assign]

    stats = refresher.run_cycle()

    assert stats.failed == 1
    assert stats.skipped_circuit == 1
    assert stats.refreshed == 0
    assert refresher._circuits["US"].consecutive_failures == 1


def test_swallowed_provider_http_error_still_classifies_retry_after(monkeypatch) -> None:
    stub = StubUSProvider()
    rate_limited = HttpFetchError(
        "rate limited",
        status_code=429,
        classification="rate_limited",
        retry_after_seconds=9.0,
    )

    def swallow(_stock_id: str):
        stub.quote_calls.append(_stock_id)
        from app.services import http_client as http_client_module

        http_client_module._remember_http_fetch_error(rate_limited)
        return None

    stub.get_quote = swallow  # type: ignore[method-assign]
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_failure_threshold", 2)
    refresher, _ = _service(watchlist=["US:AAPL"], provider=stub)

    first = refresher.run_cycle()
    second = refresher.run_cycle()

    assert first.failed == 1
    assert first.failure_by_classification == {"rate_limited": 1}
    assert second.skipped_circuit == 1
    assert refresher._circuits["US"].is_open()
    assert refresher._circuits["US"].backoff_until_monotonic > time.monotonic()


def test_jitter_remains_within_configured_range(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_jitter_seconds", 2.5)
    seen: list[tuple[float, float]] = []

    def tracking_random(low: float, high: float) -> float:
        seen.append((low, high))
        return 1.25

    refresher, _ = _service()
    refresher._random = tracking_random
    assert refresher.jitter_seconds() == pytest.approx(1.25)
    assert seen == [(0.0, 2.5)]


def test_bounded_concurrency_uses_configured_worker_limit(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_max_concurrency", 2)
    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"])
    assert refresher._pool._max_workers == 2
    refresher.run_cycle()


def test_provider_min_interval_spaces_requests(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_min_interval_seconds", 0.5)
    sleeps: list[float] = []
    refresher, stub = _service(watchlist=["US:AAPL", "US:MSFT"])
    refresher._sleep = lambda seconds: sleeps.append(seconds)
    refresher.run_cycle()
    assert stub.quote_calls
    # Second concurrent US fetch waits ~min_interval; wall clock between lock
    # acquisitions can shave a few ms off the sleep.
    assert any(s == pytest.approx(0.5, abs=0.05) for s in sleeps)


def test_rate_limited_failure_opens_backoff_and_circuit(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_failure_threshold", 2)
    stub = StubUSProvider()
    stub.fail_ids.add("US:AAPL")
    stub.fail_exc = HttpFetchError(
        "rate limited",
        status_code=429,
        classification="rate_limited",
        retry_after_seconds=9.0,
    )
    refresher, _ = _service(watchlist=["US:AAPL"], provider=stub)

    first = refresher.run_cycle()
    second = refresher.run_cycle()

    assert first.failed == 1
    assert second.skipped_circuit == 1
    assert stub.quote_calls == ["US:AAPL"]
    state = refresher._circuits["US"]
    assert state.is_open()
    assert state.backoff_until_monotonic > time.monotonic()

    stub.fail_ids.clear()
    refresher._note_provider_success("US")
    assert refresher._circuits["US"].is_open() is False
    third = refresher.run_cycle()
    assert third.refreshed == 1


def test_exponential_backoff_increases_with_failures() -> None:
    refresher, _ = _service()
    now = time.monotonic()
    refresher._note_provider_failure("US", classification="network_failure")
    first_delta = refresher._circuits["US"].backoff_until_monotonic - now
    refresher._note_provider_failure("US", classification="network_failure")
    second_until = refresher._circuits["US"].backoff_until_monotonic
    second_delta = second_until - time.monotonic()
    assert second_delta > first_delta
    assert refresher._circuits["US"].consecutive_failures == 2
    # 2^1=2 then 2^2=4
    assert first_delta == pytest.approx(2.0, abs=0.05)
    assert second_delta == pytest.approx(4.0, abs=0.05)


def test_stale_cache_remains_available_during_provider_outage() -> None:
    stub = StubUSProvider()
    cache.set_cached_quote("US:AAPL", stub._quotes["US:AAPL"])
    age_memory_cached_quote("US:AAPL")
    stub.fail_ids.add("US:AAPL")
    stub.fail_exc = HttpFetchError(
        "forbidden",
        status_code=403,
        classification="auth_denied",
    )
    refresher, _ = _service(watchlist=["US:AAPL"], provider=stub)

    refresher.run_cycle()

    cached = cache.get_cached_quote("US:AAPL")
    assert cached is not None
    assert cached.last_price == pytest.approx(187.0)


def test_distributed_lock_skips_duplicate_refresh() -> None:
    refresher, stub = _service(watchlist=["US:AAPL"])
    token = cache.try_acquire_refresh_lock("US:AAPL")
    assert token is not None

    stats = refresher.run_cycle()

    assert stats.skipped_locked == 1
    assert stub.quote_calls == []
    cache.release_refresh_lock("US:AAPL", token)


def test_leader_lock_prevents_second_replica_cycle(monkeypatch) -> None:
    held: dict[str, str] = {}

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            del ex
            if nx and key in held:
                return False
            held[key] = value
            return True

        def eval(self, script, numkeys, *args):
            del script, numkeys
            key, token = args[0], args[1]
            if held.get(key) == token:
                held.pop(key, None)
                return 1
            return 0

        def mget(self, keys):
            return [None for _ in keys]

    monkeypatch.setattr(cache.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(cache, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(cache, "redis_enabled", lambda: True)

    first, stub = _service(watchlist=["US:AAPL"])
    second, _ = _service(watchlist=["US:AAPL"], provider=stub)
    # Leadership is per-cycle; hold the first replica's lock so the second cannot
    # steal the cycle while the first is still "leader".
    monkeypatch.setattr(first, "_release_cycle_leadership", lambda _token: None)

    first_stats = first.run_cycle()
    second_stats = second.run_cycle()

    assert first_stats.refreshed == 1
    assert second_stats.considered == 0
    assert stub.quote_calls == ["US:AAPL"]


def test_leader_lock_fail_closed_when_redis_configured_but_down(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(cache, "redis_enabled", lambda: False)
    refresher, stub = _service(watchlist=["US:AAPL"])

    stats = refresher.run_cycle()

    assert stats.considered == 0
    assert stub.quote_calls == []


def test_request_immediate_refresh_uses_shared_pool() -> None:
    refresher, stub = _service(watchlist=[])
    done = time.monotonic() + 2.0
    refresher.request_immediate_refresh("US:AAPL")
    while time.monotonic() < done and not stub.quote_calls:
        time.sleep(0.01)
    assert stub.quote_calls == ["US:AAPL"]
    assert cache.get_cached_quote("US:AAPL") is not None


def test_closed_market_symbols_refresh_on_closed_cadence(monkeypatch) -> None:
    """TW stays warm on closed cadence even while US is open."""
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_market_open",
        lambda market, now=None: market == "US",
    )
    stub = StubUSProvider()
    tw = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="TSMC",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_zh_tw="台積電",
    )
    stub._stocks["TW:2330"] = tw
    stub._quotes["TW:2330"] = quote_record(
        "TW:2330",
        last_price=500.0,
        change=1.0,
        change_percent=0.2,
        previous_close=499.0,
        timestamp="2026-05-12T13:00:00+08:00",
        is_market_open=False,
    )
    quote_source = QuoteService()
    quote_source.providers = {"US": stub, "TW": stub}
    refresher = QuoteRefresherService(
        watchlist_repo=FakeTrackedIdsRepo(["US:AAPL", "TW:2330"]),
        portfolio_repo=FakeTrackedIdsRepo([]),
        quote_source=quote_source,
        now_provider=lambda: datetime(2026, 5, 12, 14, 0, tzinfo=UTC),
        sleep_fn=lambda _seconds: None,
    )
    cache.set_cached_quote("US:AAPL", stub._quotes["US:AAPL"])
    # TW missing → should refresh even though TW is closed.
    stats = refresher.run_cycle()
    assert "TW:2330" in stub.quote_calls
    assert stats.refreshed >= 1


def test_request_immediate_refresh_dedupes_inflight_symbol(monkeypatch) -> None:
    refresher, stub = _service(watchlist=[])
    submitted: list[str] = []
    monkeypatch.setattr(
        refresher._pool,
        "submit",
        lambda fn, stock_id: submitted.append(stock_id),
    )
    refresher.request_immediate_refresh("US:AAPL")
    refresher.request_immediate_refresh("US:AAPL")
    assert submitted == ["US:AAPL"]
    assert "US:AAPL" in refresher._immediate


def test_retry_after_backoff_is_capped_by_circuit_open_window(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_open_seconds", 30)
    refresher, _ = _service()
    now = time.monotonic()
    refresher._note_provider_failure(
        "US",
        classification="rate_limited",
        retry_after_seconds=3600.0,
    )
    delta = refresher._circuits["US"].backoff_until_monotonic - now
    assert delta == pytest.approx(30.0, abs=0.05)


def test_cycle_timeout_counts_stragglers(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_cycle_timeout_seconds", 0.05)
    monkeypatch.setattr(cache.settings, "quote_refresher_max_concurrency", 1)
    gate = threading.Event()

    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"])
    # Block the single worker so the cycle deadline expires with work outstanding.
    monkeypatch.setattr(
        refresher,
        "_refresh_one",
        lambda _stock_id: (gate.wait(timeout=2.0), "refreshed")[1],
    )
    stats = refresher.run_cycle()
    gate.set()
    assert stats.timed_out >= 1
    assert "US:AAPL" in refresher._priority_refresh or "US:MSFT" in refresher._priority_refresh


def test_open_market_refresh_uses_poll_cadence_not_soft_ttl(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_open_poll_seconds", 45)
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 60)
    refresher, stub = _service(watchlist=["US:AAPL"])
    cache.set_cached_quote("US:AAPL", stub._quotes["US:AAPL"])
    from datetime import UTC, datetime, timedelta

    cached_at = (datetime.now(UTC) - timedelta(seconds=50)).isoformat().replace(
        "+00:00", "Z"
    )
    age_memory_cached_quote("US:AAPL", cached_at=cached_at)

    stats = refresher.run_cycle()

    assert stats.refreshed == 1
    assert stub.quote_calls == ["US:AAPL"]


def test_priority_rotation_puts_timed_out_symbols_first() -> None:
    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"])
    refresher._priority_refresh = ["US:MSFT"]
    ordered = refresher._order_symbols_for_cycle(["US:AAPL", "US:MSFT"])
    assert ordered[0] == "US:MSFT"


def test_immediate_refresh_empty_outcome_notes_circuit_failure() -> None:
    refresher, stub = _service(watchlist=[])
    stub.get_quote = lambda _stock_id: None  # type: ignore[method-assign]
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    assert refresher._circuits["US"].consecutive_failures >= 1
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")


def test_immediate_refresh_failure_clears_queue_and_allows_retry() -> None:
    refresher, stub = _service(watchlist=[])
    stub.get_quote = lambda _stock_id: None  # type: ignore[method-assign]
    submitted: list[str] = []
    refresher._pool.submit = (  # type: ignore[method-assign]
        lambda fn, stock_id: submitted.append(stock_id)
    )

    refresher.request_immediate_refresh("US:AAPL")
    assert submitted == ["US:AAPL"]
    refresher._safe_immediate_refresh("US:AAPL")
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")

    submitted.clear()
    refresher.request_immediate_refresh("US:AAPL")
    assert submitted == ["US:AAPL"]


def test_immediate_refresh_success_clears_failure_marker() -> None:
    refresher, stub = _service(watchlist=[])
    cache.record_refresh_failure("US:AAPL")
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    assert cache.get_cached_quote("US:AAPL") is not None
    assert "US:AAPL" not in refresher._immediate
    assert not cache.refresh_failure_active("US:AAPL")


def test_immediate_refresh_exception_clears_queue_and_records_failure() -> None:
    refresher, _stub = _service(watchlist=[])

    def boom(_stock_id: str, *, respect_soft_backoff: bool = False) -> str:
        raise HttpFetchError(
            "rate limited",
            classification="rate_limited",
            status_code=429,
            retry_after_seconds=12,
        )

    refresher._refresh_one = boom  # type: ignore[method-assign]
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")
    assert refresher._circuits["US"].consecutive_failures >= 1


def test_immediate_refresh_locked_clears_queue_without_failure_marker(
    monkeypatch,
) -> None:
    refresher, _stub = _service(watchlist=[])
    monkeypatch.setattr(
        "app.services.quote_refresher_service.try_acquire_refresh_lock",
        lambda _stock_id: None,
    )
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    assert "US:AAPL" not in refresher._immediate
    assert not cache.refresh_failure_active("US:AAPL")


def test_immediate_refresh_respects_soft_circuit_backoff() -> None:
    refresher, stub = _service(watchlist=[])
    refresher._circuits["US"] = ProviderCircuitState(
        consecutive_failures=1,
        backoff_until_monotonic=time.monotonic() + 30,
    )
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    assert stub.quote_calls == []
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")


def test_immediate_refresh_failure_marker_honors_retry_after() -> None:
    refresher, _stub = _service(watchlist=[])

    def boom(_stock_id: str, *, respect_soft_backoff: bool = False) -> str:
        raise HttpFetchError(
            "rate limited",
            classification="rate_limited",
            status_code=429,
            retry_after_seconds=45,
        )

    refresher._refresh_one = boom  # type: ignore[method-assign]
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")

    assert cache.refresh_failure_active("US:AAPL")
    with cache._memory_lock:
        until = cache._memory_refresh_failures["US:AAPL"]
    remaining = until - time.monotonic()
    assert remaining == pytest.approx(45, abs=1.5)


def test_immediate_refresh_circuit_outcome_clears_queue_and_records_failure() -> None:
    refresher, stub = _service(watchlist=[])
    refresher._circuits["US"] = ProviderCircuitState(
        consecutive_failures=5,
        open_until_monotonic=time.monotonic() + 60,
    )
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    assert stub.quote_calls == []
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")


def test_taiwan_provider_miss_does_not_refresh_cache_or_reset_circuit() -> None:
    """MIS+Yahoo miss must not re-cache last-good as fresh (#263)."""
    tw = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="TSMC",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_zh_tw="台積電",
    )
    prior = quote_record(
        "TW:2330",
        last_price=500.0,
        change=1.0,
        change_percent=0.2,
        previous_close=499.0,
        timestamp="2026-05-12T13:00:00+08:00",
        is_market_open=False,
    )
    cache.set_cached_quote("TW:2330", prior)
    age_memory_cached_quote("TW:2330")
    before = cache.get_cached_quote_entry("TW:2330")
    assert before is not None
    prior_cached_at = before.cached_at

    class EmptyTW:
        def get_quote(self, stock_id: str):
            return None

        def get_stock(self, stock_id: str):
            return tw if stock_id == "TW:2330" else None

    quote_source = QuoteService()
    quote_source.providers = {"TW": EmptyTW()}
    refresher = QuoteRefresherService(
        watchlist_repo=FakeTrackedIdsRepo(["TW:2330"]),
        portfolio_repo=FakeTrackedIdsRepo([]),
        quote_source=quote_source,
        now_provider=lambda: datetime(2026, 5, 12, 14, 0, tzinfo=UTC),
        sleep_fn=lambda _seconds: None,
    )
    monkeypatch_threshold = 3
    refresher._circuits["TW"] = ProviderCircuitState(consecutive_failures=0)

    for _ in range(monkeypatch_threshold):
        outcome = refresher._refresh_one("TW:2330")
        refresher._apply_refresh_outcome("TW:2330", outcome)

    after = cache.get_cached_quote_entry("TW:2330")
    assert after is not None
    assert after.cached_at == prior_cached_at
    assert after.stale is True
    assert after.quote.last_price == pytest.approx(500.0)
    assert refresher._circuits["TW"].consecutive_failures >= monkeypatch_threshold
    assert refresher._hard_circuit_is_open("TW")
