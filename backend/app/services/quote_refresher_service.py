"""Background quote refresher for watchlist and portfolio symbols.

Keeps Redis (or in-process) quote cache warm with market-aware cadence,
bounded concurrency, per-provider spacing, jitter, circuit breakers, and
Redis locks so multiple backend replicas do not stampede free providers.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
import random
import threading
import time
from typing import Callable
from uuid import uuid4

from app.core.config import settings
from app.core.diagnostics import elapsed_ms
from app.repositories.portfolio_repository import portfolio_repository
from app.repositories.watchlist_repository import watchlist_repository
from app.services.http_client import (
    HttpFetchError,
    set_provider_request_spacing,
)
from app.services.market_clock import is_market_open, is_within_hour_before_market_open
from app.services.quote_redis_cache import (
    get_cached_quote_entries,
    redis_enabled,
    refresh_lock_seconds,
    release_refresh_lock,
    renew_refresh_lock,
    set_cached_quote,
    try_acquire_refresh_lock,
)
from app.services.quote_service import quote_service

logger = logging.getLogger(__name__)

_LEADER_KEY = "quote:refresher:v1:leader"
_MARKETS = ("US", "TW")
_HTTP_PROVIDER_TO_MARKET = {
    "yahoo_finance": "US",
    "stooq": "US",
    "twse": "TW",
    "twse_openapi": "TW",
    "tpex": "TW",
}
_RELEASE_LEADER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""


@dataclass
class ProviderCircuitState:
    consecutive_failures: int = 0
    open_until_monotonic: float = 0.0
    backoff_until_monotonic: float = 0.0
    last_classification: str | None = None

    def is_open(self, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        return current < self.open_until_monotonic or current < self.backoff_until_monotonic


@dataclass
class QuoteRefreshCycleStats:
    considered: int = 0
    refreshed: int = 0
    skipped_fresh: int = 0
    skipped_locked: int = 0
    skipped_circuit: int = 0
    skipped_closed_fresh: int = 0
    failed: int = 0
    timed_out: int = 0
    # How many duplicate tracked rows were collapsed into unique IDs.
    duplicates_collapsed: int = 0
    provider_requests: dict[str, int] = field(default_factory=dict)
    success_by_provider: dict[str, int] = field(default_factory=dict)
    failure_by_classification: dict[str, int] = field(default_factory=dict)
    cache_age_seconds_sample: list[float] = field(default_factory=list)
    duration_ms: float = 0.0
    in_flight_peak: int = 0


class QuoteRefresherService:
    def __init__(
        self,
        *,
        watchlist_repo=watchlist_repository,
        portfolio_repo=portfolio_repository,
        quote_source=quote_service,
        now_provider: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._watchlist_repo = watchlist_repo
        self._portfolio_repo = portfolio_repo
        self._quote_source = quote_source
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn
        self._random = random_fn
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._immediate: set[str] = set()
        self._immediate_lock = threading.Lock()
        self._circuits: dict[str, ProviderCircuitState] = {
            market: ProviderCircuitState() for market in _MARKETS
        }
        self._circuit_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_provider_slot: dict[str, float] = dict.fromkeys(_MARKETS, 0.0)
        max_workers = max(int(settings.quote_refresher_max_concurrency), 1)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="quote-refresher",
        )
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()
        self._in_flight_peak = 0
        self._cycle_cursor = 0
        self._priority_refresh: list[str] = []
        self._priority_lock = threading.Lock()

    def is_sampler_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start_background_sampler(self) -> None:
        if not self._watchlist_repo.is_available() or not self._portfolio_repo.is_available():
            logger.info(
                "Quote refresher skipped because the database is unavailable",
                extra={
                    "event": "quote_refresher.sampler.skipped",
                    "reason": "database_unavailable",
                },
            )
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug(
                    "Quote refresher was already running",
                    extra={"event": "quote_refresher.sampler.already_running"},
                )
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run_sampler,
                daemon=True,
                name="quote-refresher",
            )
            self._thread.start()
        logger.info(
            "Quote refresher started",
            extra={
                "event": "quote_refresher.sampler.started",
                "job_name": "quote-refresher",
                "open_poll_seconds": settings.quote_refresher_open_poll_seconds,
                "closed_poll_seconds": settings.quote_refresher_closed_poll_seconds,
                "max_concurrency": settings.quote_refresher_max_concurrency,
                "min_interval_seconds": settings.quote_refresher_min_interval_seconds,
            },
        )

    def stop_background_sampler(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._thread = None
            self._stop_event.set()
        thread.join(timeout=2.0)
        logger.info(
            "Quote refresher stopped",
            extra={"event": "quote_refresher.sampler.stopped"},
        )

    def request_immediate_refresh(self, stock_id: str) -> None:
        """Queue a newly tracked symbol onto the bounded refresher pool."""
        normalized = stock_id.strip()
        if not normalized:
            return
        with self._immediate_lock:
            already_queued = normalized in self._immediate
            self._immediate.add(normalized)
        if already_queued or not settings.quote_refresher_enabled:
            return
        self._pool.submit(self._safe_immediate_refresh, normalized)

    def _safe_immediate_refresh(self, stock_id: str) -> None:
        try:
            outcome = self._refresh_one(stock_id)
            self._apply_refresh_outcome(stock_id, outcome)
            if outcome == "refreshed":
                with self._immediate_lock:
                    self._immediate.discard(stock_id)
        except Exception as exc:  # noqa: BLE001 - never kill caller paths
            self._apply_refresh_exception(stock_id, exc)
            logger.warning(
                "Immediate quote refresh failed",
                extra={
                    "event": "quote_refresher.immediate.failed",
                    "stock_id": stock_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    def collect_tracked_stock_ids(self) -> tuple[list[str], int]:
        """Return unique IDs and how many duplicate rows were collapsed."""
        watchlist_ids = self._watchlist_repo.list_distinct_stock_ids()
        portfolio_ids = self._portfolio_repo.list_distinct_position_stock_ids()
        with self._immediate_lock:
            immediate = sorted(self._immediate)
            # Keep immediate IDs until a successful refresh removes them.
        raw_count = len(watchlist_ids) + len(portfolio_ids) + len(immediate)
        unique = list(dict.fromkeys([*watchlist_ids, *portfolio_ids, *immediate]))
        return unique, max(0, raw_count - len(unique))

    def select_poll_seconds(self, *, now: datetime | None = None) -> int:
        current = now or self._now_provider()
        if any(is_market_open(market, current) for market in _MARKETS):
            return int(settings.quote_refresher_open_poll_seconds)
        if any(is_within_hour_before_market_open(market, current) for market in _MARKETS):
            return int(settings.quote_refresher_open_poll_seconds)
        return int(settings.quote_refresher_closed_poll_seconds)

    def open_markets(self, *, now: datetime | None = None) -> set[str]:
        current = now or self._now_provider()
        markets: set[str] = set()
        for market in _MARKETS:
            if is_market_open(market, current) or is_within_hour_before_market_open(
                market, current
            ):
                markets.add(market)
        return markets

    def run_cycle(self) -> QuoteRefreshCycleStats:
        started_at = time.perf_counter()
        stats = QuoteRefreshCycleStats()
        leader_token = self._try_acquire_cycle_leadership()
        if leader_token is None:
            logger.info(
                "Quote refresher skipped cycle; another replica holds leadership "
                "or Redis leadership is unavailable",
                extra={"event": "quote_refresher.cycle.skipped_leader"},
            )
            return stats

        try:
            stock_ids, duplicates = self.collect_tracked_stock_ids()
            stats.considered = len(stock_ids)
            stats.duplicates_collapsed = duplicates
            to_refresh = self._select_symbols_to_refresh(stock_ids, stats)
            if not to_refresh:
                stats.duration_ms = elapsed_ms(started_at)
                self._log_cycle(stats)
                return stats

            self._run_refresh_batch(to_refresh, stats)
            stats.duration_ms = elapsed_ms(started_at)
            self._log_cycle(stats)
            return stats
        finally:
            self._release_cycle_leadership(leader_token)

    def _select_symbols_to_refresh(
        self,
        stock_ids: list[str],
        stats: QuoteRefreshCycleStats,
    ) -> list[str]:
        ordered_ids = self._order_symbols_for_cycle(stock_ids)
        now = self._now_provider()
        open_markets = self.open_markets(now=now)
        open_min_age = float(settings.quote_refresher_open_poll_seconds)
        closed_min_age = float(settings.quote_refresher_closed_poll_seconds)
        cached = get_cached_quote_entries(ordered_ids)
        to_refresh: list[str] = []
        for stock_id in ordered_ids:
            market = stock_id.split(":", 1)[0]
            if self._circuit_is_open(market):
                stats.skipped_circuit += 1
                continue
            entry = cached.get(stock_id)
            if entry is not None:
                stats.cache_age_seconds_sample.append(entry.age_seconds)
            if market in open_markets:
                # Refresh on the configured open cadence, not soft-TTL freshness.
                if entry is not None and entry.age_seconds < open_min_age:
                    stats.skipped_fresh += 1
                    continue
            elif entry is not None and entry.age_seconds < closed_min_age:
                stats.skipped_closed_fresh += 1
                continue
            to_refresh.append(stock_id)
        return to_refresh

    def _order_symbols_for_cycle(self, stock_ids: list[str]) -> list[str]:
        """Prioritize previously timed-out IDs and rotate the remainder."""
        if not stock_ids:
            return []
        id_set = set(stock_ids)
        with self._priority_lock:
            priority = [sid for sid in self._priority_refresh if sid in id_set]
            self._priority_refresh = [sid for sid in self._priority_refresh if sid in id_set]
        remaining = [sid for sid in stock_ids if sid not in set(priority)]
        if remaining:
            offset = self._cycle_cursor % len(remaining)
            remaining = remaining[offset:] + remaining[:offset]
            self._cycle_cursor = (self._cycle_cursor + 1) % len(remaining)
        return [*priority, *remaining]

    def _note_timed_out_priority(self, stock_id: str) -> None:
        with self._priority_lock:
            if stock_id not in self._priority_refresh:
                self._priority_refresh.append(stock_id)

    def _run_refresh_batch(
        self,
        to_refresh: list[str],
        stats: QuoteRefreshCycleStats,
    ) -> None:
        futures: dict[Future[str], str] = {
            self._pool.submit(self._refresh_one, stock_id): stock_id
            for stock_id in to_refresh
        }
        timeout = float(settings.quote_refresher_cycle_timeout_seconds)
        recorded: set[str] = set()
        with self._in_flight_lock:
            self._in_flight_peak = 0
        try:
            for future in as_completed(futures, timeout=timeout):
                stock_id = futures[future]
                recorded.add(stock_id)
                self._record_future_outcome(future, stock_id, stats)
                with self._priority_lock:
                    if stock_id in self._priority_refresh:
                        self._priority_refresh = [
                            item for item in self._priority_refresh if item != stock_id
                        ]
        except FuturesTimeout:
            self._finalize_timed_out_futures(futures, recorded, stats)
        with self._in_flight_lock:
            stats.in_flight_peak = self._in_flight_peak

    def _finalize_timed_out_futures(
        self,
        futures: dict[Future[str], str],
        recorded: set[str],
        stats: QuoteRefreshCycleStats,
    ) -> None:
        for future, stock_id in futures.items():
            if stock_id in recorded:
                continue
            if future.done() and not future.cancelled():
                recorded.add(stock_id)
                self._record_future_outcome(future, stock_id, stats)
                continue
            cancelled = future.cancel()
            stats.timed_out += 1
            self._note_timed_out_priority(stock_id)
            logger.warning(
                "Quote refresher cycle timed out waiting for symbol",
                extra={
                    "event": "quote_refresher.cycle.timeout_symbol",
                    "stock_id": stock_id,
                    "cancelled": cancelled,
                },
            )
            if cancelled:
                continue
            # Running futures cannot be cancelled; still account their eventual
            # outcome so 429/Retry-After update backoff/circuit state.
            future.add_done_callback(
                lambda done, sid=stock_id: self._record_late_future_outcome(done, sid)
            )

    def _record_late_future_outcome(self, future: Future[str], stock_id: str) -> None:
        try:
            late_stats = QuoteRefreshCycleStats()
            self._record_future_outcome(future, stock_id, late_stats)
            logger.info(
                "Quote refresher recorded late future outcome",
                extra={
                    "event": "quote_refresher.cycle.late_outcome",
                    "stock_id": stock_id,
                    "symbols_refreshed": late_stats.refreshed,
                    "symbols_failed": late_stats.failed,
                    "failure_by_classification": late_stats.failure_by_classification,
                },
            )
        except Exception as exc:  # noqa: BLE001 - never raise into pool callbacks
            logger.debug("Late future outcome handling failed: %s", exc)

    def jitter_seconds(self) -> float:
        upper = float(settings.quote_refresher_jitter_seconds)
        if upper <= 0:
            return 0.0
        return float(self._random(0.0, upper))

    def _run_sampler(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as exc:  # noqa: BLE001 - keep sampler alive
                logger.exception(
                    "Quote refresher cycle crashed",
                    extra={
                        "event": "quote_refresher.cycle.failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
            poll_seconds = self.select_poll_seconds()
            if self._stop_event.wait(poll_seconds):
                return

    def _refresh_one(self, stock_id: str) -> str:
        market = stock_id.split(":", 1)[0]
        # Soft backoff is evaluated at cycle planning time only so one failure
        # cannot abort sibling symbols already submitted in this cycle.
        if self._hard_circuit_is_open(market):
            return "circuit"
        delay = self.jitter_seconds()
        if delay > 0:
            self._sleep(delay)
        self._wait_for_provider_slot(market)
        token = try_acquire_refresh_lock(stock_id)
        if token is None:
            return "locked"
        if not renew_refresh_lock(stock_id, token):
            return "locked"
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=self._renew_lock_until,
            args=(stock_id, token, stop_heartbeat),
            daemon=True,
            name=f"quote-lock-{stock_id}",
        )
        heartbeat.start()
        with self._in_flight_lock:
            self._in_flight += 1
            self._in_flight_peak = max(self._in_flight_peak, self._in_flight)
        previous_spacing = None
        try:
            from app.services.http_client import get_provider_request_spacing

            previous_spacing = get_provider_request_spacing()
            set_provider_request_spacing(self._spacing_for_http_provider)
            attempt = self._quote_source.fetch_quote_for_refresh(stock_id)
            if attempt.quote is not None:
                set_cached_quote(stock_id, attempt.quote)
                with self._immediate_lock:
                    self._immediate.discard(stock_id)
                return "refreshed"
            if attempt.error is not None:
                raise attempt.error
            return "empty"
        finally:
            set_provider_request_spacing(previous_spacing)
            stop_heartbeat.set()
            heartbeat.join(timeout=1.0)
            with self._in_flight_lock:
                self._in_flight = max(0, self._in_flight - 1)
            release_refresh_lock(stock_id, token)

    def _renew_lock_until(
        self,
        stock_id: str,
        token: str,
        stop_event: threading.Event,
    ) -> None:
        interval = max(5.0, float(refresh_lock_seconds()) / 3.0)
        while not stop_event.wait(interval):
            if not renew_refresh_lock(stock_id, token):
                return

    def _spacing_for_http_provider(self, provider_name: str) -> None:
        market = _HTTP_PROVIDER_TO_MARKET.get(provider_name)
        if market is None:
            return
        self._wait_for_provider_slot(market)

    def _wait_for_provider_slot(self, market: str) -> None:
        interval = float(settings.quote_refresher_min_interval_seconds)
        if interval <= 0:
            return
        with self._rate_lock:
            now = time.monotonic()
            ready_at = self._next_provider_slot.get(market, 0.0)
            wait = max(0.0, ready_at - now)
            self._next_provider_slot[market] = max(now, ready_at) + interval
        if wait > 0:
            self._sleep(wait)

    def _try_acquire_cycle_leadership(self) -> str | None:
        """Return an owner token when this replica should run the cycle.

        Fail closed when Redis is configured but unavailable, so replicas do not
        stampede providers during a Redis outage.
        """
        redis_url = (settings.redis_url or "").strip()
        if not redis_url:
            # Local/dev without Redis: single-process leadership.
            return "local"
        if not redis_enabled():
            logger.warning(
                "Quote refresher skipping cycle; Redis configured but unavailable",
                extra={"event": "quote_refresher.leader.redis_unavailable"},
            )
            return None
        from app.services.quote_redis_cache import run_redis

        ttl = max(
            int(settings.quote_refresher_open_poll_seconds),
            int(settings.quote_refresher_cycle_timeout_seconds),
        ) + 15
        token = uuid4().hex
        acquired = run_redis(
            "quote refresher leader lock",
            lambda client: bool(client.set(_LEADER_KEY, token, nx=True, ex=ttl)),
            kind="write",
        )
        if acquired is None:
            logger.warning(
                "Quote refresher leader lock failed; skipping cycle",
                extra={
                    "event": "quote_refresher.leader.failed",
                    "error_type": "redis_unavailable",
                },
            )
            return None
        return token if acquired else None

    def _release_cycle_leadership(self, token: str) -> None:
        if token == "local":
            return
        from app.services.quote_redis_cache import run_redis

        run_redis(
            "quote refresher leader release",
            lambda client: client.eval(_RELEASE_LEADER_SCRIPT, 1, _LEADER_KEY, token),
            kind="write",
        )

    def _circuit_is_open(self, market: str) -> bool:
        with self._circuit_lock:
            state = self._circuits.setdefault(market, ProviderCircuitState())
            return state.is_open()

    def _hard_circuit_is_open(self, market: str) -> bool:
        """True only after the failure threshold trips the open window."""
        with self._circuit_lock:
            state = self._circuits.setdefault(market, ProviderCircuitState())
            return time.monotonic() < state.open_until_monotonic

    def _note_provider_success(self, market: str) -> None:
        with self._circuit_lock:
            state = self._circuits.setdefault(market, ProviderCircuitState())
            state.consecutive_failures = 0
            state.backoff_until_monotonic = 0.0
            state.open_until_monotonic = 0.0
            state.last_classification = None

    def _note_provider_failure(
        self,
        market: str,
        *,
        classification: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        threshold = int(settings.quote_refresher_circuit_failure_threshold)
        open_seconds = float(settings.quote_refresher_circuit_open_seconds)
        with self._circuit_lock:
            state = self._circuits.setdefault(market, ProviderCircuitState())
            state.consecutive_failures += 1
            state.last_classification = classification
            backoff = min(
                open_seconds,
                float(2 ** min(state.consecutive_failures, 8)),
            )
            if retry_after_seconds is not None:
                # Honor Retry-After but never exceed the circuit open window.
                capped_retry = min(float(retry_after_seconds), open_seconds)
                backoff = max(backoff, capped_retry)
            state.backoff_until_monotonic = time.monotonic() + backoff
            if state.consecutive_failures >= threshold:
                state.open_until_monotonic = time.monotonic() + open_seconds
                logger.warning(
                    "Quote refresher circuit opened",
                    extra={
                        "event": "quote_refresher.circuit.opened",
                        "provider": market,
                        "consecutive_failures": state.consecutive_failures,
                        "open_seconds": open_seconds,
                        "classification": classification,
                    },
                )

    def _record_future_outcome(
        self,
        future: Future[str],
        stock_id: str,
        stats: QuoteRefreshCycleStats,
    ) -> None:
        try:
            outcome = future.result()
        except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
            self._apply_refresh_exception(stock_id, exc, stats=stats)
            return
        self._apply_refresh_outcome(stock_id, outcome, stats=stats)

    def _apply_refresh_exception(
        self,
        stock_id: str,
        exc: Exception,
        *,
        stats: QuoteRefreshCycleStats | None = None,
    ) -> None:
        market = stock_id.split(":", 1)[0]
        classification = _classification_from_exc(exc)
        if stats is not None:
            stats.failed += 1
            stats.provider_requests[market] = stats.provider_requests.get(market, 0) + 1
            stats.failure_by_classification[classification] = (
                stats.failure_by_classification.get(classification, 0) + 1
            )
        self._note_provider_failure(
            market,
            classification=classification,
            retry_after_seconds=_retry_after_from_exc(exc),
        )

    def _apply_refresh_outcome(
        self,
        stock_id: str,
        outcome: str,
        *,
        stats: QuoteRefreshCycleStats | None = None,
    ) -> None:
        market = stock_id.split(":", 1)[0]
        if outcome == "refreshed":
            if stats is not None:
                stats.refreshed += 1
                stats.provider_requests[market] = (
                    stats.provider_requests.get(market, 0) + 1
                )
                stats.success_by_provider[market] = (
                    stats.success_by_provider.get(market, 0) + 1
                )
            self._note_provider_success(market)
            return
        if outcome == "locked":
            if stats is not None:
                stats.skipped_locked += 1
            return
        if outcome == "circuit":
            if stats is not None:
                stats.skipped_circuit += 1
            return
        classification = "empty" if outcome == "empty" else "http_error"
        if stats is not None:
            stats.failed += 1
            stats.provider_requests[market] = stats.provider_requests.get(market, 0) + 1
            stats.failure_by_classification[classification] = (
                stats.failure_by_classification.get(classification, 0) + 1
            )
        self._note_provider_failure(market, classification=classification)

    def _log_cycle(self, stats: QuoteRefreshCycleStats) -> None:
        with self._circuit_lock:
            circuit_state = {
                market: {
                    "open": state.is_open(),
                    "consecutive_failures": state.consecutive_failures,
                    "last_classification": state.last_classification,
                }
                for market, state in self._circuits.items()
            }
        ages = stats.cache_age_seconds_sample
        avg_age = round(sum(ages) / len(ages), 3) if ages else None
        logger.info(
            "Quote refresher cycle completed",
            extra={
                "event": "quote_refresher.cycle.completed",
                "duration_ms": stats.duration_ms,
                "symbols_considered": stats.considered,
                "symbols_refreshed": stats.refreshed,
                "symbols_skipped_fresh": stats.skipped_fresh,
                "symbols_skipped_closed_fresh": stats.skipped_closed_fresh,
                "symbols_skipped_locked": stats.skipped_locked,
                "symbols_skipped_circuit": stats.skipped_circuit,
                "symbols_failed": stats.failed,
                "symbols_timed_out": stats.timed_out,
                "duplicates_collapsed": stats.duplicates_collapsed,
                "provider_requests": stats.provider_requests,
                "success_by_provider": stats.success_by_provider,
                "failure_by_classification": stats.failure_by_classification,
                "cache_age_avg_seconds": avg_age,
                "cache_age_samples": len(ages),
                "in_flight_peak": stats.in_flight_peak,
                "max_concurrency": settings.quote_refresher_max_concurrency,
                "circuit_state": circuit_state,
                "redis_enabled": redis_enabled(),
            },
        )


def _classification_from_exc(exc: Exception) -> str:
    if isinstance(exc, HttpFetchError):
        return exc.classification
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "network_failure"
    return "http_error"


def _retry_after_from_exc(exc: Exception) -> float | None:
    if isinstance(exc, HttpFetchError):
        return exc.retry_after_seconds
    return None


quote_refresher_service = QuoteRefresherService()
