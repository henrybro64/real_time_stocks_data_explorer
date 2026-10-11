from __future__ import annotations

from dataclasses import replace
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
    # Cache soft-stale is market-aware (#270); keep open for age-based assertions.
    monkeypatch.setattr(cache, "is_market_open", lambda market, now=None, **_kwargs: True)
    monkeypatch.setattr(
        cache,
        "is_within_hour_before_market_open",
        lambda market, now=None: False,
    )
    yield
    cache.clear_quote_cache_for_tests()


def _service(
    *,
    watchlist: list[str] | None = None,
    portfolio: list[str] | None = None,
    alerts: list[str] | None = None,
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
        alert_repo=FakeTrackedIdsRepo(alerts),
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
        alerts=["US:AAPL", "US:GOOG"],
    )

    ids, duplicates = refresher.collect_tracked_stock_ids()
    # Tiered: alerts before watchlist/portfolio (#271).
    assert ids == ["US:AAPL", "US:GOOG", "US:MSFT"]
    assert duplicates == 4


def test_request_immediate_refresh_skips_closed_market_with_cached_quote(
    monkeypatch,
) -> None:
    refresher, stub = _service(watchlist=["US:AAPL"])
    closing = replace(
        stub._quotes["US:AAPL"],
        timestamp="2026-05-11T16:00:00-04:00",
        is_market_open=False,
    )
    cache.set_cached_quote("US:AAPL", closing)
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_market_open",
        lambda market, now=None: False,
    )
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_within_hour_before_market_open",
        lambda market, now=None: False,
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        refresher._pool,
        "submit",
        lambda fn, stock_id: submitted.append(stock_id),
    )

    refresher.request_immediate_refresh("US:AAPL")

    assert submitted == []
    assert stub.quote_calls == []


def test_request_immediate_refresh_still_runs_for_an_intraday_print_after_close(
    monkeypatch,
) -> None:
    refresher, stub = _service(watchlist=["US:AAPL"])
    cache.set_cached_quote("US:AAPL", stub._quotes["US:AAPL"])
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_market_open",
        lambda market, now=None: False,
    )
    monkeypatch.setattr(
        "app.services.quote_refresher_service.is_within_hour_before_market_open",
        lambda market, now=None: False,
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        refresher._immediate_pool,
        "submit",
        lambda fn, stock_id: submitted.append(stock_id),
    )

    refresher.request_immediate_refresh("US:AAPL")

    assert submitted == ["US:AAPL"]


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


def test_processing_error_releases_later_batch_locks(monkeypatch) -> None:
    from app.services.quote_refresher_service import QuoteRefreshCycleStats
    from app.services.quote_service import QuoteFetchAttempt, QuoteRefreshBatch

    refresher, stub = _service(watchlist=["US:AAPL", "US:MSFT"])
    quote = stub._quotes["US:AAPL"]
    batch = QuoteRefreshBatch(
        attempts={
            "US:AAPL": QuoteFetchAttempt(quote=quote),
            "US:MSFT": QuoteFetchAttempt(quote=quote),
        }
    )
    monkeypatch.setattr(
        refresher._quote_source,
        "fetch_quotes_for_refresh_batch",
        lambda _ids, **_kwargs: batch,
    )

    def boom(_stock_id: str, _outcome: str, stats=None) -> None:
        raise RuntimeError("processing failed")

    refresher._apply_refresh_outcome = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="processing failed"):
        refresher._refresh_batched_markets(
            ["US:AAPL", "US:MSFT"],
            QuoteRefreshCycleStats(),
        )
    assert refresher._held_refresh_locks == {}


def test_circuit_skip_does_not_count_as_failure(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_circuit_failure_threshold", 5)
    stub = StubUSProvider()
    stub.fail_ids.add("US:AAPL")
    refresher, _ = _service(watchlist=["US:AAPL", "US:MSFT"], provider=stub)
    # Exercise the per-symbol path so injected circuit outcomes are visible.
    monkeypatch.setattr(
        refresher._quote_source,
        "fetch_quotes_for_refresh_batch",
        lambda _ids, **_kwargs: {},
    )
    original = refresher._refresh_one

    def refresh_with_injected_circuit(stock_id: str, **kwargs) -> str:
        if stock_id == "US:MSFT":
            return "circuit"
        return original(stock_id, **kwargs)

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
    from app.services.http_client import get_provider_request_spacing

    monkeypatch.setattr(cache.settings, "quote_refresher_min_interval_seconds", 0.5)
    sleeps: list[float] = []
    refresher, stub = _service(watchlist=["US:AAPL", "US:MSFT"])
    # Per-symbol fallback spaces each upstream attempt, not the whole symbol once.
    monkeypatch.setattr(
        refresher._quote_source,
        "fetch_quotes_for_refresh_batch",
        lambda _ids, **_kwargs: {},
    )
    original = stub.get_quote

    def spaced(stock_id: str):
        hook = get_provider_request_spacing()
        assert hook is not None
        hook("yahoo_finance")
        hook("yahoo_finance")
        return original(stock_id)

    stub.get_quote = spaced  # type: ignore[method-assign]
    refresher._sleep = lambda seconds: sleeps.append(seconds)
    refresher.run_cycle()
    assert stub.quote_calls
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


def test_request_immediate_refresh_uses_immediate_pool() -> None:
    refresher, stub = _service(watchlist=[])
    done = time.monotonic() + 2.0
    refresher.request_immediate_refresh("US:AAPL")
    while time.monotonic() < done and cache.get_cached_quote("US:AAPL") is None:
        time.sleep(0.01)
    assert cache.get_cached_quote("US:AAPL") is not None
    assert stub.quote_calls == ["US:AAPL"]


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
        refresher._immediate_pool,
        "submit",
        lambda fn, stock_id: submitted.append(stock_id),
    )
    refresher.request_immediate_refresh("US:AAPL")
    refresher.request_immediate_refresh("US:AAPL")
    assert submitted == ["US:AAPL"]
    assert "US:AAPL" in refresher._immediate


def test_immediate_refresh_queue_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_immediate_queue_max", 2)
    refresher, _ = _service(watchlist=[])
    submitted: list[str] = []
    monkeypatch.setattr(
        refresher._immediate_pool,
        "submit",
        lambda fn, stock_id: submitted.append(stock_id),
    )
    refresher.request_immediate_refresh("US:AAPL")
    refresher.request_immediate_refresh("US:MSFT")
    refresher.request_immediate_refresh("US:GOOG")
    assert submitted == ["US:AAPL", "US:MSFT"]
    assert "US:GOOG" not in refresher._immediate


def test_cycle_500_symbols_completes_within_open_poll_interval(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_cycle_timeout_seconds", 45)
    ids = [f"US:S{i:04d}" for i in range(500)]
    stub = StubUSProvider()
    for stock_id in ids:
        symbol = stock_id.split(":", 1)[1]
        stub._stocks[stock_id] = stock_record(
            stock_id,
            market="US",
            symbol=symbol,
            name=symbol,
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        )
        stub._quotes[stock_id] = quote_record(
            stock_id,
            last_price=10.0,
            change=0.1,
            change_percent=1.0,
            previous_close=9.9,
            timestamp="2026-05-12T10:00:00-04:00",
            is_market_open=True,
        )
    refresher, _ = _service(watchlist=ids, provider=stub)
    started = time.perf_counter()
    stats = refresher.run_cycle()
    elapsed = time.perf_counter() - started
    assert elapsed < 45
    assert stats.refreshed == 500


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
    # Force the per-symbol path so the cycle deadline can expire mid-flight.
    monkeypatch.setattr(
        refresher._quote_source,
        "fetch_quotes_for_refresh_batch",
        lambda _ids, **_kwargs: {},
    )
    monkeypatch.setattr(
        refresher,
        "_refresh_one",
        lambda _stock_id, **_kwargs: (gate.wait(timeout=2.0), "refreshed")[1],
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


def test_immediate_refresh_empty_outcome_backs_off_symbol_not_market() -> None:
    refresher, stub = _service(watchlist=[])
    stub.get_quote = lambda _stock_id: None  # type: ignore[method-assign]
    refresher._immediate.add("US:AAPL")
    refresher._safe_immediate_refresh("US:AAPL")
    # Empty is per-symbol; five empties must not open the market circuit (#268).
    assert refresher._circuits["US"].consecutive_failures == 0
    assert not refresher._hard_circuit_is_open("US")
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")


def test_five_empty_symbols_do_not_open_market_circuit() -> None:
    refresher, stub = _service(
        watchlist=["US:AAPL", "US:MSFT", "US:NVDA", "US:GOOG", "US:AMZN"]
    )
    stub.get_quote = lambda _stock_id: None  # type: ignore[method-assign]
    for stock_id in ["US:AAPL", "US:MSFT", "US:NVDA", "US:GOOG", "US:AMZN"]:
        outcome = refresher._refresh_one(stock_id)
        refresher._apply_refresh_outcome(stock_id, outcome)
    assert refresher._circuits["US"].consecutive_failures == 0
    assert not refresher._hard_circuit_is_open("US")
    assert all(
        cache.refresh_failure_active(sid)
        for sid in ["US:AAPL", "US:MSFT", "US:NVDA", "US:GOOG", "US:AMZN"]
    )


def test_immediate_refresh_failure_clears_queue_and_honours_backoff() -> None:
    refresher, stub = _service(watchlist=[])
    stub.get_quote = lambda _stock_id: None  # type: ignore[method-assign]
    submitted: list[str] = []
    refresher._immediate_pool.submit = (  # type: ignore[method-assign]
        lambda fn, stock_id: submitted.append(stock_id)
    )

    refresher.request_immediate_refresh("US:AAPL")
    assert submitted == ["US:AAPL"]
    refresher._safe_immediate_refresh("US:AAPL")
    assert "US:AAPL" not in refresher._immediate
    assert cache.refresh_failure_active("US:AAPL")

    submitted.clear()
    # Failure backoff must suppress immediate re-queues (#270).
    refresher.request_immediate_refresh("US:AAPL")
    assert submitted == []


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


def test_taiwan_provider_miss_does_not_refresh_cache_or_open_market_circuit() -> None:
    """MIS+Yahoo miss must not re-cache last-good as fresh (#263) or pause TW (#268)."""
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
    refresher._circuits["TW"] = ProviderCircuitState(consecutive_failures=0)

    for _ in range(5):
        # Clear per-symbol backoff so each cycle attempt can run.
        cache.clear_refresh_failure("TW:2330")
        outcome = refresher._refresh_one("TW:2330")
        refresher._apply_refresh_outcome("TW:2330", outcome)

    after = cache.get_cached_quote_entry("TW:2330")
    assert after is not None
    assert after.cached_at == prior_cached_at
    assert after.stale is True
    assert after.quote.last_price == pytest.approx(500.0)
    assert refresher._circuits["TW"].consecutive_failures == 0
    assert not refresher._hard_circuit_is_open("TW")
    assert cache.refresh_failure_active("TW:2330")


def test_batch_rate_limit_keeps_parsed_quotes_and_does_not_fall_back() -> None:
    from app.services.quote_batch import QuoteBatchInterrupted

    ids = ["US:AAPL", "US:MSFT", "US:NVDA", "US:GOOG", "US:AMZN"]
    refresher, stub = _service(watchlist=ids)
    for stock_id in ids:
        if stock_id in stub._stocks:
            continue
        symbol = stock_id.split(":", 1)[1]
        stub._stocks[stock_id] = stock_record(
            stock_id,
            market="US",
            symbol=symbol,
            name=symbol,
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
        )
    fallback: list[str] = []
    stub.get_quote = lambda stock_id: fallback.append(stock_id)  # type: ignore[method-assign]

    def batch(_stocks, *, before_chunk=None):
        raise QuoteBatchInterrupted(
            quotes={"US:AAPL": stub._quotes["US:AAPL"]},
            deferred_ids=ids[1:],
            error=HttpFetchError(
                "rate limited",
                status_code=429,
                classification="rate_limited",
                retry_after_seconds=8,
            ),
        )

    stub.get_quotes_batch = batch  # type: ignore[method-assign]
    stats = refresher.run_cycle()

    assert fallback == []
    assert cache.get_cached_quote("US:AAPL") is not None
    assert cache.get_cached_quote("US:MSFT") is None
    assert stats.refreshed == 1
    assert stats.failed == 1
    assert stats.skipped_circuit == 4
    assert refresher._circuits["US"].consecutive_failures == 1


def test_batch_deadline_does_not_fall_back_per_symbol(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_cycle_timeout_seconds", 0)
    refresher, stub = _service(watchlist=["US:AAPL", "US:MSFT"])
    fallback: list[str] = []
    stub.get_quote = lambda stock_id: fallback.append(stock_id)  # type: ignore[method-assign]

    def batch(_stocks, *, before_chunk=None):
        if before_chunk is not None:
            before_chunk()
        raise AssertionError("chunk should not run past the deadline")

    stub.get_quotes_batch = batch  # type: ignore[method-assign]
    stats = refresher.run_cycle()

    assert fallback == []
    assert stats.timed_out == 2
    assert "US:AAPL" in refresher._priority_refresh
    assert "US:MSFT" in refresher._priority_refresh


def test_viewed_and_alerted_symbols_stay_ahead_of_rotation() -> None:
    refresher, _ = _service(watchlist=[])
    refresher._immediate.add("US:NVDA")
    refresher._alert_ids.add("US:MSFT")
    ids = ["US:NVDA", "US:MSFT", "US:AAPL", "US:GOOG"]

    first = refresher._order_symbols_for_cycle(ids)
    second = refresher._order_symbols_for_cycle(ids)

    assert first[:2] == ["US:NVDA", "US:MSFT"]
    assert second[:2] == ["US:NVDA", "US:MSFT"]
    assert first[2:] == ["US:AAPL", "US:GOOG"]
    assert second[2:] == ["US:GOOG", "US:AAPL"]


def test_hot_symbols_batch_ahead_of_the_other_market() -> None:
    refresher, _ = _service(watchlist=[])
    refresher._alert_ids.add("TW:2330")
    refresher._priority_refresh = ["US:AAPL", "US:MSFT"]
    ordered = refresher._order_symbols_for_cycle(["US:AAPL", "US:MSFT", "TW:2330"])

    assert ordered[0] == "TW:2330"
    groups = refresher._market_batches(ordered)
    assert groups[0] == ("TW", ["TW:2330"])
    assert groups[1][0] == "US"
    assert "TW:2330" not in groups[1][1]


def test_slot_wait_does_not_start_a_request_after_the_deadline(monkeypatch) -> None:
    from app.services.http_client import ProviderRequestAborted

    clock = {"now": 1_000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(cache.settings, "quote_refresher_min_interval_seconds", 5.0)
    refresher, _ = _service(watchlist=["US:AAPL"])
    refresher._sleep = lambda seconds: clock.__setitem__("now", clock["now"] + seconds)
    refresher._next_provider_slot["US"] = clock["now"] + 5.0
    refresher._deadline_local.deadline = clock["now"] + 1.0

    with pytest.raises(ProviderRequestAborted):
        refresher._reserve_provider_slot("US")


def test_batch_renews_refresh_locks_while_the_request_runs(monkeypatch) -> None:
    from app.services import quote_refresher_service as refresher_module

    monkeypatch.setattr(cache.settings, "quote_refresh_lock_seconds", 3)
    renewed: list[str] = []
    real_renew = refresher_module.renew_refresh_lock

    def renew(stock_id: str, token: str) -> bool:
        renewed.append(stock_id)
        return real_renew(stock_id, token)

    monkeypatch.setattr(refresher_module, "renew_refresh_lock", renew)
    refresher, _ = _service(watchlist=["US:AAPL"])
    during_batch: list[str] = []

    def slow(_ids, **_kwargs):
        time.sleep(1.4)
        during_batch.extend(renewed)
        return {}

    monkeypatch.setattr(
        refresher._quote_source,
        "fetch_quotes_for_refresh_batch",
        slow,
    )
    refresher.run_cycle()
    assert "US:AAPL" in during_batch
