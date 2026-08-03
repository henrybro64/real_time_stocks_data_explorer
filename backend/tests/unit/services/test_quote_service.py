from __future__ import annotations

import threading
import time

import pytest

from app.services import quote_redis_cache as cache
from app.services.http_client import HttpFetchError
from app.services.quote_service import QuoteService
from tests.support import quote_record, stock_record


class StubUSProvider:
    market = "US"

    def __init__(self) -> None:
        self.quote_calls: list[str] = []
        self.live_quote_calls: list[str] = []
        self._stocks = {
            "US:AAPL": stock_record(
                "US:AAPL",
                market="US",
                symbol="AAPL",
                name="Apple Inc.",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Apple Inc.",
            ),
            "US:MSFT": stock_record(
                "US:MSFT",
                market="US",
                symbol="MSFT",
                name="Microsoft Corporation",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Microsoft Corporation",
            ),
        }
        self._quotes = {
            "US:AAPL": quote_record(
                "US:AAPL",
                last_price=187.0,
                change=1.0,
                change_percent=0.5,
                previous_close=186.0,
                timestamp="2026-05-12T10:00:00-04:00",
                is_market_open=True,
                volume=100,
            ),
            "US:MSFT": quote_record(
                "US:MSFT",
                last_price=100.0,
                change=1.0,
                change_percent=1.0,
                previous_close=99.0,
                timestamp="2026-05-12T10:00:00-04:00",
                is_market_open=True,
                volume=300,
            ),
        }
        self.quote_delay_seconds = 0.0
        self.fail_ids: set[str] = set()

    def list_stocks(self):
        return list(self._stocks.values())

    def search(self, query: str):
        return []

    def get_stock(self, stock_id: str):
        return self._stocks.get(stock_id)

    def get_quote(self, stock_id: str):
        self.quote_calls.append(stock_id)
        if self.quote_delay_seconds:
            time.sleep(self.quote_delay_seconds)
        if stock_id in self.fail_ids:
            raise RuntimeError(f"provider failed for {stock_id}")
        return self._quotes.get(stock_id)

    def get_live_quote(self, stock_id: str):
        self.live_quote_calls.append(stock_id)
        if stock_id == "US:MSFT":
            return self._quotes["US:MSFT"]
        return None

    def get_history(self, stock_id: str, range_name: str, *, limit: int | None = None):
        del limit
        return []


@pytest.fixture(autouse=True)
def _reset_quote_cache(monkeypatch):
    cache.clear_quote_cache_for_tests()
    monkeypatch.setattr(cache.settings, "redis_url", "")
    monkeypatch.setattr(cache.settings, "quote_cache_ttl_seconds", 60)
    monkeypatch.setattr(cache.settings, "quote_cache_hard_ttl_seconds", 900)
    monkeypatch.setattr(cache.settings, "quote_refresh_lock_seconds", 30)
    # Keep request-path SWR enqueue tests exercising the local scheduler.
    monkeypatch.setattr(cache.settings, "quote_refresher_enabled", False)
    monkeypatch.setattr(cache.settings, "quote_refresher_owns_upstream", False)
    yield
    cache.clear_quote_cache_for_tests()


def _service_with_provider(provider: StubUSProvider) -> QuoteService:
    service = QuoteService()
    service.providers = {"US": provider}
    return service


def test_get_live_quotes_uses_live_us_provider_and_skips_missing_symbols() -> None:
    service = QuoteService()
    provider = StubUSProvider()
    service.providers = {"US": provider}

    envelopes = service.get_live_quotes(["US:AAPL", "US:MSFT"])

    assert [stock.symbol for stock, _quote in envelopes] == ["MSFT"]
    assert provider.live_quote_calls == ["US:AAPL", "US:MSFT"]
    assert provider.quote_calls == []


def test_cache_first_batch_returns_fresh_cache_without_provider_calls() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    cache.set_cached_quote("US:MSFT", provider._quotes["US:MSFT"])

    items = service.get_quotes_cache_first(["US:AAPL", "US:MSFT"])

    assert [item.stock.id for item in items] == ["US:AAPL", "US:MSFT"]
    assert all(item.quote is not None for item in items)
    assert all(item.stale is False for item in items)
    assert all(item.refresh_status == "idle" for item in items)
    assert provider.quote_calls == []


def test_cache_first_batch_returns_stale_immediately_and_queues_refresh() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    refreshed = threading.Event()

    def capture_refresh(svc: QuoteService, stock_id: str, token: str) -> None:
        refreshed.set()
        svc._refresh_quote_in_background(stock_id, token)

    service._refresh_scheduler = capture_refresh
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    # Force soft-stale by rewriting payload age.
    entry = cache.get_cached_quote_entry("US:AAPL")
    assert entry is not None
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))

    started = time.perf_counter()
    items = service.get_quotes_cache_first(["US:AAPL"])
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2
    assert items[0].quote is not None
    assert items[0].stale is True
    assert items[0].refresh_status == "queued"
    assert refreshed.wait(timeout=1.0)
    assert "US:AAPL" in provider.quote_calls


def test_cache_first_miss_without_provider_is_unavailable() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    stock = provider._stocks["US:AAPL"]
    service.get_stock = lambda stock_id: stock if stock_id == "US:AAPL" else None  # type: ignore[method-assign]
    service.providers = {}
    scheduled: list[str] = []
    service._refresh_scheduler = lambda svc, stock_id, token: scheduled.append(stock_id)

    items = service.get_quotes_cache_first(["US:AAPL"])

    assert len(items) == 1
    assert items[0].quote is None
    assert items[0].refresh_status == "unavailable"
    assert scheduled == []


def test_cache_first_batch_missing_symbol_is_partial_and_queues_refresh() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    refreshed = threading.Event()

    def capture_refresh(svc: QuoteService, stock_id: str, token: str) -> None:
        refreshed.set()
        svc._refresh_quote_in_background(stock_id, token)

    service._refresh_scheduler = capture_refresh
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])

    items = service.get_quotes_cache_first(["US:AAPL", "US:MSFT"])

    by_id = {item.stock.id: item for item in items}
    assert by_id["US:AAPL"].quote is not None
    assert by_id["US:AAPL"].refresh_status == "idle"
    assert by_id["US:MSFT"].quote is None
    assert by_id["US:MSFT"].refresh_status == "queued"
    assert refreshed.wait(timeout=1.0)


def test_cache_first_batch_does_not_wait_for_slow_provider() -> None:
    provider = StubUSProvider()
    provider.quote_delay_seconds = 0.5
    service = _service_with_provider(provider)
    done = threading.Event()
    scheduled_token: dict[str, str] = {}

    def capture_refresh(svc: QuoteService, stock_id: str, token: str) -> None:
        scheduled_token[stock_id] = token
        # Do not run the slow provider inline — keep the batch response fast.
        done.set()

    service._refresh_scheduler = capture_refresh
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])

    started = time.perf_counter()
    items = service.get_quotes_cache_first(["US:AAPL", "US:MSFT"])
    elapsed = time.perf_counter() - started

    assert elapsed < 0.25
    assert items[0].quote is not None
    assert items[1].quote is None
    assert done.wait(timeout=1.0)
    # Release the acquired lease so later tests are not affected.
    cache.release_refresh_lock("US:MSFT", scheduled_token["US:MSFT"])


def test_duplicate_concurrent_requests_enqueue_one_refresh() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    scheduled: list[str] = []
    lock = threading.Lock()

    def capture_refresh(svc: QuoteService, stock_id: str, token: str) -> None:
        del token
        with lock:
            scheduled.append(stock_id)

    service._refresh_scheduler = capture_refresh

    first = service.get_quotes_cache_first(["US:AAPL"])
    second = service.get_quotes_cache_first(["US:AAPL"])

    assert first[0].refresh_status == "queued"
    assert second[0].refresh_status == "refreshing"
    assert scheduled == ["US:AAPL"]


def test_stale_duplicate_requests_mark_refreshing_not_idle() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    scheduled: list[str] = []

    def capture_refresh(svc: QuoteService, stock_id: str, token: str) -> None:
        del token
        scheduled.append(stock_id)

    service._refresh_scheduler = capture_refresh
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))

    first = service.get_quotes_cache_first(["US:AAPL"])
    second = service.get_quotes_cache_first(["US:AAPL"])

    assert first[0].stale is True
    assert first[0].refresh_status == "queued"
    assert second[0].stale is True
    assert second[0].refresh_status == "refreshing"
    assert scheduled == ["US:AAPL"]


def test_cache_first_keeps_stale_quote_when_background_refresh_fails() -> None:
    provider = StubUSProvider()
    provider.fail_ids.add("US:AAPL")
    service = _service_with_provider(provider)
    done = threading.Event()

    def run_refresh(svc: QuoteService, stock_id: str, token: str) -> None:
        svc._refresh_quote_in_background(stock_id, token)
        done.set()

    service._refresh_scheduler = run_refresh
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))

    first = service.get_quotes_cache_first(["US:AAPL"])
    assert first[0].quote is not None
    assert first[0].stale is True
    assert done.wait(timeout=1.0)

    second = service.get_quotes_cache_first(["US:AAPL"])
    assert second[0].quote is not None
    assert second[0].quote.last_price == pytest.approx(187.0)
    assert second[0].stale is True
    # Failure backoff surfaces stale idle instead of perpetual "Updating".
    assert second[0].refresh_status == "idle"


def test_get_quotes_does_not_serve_stale_fallback_for_rankings() -> None:
    provider = StubUSProvider()
    provider.fail_ids.add("US:AAPL")
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))

    with pytest.raises(RuntimeError, match="provider failed"):
        service.get_quotes(["US:AAPL"])


def test_get_quote_detail_reports_provider_source_on_uncached_fetch() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)

    item = service.get_quote_detail("US:AAPL")

    assert item is not None
    assert item.quote is not None
    assert item.source == "provider"
    assert item.stale is False
    assert provider.quote_calls == ["US:AAPL"]


def test_get_quote_detail_includes_cache_freshness() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])

    item = service.get_quote_detail("US:AAPL")

    assert item is not None
    assert item.quote is not None
    assert item.cached_at is not None
    assert item.source in {"memory", "redis"}
    assert item.refresh_status == "idle"
    assert provider.quote_calls == []


def test_get_quote_keeps_stale_cache_when_provider_fails() -> None:
    provider = StubUSProvider()
    provider.fail_ids.add("US:AAPL")
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))

    quote = service.get_quote("US:AAPL")

    assert quote is not None
    assert quote.last_price == pytest.approx(187.0)
    assert provider.quote_calls == ["US:AAPL"]


def test_get_quote_returns_stale_cache_when_provider_returns_none() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))
    provider._quotes.pop("US:AAPL")

    quote = service.get_quote("US:AAPL")

    assert quote is not None
    assert quote.last_price == pytest.approx(187.0)
    assert provider.quote_calls == ["US:AAPL"]


def test_cache_first_response_includes_freshness_metadata() -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])

    item = service.get_quotes_cache_first(["US:AAPL"])[0]

    assert item.cached_at is not None
    assert item.age_seconds is not None
    assert item.source in {"memory", "redis"}
    assert item.refresh_status == "idle"
    assert item.stale is False


def test_cache_first_does_not_enqueue_when_refresher_owns_upstream(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_enabled", True)
    monkeypatch.setattr(cache.settings, "quote_refresher_owns_upstream", True)
    monkeypatch.setattr(
        "app.services.quote_refresher_service.quote_refresher_service.is_sampler_running",
        lambda: True,
    )
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    scheduled: list[str] = []
    service._refresh_scheduler = (
        lambda svc, stock_id, token: scheduled.append(stock_id)
    )

    items = service.get_quotes_cache_first(["US:AAPL"])

    assert items[0].quote is None
    assert items[0].refresh_status == "queued"
    assert scheduled == []
    assert provider.quote_calls == []


def test_get_quote_does_not_hit_provider_when_refresher_owns_upstream(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_enabled", True)
    monkeypatch.setattr(cache.settings, "quote_refresher_owns_upstream", True)
    monkeypatch.setattr(
        "app.services.quote_refresher_service.quote_refresher_service.is_sampler_running",
        lambda: True,
    )
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))
    requested: list[str] = []
    monkeypatch.setattr(
        service,
        "_request_background_refresh",
        lambda stock_id: requested.append(stock_id),
    )

    quote = service.get_quote("US:AAPL")

    assert quote is not None
    assert quote.last_price == pytest.approx(187.0)
    assert provider.quote_calls == []
    assert requested == ["US:AAPL"]


def test_get_quote_falls_back_to_provider_when_refresher_not_running(monkeypatch) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_enabled", True)
    monkeypatch.setattr(cache.settings, "quote_refresher_owns_upstream", True)
    monkeypatch.setattr(
        "app.services.quote_refresher_service.quote_refresher_service.is_sampler_running",
        lambda: False,
    )
    provider = StubUSProvider()
    service = _service_with_provider(provider)

    quote = service.get_quote("US:AAPL")

    assert quote is not None
    assert provider.quote_calls == ["US:AAPL"]


def test_get_quote_detail_serves_stale_without_provider_when_refresher_owns(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cache.settings, "quote_refresher_enabled", True)
    monkeypatch.setattr(cache.settings, "quote_refresher_owns_upstream", True)
    monkeypatch.setattr(
        "app.services.quote_refresher_service.quote_refresher_service.is_sampler_running",
        lambda: True,
    )
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    cache.set_cached_quote("US:AAPL", provider._quotes["US:AAPL"])
    with cache._memory_lock:
        raw = cache._memory_quotes["US:AAPL"][1]
    payload = __import__("json").loads(raw)
    payload["cached_at"] = "2026-01-01T00:00:00Z"
    with cache._memory_lock:
        cache._memory_quotes["US:AAPL"] = (1e18, __import__("json").dumps(payload))
    requested: list[str] = []
    monkeypatch.setattr(
        service,
        "_request_background_refresh",
        lambda stock_id: requested.append(stock_id),
    )

    item = service.get_quote_detail("US:AAPL")

    assert item is not None
    assert item.quote is not None
    assert item.stale is True
    assert item.refresh_status == "queued"
    assert provider.quote_calls == []
    assert requested == ["US:AAPL"]


def test_fetch_quote_for_refresh_recovers_swallowed_http_error(monkeypatch) -> None:
    provider = StubUSProvider()
    service = _service_with_provider(provider)
    err = HttpFetchError(
        "rate limited",
        status_code=429,
        classification="rate_limited",
        retry_after_seconds=12.0,
    )

    def swallow(_stock_id: str):
        from app.services import http_client as http_client_module

        http_client_module._remember_http_fetch_error(err)
        return None

    provider.get_quote = swallow  # type: ignore[method-assign]

    attempt = service.fetch_quote_for_refresh("US:AAPL")

    assert attempt.quote is None
    assert attempt.error is err
    assert attempt.error.retry_after_seconds == pytest.approx(12.0)
