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
from app.repositories.alert_repository import alert_repository
from app.repositories.portfolio_repository import portfolio_repository
from app.repositories.watchlist_repository import watchlist_repository
from app.services.http_client import (
    HttpFetchError,
    ProviderRequestAborted,
    set_provider_request_spacing,
)
from app.services.quote_service import QuoteRefreshBatch
from app.services.market_clock import (
    is_market_open,
    is_within_hour_before_market_open,
    timestamp_is_completed_session_close,
)
from app.services.quote_redis_cache import (
    clear_refresh_failure,
    get_cached_quote_entry,
    get_cached_quote_entries,
    record_refresh_failure,
    redis_enabled,
    refresh_failure_active,
    refresh_failure_backoff_seconds,
    refresh_lock_seconds,
    release_refresh_lock,
    renew_refresh_lock,
    set_cached_quote,
    try_acquire_refresh_lock,
)
from app.services.quote_service import quote_service

_IMMEDIATE_REDIS_KEY = "quote:immediate:v1"

logger = logging.getLogger(__name__)

_LEADER_KEY = "quote:refresher:v1:leader"
_MARKETS = ("US", "TW")
_RELEASE_LEADER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""

# Renew our own lease. Start-to-start cycles can begin a moment before the key
# this process set last cycle expires, which must not count as "held elsewhere" (#291).
_RENEW_LEADER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  redis.call("set", KEYS[1], ARGV[1], "EX", ARGV[2])
  return 1
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
    # True when the cycle did not run because leadership was not acquired (#291).
    skipped_leader: bool = False


class QuoteRefresherService:
    def __init__(
        self,
        *,
        watchlist_repo=watchlist_repository,
        portfolio_repo=portfolio_repository,
        alert_repo=alert_repository,
        quote_source=quote_service,
        now_provider: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._watchlist_repo = watchlist_repo
        self._portfolio_repo = portfolio_repo
        self._alert_repo = alert_repo
        self._quote_source = quote_source
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._sleep = sleep_fn
        self._random = random_fn
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_leader_until = 0.0
        # One token per process so a later cycle can renew its own lease (#291).
        self._cycle_leader_token = uuid4().hex
        self._held_refresh_locks: dict[str, str] = {}
        self._held_refresh_locks_guard = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._immediate: set[str] = set()
        self._immediate_lock = threading.Lock()
        self._immediate_dropped = 0
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
        immediate_workers = max(int(settings.quote_refresher_immediate_concurrency), 1)
        self._immediate_pool = ThreadPoolExecutor(
            max_workers=immediate_workers,
            thread_name_prefix="quote-immediate",
        )
        self._in_flight = 0
        self._in_flight_lock = threading.Lock()
        self._in_flight_peak = 0
        self._cycle_cursor = 0
        self._priority_refresh: list[str] = []
        self._priority_lock = threading.Lock()
        self._alert_ids: set[str] = set()
        self._alert_ids_lock = threading.Lock()
        self._deadline_local = threading.local()

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
            self._heartbeat_stop.set()
        thread.join(timeout=2.0)
        heartbeat = self._heartbeat_thread
        if heartbeat is not None:
            heartbeat.join(timeout=1.0)
            self._heartbeat_thread = None
        # Hand the window to the next worker now instead of after the TTL (#291).
        # The owner-checked script leaves another process's key alone.
        if self._cycle_leader_until > 0.0 and (settings.redis_url or "").strip():
            self._release_cycle_leadership(self._cycle_leader_token)
        self._cycle_leader_until = 0.0
        logger.info(
            "Quote refresher stopped",
            extra={"event": "quote_refresher.sampler.stopped"},
        )

    def request_immediate_refresh(self, stock_id: str) -> None:
        """Queue a newly tracked symbol onto the bounded immediate pool (#271).

        Closed-market symbols that already hold the session's closing print are
        skipped so UI polling cannot defeat the closed-market cycle (#270).
        An intraday print cached before the close is still refreshed. Failure
        backoff is honoured the same way as cache-first read paths. The
        immediate pool is separate from the scheduled cycle so a burst of
        cache-miss reads cannot starve the cycle deadline.

        When Redis is configured, API replicas (``RUN_BACKGROUND_JOBS=false``)
        publish into a shared Redis set that the worker drains (#276).
        """
        normalized = stock_id.strip()
        if not normalized:
            return
        if not settings.quote_refresher_enabled:
            return
        if refresh_failure_active(normalized):
            return
        if self._should_skip_closed_market_immediate(normalized):
            return
        queue_max = max(int(settings.quote_refresher_immediate_queue_max), 1)
        if settings.redis_url.strip() and not self.is_sampler_running():
            if self._enqueue_immediate_redis(normalized, queue_max=queue_max):
                return
            # Redis unavailable: fall through to local queue when this process
            # can still run work (single-process / jobs-on-API).
            if not settings.run_background_jobs:
                return
        with self._immediate_lock:
            already_queued = normalized in self._immediate
            if already_queued:
                return
            if len(self._immediate) >= queue_max:
                self._immediate_dropped += 1
                logger.warning(
                    "Immediate quote refresh queue full; dropping request",
                    extra={
                        "event": "quote_refresher.immediate.dropped",
                        "stock_id": normalized,
                        "queue_size": len(self._immediate),
                        "queue_max": queue_max,
                    },
                )
                return
            self._immediate.add(normalized)
        self._immediate_pool.submit(self._safe_immediate_refresh, normalized)

    def _enqueue_immediate_redis(self, stock_id: str, *, queue_max: int) -> bool:
        """Publish an immediate refresh request for the worker. True if handled."""
        if not redis_enabled():
            return False
        from app.services.quote_redis_cache import run_redis

        def _enqueue(client):
            if client.sismember(_IMMEDIATE_REDIS_KEY, stock_id):
                return True
            size = int(client.scard(_IMMEDIATE_REDIS_KEY) or 0)
            if size >= queue_max:
                return "full"
            client.sadd(_IMMEDIATE_REDIS_KEY, stock_id)
            client.expire(_IMMEDIATE_REDIS_KEY, 3600)
            return True

        result = run_redis("quote immediate enqueue", _enqueue, kind="write")
        if result == "full":
            self._immediate_dropped += 1
            logger.warning(
                "Immediate quote refresh Redis queue full; dropping request",
                extra={
                    "event": "quote_refresher.immediate.dropped_redis",
                    "stock_id": stock_id,
                    "queue_max": queue_max,
                },
            )
            return True
        return bool(result)

    def _drain_immediate_redis(self) -> None:
        """Pull Redis-queued symbols into the local immediate pool (worker)."""
        if not settings.redis_url.strip() or not redis_enabled():
            return
        from app.services.quote_redis_cache import run_redis

        queue_max = max(int(settings.quote_refresher_immediate_queue_max), 1)
        batch = run_redis(
            "quote immediate drain",
            lambda client: list(client.spop(_IMMEDIATE_REDIS_KEY, min(queue_max, 32)) or []),
            kind="write",
        )
        if not batch:
            return
        for raw in batch:
            stock_id = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            stock_id = stock_id.strip()
            if not stock_id:
                continue
            if refresh_failure_active(stock_id):
                continue
            if self._should_skip_closed_market_immediate(stock_id):
                continue
            with self._immediate_lock:
                if stock_id in self._immediate or len(self._immediate) >= queue_max:
                    continue
                self._immediate.add(stock_id)
            self._immediate_pool.submit(self._safe_immediate_refresh, stock_id)

    def _should_skip_closed_market_immediate(self, stock_id: str) -> bool:
        market = stock_id.split(":", 1)[0]
        if market not in _MARKETS:
            return False
        now = self._now_provider()
        if is_market_open(market, now) or is_within_hour_before_market_open(market, now):
            return False
        entry = get_cached_quote_entry(stock_id)
        if entry is None or entry.quote is None:
            return False
        return timestamp_is_completed_session_close(
            market,
            entry.quote.timestamp,
            now,
            allow_holiday_fetch=False,
        )

    def _safe_immediate_refresh(self, stock_id: str) -> None:
        outcome = "failed"
        try:
            outcome = self._refresh_one(stock_id, respect_soft_backoff=True)
            self._apply_refresh_outcome(stock_id, outcome)
            self._finalize_immediate_attempt(stock_id, outcome)
        except Exception as exc:  # noqa: BLE001 - never kill caller paths
            self._apply_refresh_exception(stock_id, exc)
            self._finalize_immediate_attempt(stock_id, "failed", error=exc)
        finally:
            # Always leave a terminal state so later polls can re-queue after backoff.
            with self._immediate_lock:
                self._immediate.discard(stock_id)

    def _finalize_immediate_attempt(
        self,
        stock_id: str,
        outcome: str,
        *,
        error: Exception | None = None,
    ) -> None:
        """Record success/failure markers for cache-miss refreshStatus accuracy."""
        if outcome == "refreshed":
            clear_refresh_failure(stock_id)
            logger.info(
                "Immediate quote refresh completed",
                extra={
                    "event": "quote_refresher.immediate.completed",
                    "stock_id": stock_id,
                    "outcome": "refreshed",
                },
            )
            return

        if outcome == "locked":
            # Another worker owns the refresh; do not start failure backoff.
            logger.info(
                "Immediate quote refresh deferred; lock held elsewhere",
                extra={
                    "event": "quote_refresher.immediate.locked",
                    "stock_id": stock_id,
                    "outcome": "locked",
                },
            )
            return

        if error is not None:
            classification = _classification_from_exc(error)
        elif outcome == "empty":
            classification = "empty"
        else:
            classification = outcome
        retry_after = _retry_after_from_exc(error) if error is not None else None
        market = stock_id.split(":", 1)[0]
        backoff_seconds = max(
            float(refresh_failure_backoff_seconds()),
            float(retry_after or 0.0),
            self._soft_backoff_remaining_seconds(market),
        )
        record_refresh_failure(stock_id, backoff_seconds=backoff_seconds)
        logger.warning(
            "Immediate quote refresh ended without a usable quote",
            extra={
                "event": "quote_refresher.immediate.terminal",
                "stock_id": stock_id,
                "outcome": outcome,
                "classification": classification,
                "backoff_seconds": backoff_seconds,
                "error_type": type(error).__name__ if error is not None else None,
            },
        )

    def collect_tracked_stock_ids(self) -> tuple[list[str], int]:
        """Return unique IDs tiered by demand and how many duplicates collapsed.

        Order: currently viewed (immediate) → alerted → watchlisted/portfolio
        so active use is refreshed first when a cycle cannot finish everyone (#271).
        """
        watchlist_ids = self._watchlist_repo.list_distinct_stock_ids()
        portfolio_ids = self._portfolio_repo.list_distinct_position_stock_ids()
        alert_ids: list[str] = []
        if self._alert_repo is not None and self._alert_repo.is_available():
            try:
                alert_ids = self._alert_repo.list_distinct_enabled_stock_ids()
            except Exception:  # noqa: BLE001 - never fail the refresher for alerts
                logger.debug(
                    "Could not load alert-tracked stock ids",
                    extra={"event": "quote_refresher.alerts.list_failed"},
                )
                alert_ids = []
        with self._immediate_lock:
            immediate = sorted(self._immediate)
        with self._alert_ids_lock:
            self._alert_ids = set(alert_ids)
        raw_count = (
            len(watchlist_ids) + len(portfolio_ids) + len(alert_ids) + len(immediate)
        )
        unique = list(
            dict.fromkeys([*immediate, *alert_ids, *watchlist_ids, *portfolio_ids])
        )
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
            stats.skipped_leader = True
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

            # One jitter per cycle, not per symbol (#271).
            delay = self.jitter_seconds()
            if delay > 0:
                self._sleep(delay)
            self._run_refresh_batch(to_refresh, stats)
            stats.duration_ms = elapsed_ms(started_at)
            self._log_cycle(stats)
            return stats
        except Exception:
            # A failed cycle may be retried by another replica. A finished cycle
            # keeps the lock until its TTL so the same window is not fetched twice.
            self._release_cycle_leadership(leader_token)
            self._cycle_leader_until = 0.0
            raise

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
        """Keep viewed and alerted symbols ahead of timeouts and the rotating tail."""
        if not stock_ids:
            return []
        id_set = set(stock_ids)
        with self._priority_lock:
            priority = [sid for sid in self._priority_refresh if sid in id_set]
            self._priority_refresh = [sid for sid in self._priority_refresh if sid in id_set]
        priority_set = set(priority)
        with self._immediate_lock:
            hot = set(self._immediate)
        with self._alert_ids_lock:
            hot |= set(self._alert_ids)
        # Hot demand stays ahead of a timed-out tail so one market cannot
        # starve the other market's viewed symbols on the next cycle (#271).
        hot_ids = [sid for sid in stock_ids if sid in hot]
        hot_set = set(hot_ids)
        priority_ids = [sid for sid in priority if sid not in hot_set]
        cold = [
            sid
            for sid in stock_ids
            if sid not in hot_set and sid not in priority_set
        ]
        if cold:
            offset = self._cycle_cursor % len(cold)
            cold = cold[offset:] + cold[:offset]
            self._cycle_cursor = (self._cycle_cursor + 1) % len(cold)
        return [*hot_ids, *priority_ids, *cold]

    def _market_batches(self, ordered_ids: list[str]) -> list[tuple[str, list[str]]]:
        """Group each demand tier by market without pulling a cold market forward."""
        if not ordered_ids:
            return []
        id_set = set(ordered_ids)
        with self._priority_lock:
            priority_set = {sid for sid in self._priority_refresh if sid in id_set}
        with self._immediate_lock:
            hot = set(self._immediate)
        with self._alert_ids_lock:
            hot |= set(self._alert_ids)
        hot_ids = [sid for sid in ordered_ids if sid in hot]
        hot_set = set(hot_ids)
        priority_ids = [
            sid for sid in ordered_ids if sid in priority_set and sid not in hot_set
        ]
        claimed = hot_set | set(priority_ids)
        cold = [sid for sid in ordered_ids if sid not in claimed]
        groups: list[tuple[str, list[str]]] = []
        for tier in (hot_ids, priority_ids, cold):
            by_market: dict[str, list[str]] = {}
            for stock_id in tier:
                market = stock_id.split(":", 1)[0]
                by_market.setdefault(market, []).append(stock_id)
            groups.extend(by_market.items())
        return groups

    def _note_timed_out_priority(self, stock_id: str) -> None:
        with self._priority_lock:
            if stock_id not in self._priority_refresh:
                self._priority_refresh.append(stock_id)

    def _run_refresh_batch(
        self,
        to_refresh: list[str],
        stats: QuoteRefreshCycleStats,
    ) -> None:
        """Refresh via provider batches first, then per-symbol fallback (#271)."""
        with self._in_flight_lock:
            self._in_flight_peak = 0
        self._arm_cycle_deadline()
        try:
            remaining = self._refresh_batched_markets(to_refresh, stats)
            if not remaining:
                with self._in_flight_lock:
                    stats.in_flight_peak = max(stats.in_flight_peak, self._in_flight_peak)
                return

            futures: dict[Future[str], str] = {
                self._pool.submit(
                    lambda sid=stock_id: self._refresh_one(sid, apply_jitter=False)
                ): stock_id
                for stock_id in remaining
            }
            timeout = self._cycle_timeout_remaining()
            recorded: set[str] = set()
            try:
                for future in as_completed(futures, timeout=timeout):
                    stock_id = futures[future]
                    recorded.add(stock_id)
                    self._record_future_outcome(future, stock_id, stats)
                    with self._priority_lock:
                        if stock_id in self._priority_refresh:
                            self._priority_refresh = [
                                item
                                for item in self._priority_refresh
                                if item != stock_id
                            ]
            except FuturesTimeout:
                self._finalize_timed_out_futures(futures, recorded, stats)
            with self._in_flight_lock:
                stats.in_flight_peak = max(stats.in_flight_peak, self._in_flight_peak)
        finally:
            self._clear_cycle_deadline()

    def _refresh_batched_markets(
        self,
        to_refresh: list[str],
        stats: QuoteRefreshCycleStats,
    ) -> list[str]:
        """Apply provider batch fetches; return ids that still need per-symbol work."""
        remaining: list[str] = []
        for market, ids in self._market_batches(to_refresh):
            if self._hard_circuit_is_open(market) or self._circuit_is_open(market):
                stats.skipped_circuit += len(ids)
                continue

            locked_tokens: dict[str, str] = {}
            unlocked: list[str] = []
            for stock_id in ids:
                token = try_acquire_refresh_lock(stock_id)
                if token is None:
                    stats.skipped_locked += 1
                    continue
                locked_tokens[stock_id] = token
                unlocked.append(stock_id)
            if not unlocked:
                continue

            for stock_id, token in locked_tokens.items():
                self._hold_refresh_lock(stock_id, token)
            still_held = set(unlocked)
            try:
                try:
                    raw = self._quote_source.fetch_quotes_for_refresh_batch(
                        unlocked,
                        before_chunk_for_market=self._before_chunk_for_market,
                    )
                except Exception as exc:  # noqa: BLE001 - fall back per symbol
                    logger.debug(
                        "Batch refresh unavailable; falling back per symbol: %s",
                        exc,
                    )
                    for stock_id in unlocked:
                        remaining.append(stock_id)
                    continue

                batch = raw if isinstance(raw, QuoteRefreshBatch) else QuoteRefreshBatch()
                timed_out = set(batch.timed_out_ids)
                deferred = set(batch.deferred_ids)
                for stock_id in unlocked:
                    token = locked_tokens[stock_id]
                    try:
                        if stock_id in timed_out:
                            stats.timed_out += 1
                            self._note_timed_out_priority(stock_id)
                            continue
                        if stock_id in deferred:
                            continue
                        attempt = batch.attempts.get(stock_id)
                        if attempt is None:
                            remaining.append(stock_id)
                            continue
                        if attempt.quote is not None:
                            set_cached_quote(stock_id, attempt.quote)
                            self._apply_refresh_outcome(
                                stock_id, "refreshed", stats=stats
                            )
                            with self._priority_lock:
                                if stock_id in self._priority_refresh:
                                    self._priority_refresh = [
                                        item
                                        for item in self._priority_refresh
                                        if item != stock_id
                                    ]
                            continue
                        if attempt.error is not None:
                            self._apply_refresh_exception(
                                stock_id, attempt.error, stats=stats
                            )
                            continue
                        remaining.append(stock_id)
                    finally:
                        still_held.discard(stock_id)
                        self._drop_refresh_lock(stock_id, token)
                        release_refresh_lock(stock_id, token)
                hard_stop = batch.hard_stops.get(market)
                if hard_stop is not None:
                    self._note_provider_failure(
                        market,
                        classification=_classification_from_exc(hard_stop),
                        retry_after_seconds=_retry_after_from_exc(hard_stop),
                    )
                    stats.failed += 1
                    stats.provider_requests[market] = (
                        stats.provider_requests.get(market, 0) + 1
                    )
                    classification = _classification_from_exc(hard_stop)
                    stats.failure_by_classification[classification] = (
                        stats.failure_by_classification.get(classification, 0) + 1
                    )
                    stats.skipped_circuit += sum(
                        1 for sid in unlocked if sid in deferred
                    )
            finally:
                for stock_id in list(still_held):
                    token = locked_tokens[stock_id]
                    still_held.discard(stock_id)
                    self._drop_refresh_lock(stock_id, token)
                    release_refresh_lock(stock_id, token)
        pending = set(remaining)
        return [stock_id for stock_id in to_refresh if stock_id in pending]

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

    def seconds_until_next_cycle(self, *, elapsed_seconds: float, skipped_leader: bool) -> float:
        """Start-to-start poll after a cycle; a short retry after a leadership skip (#291)."""
        poll_seconds = float(self.select_poll_seconds())
        if skipped_leader:
            retry = float(settings.quote_refresher_leader_retry_seconds)
            retry += float(self._random(0.0, retry * 0.2))
            return min(poll_seconds, retry)
        return max(0.0, poll_seconds - elapsed_seconds)

    def _run_sampler(self) -> None:
        from app.jobs.health import JOB_TICK_INCOMPLETE, run_guarded_tick

        while not self._stop_event.is_set():
            # Fixed period measured start-to-start so cycles stay inside soft TTL (#271).
            cycle_started = time.monotonic()
            # Any live worker may drain: SPOP is atomic and each symbol has its own lock (#291).
            self._drain_immediate_redis()
            skipped = False

            def _tick() -> object:
                nonlocal skipped
                stats = self.run_cycle()
                if stats.skipped_leader:
                    # Not a success: /health must not say the refresher worked (#291).
                    skipped = True
                    return JOB_TICK_INCOMPLETE
                return stats

            run_guarded_tick(
                "quote-refresher",
                event="quote_refresher.cycle.failed",
                tick=_tick,
            )
            wait_seconds = self.seconds_until_next_cycle(
                elapsed_seconds=time.monotonic() - cycle_started,
                skipped_leader=skipped,
            )
            # Drain Redis immediate requests while waiting so API replicas stay responsive (#276).
            remaining = wait_seconds
            while remaining > 0 and not self._stop_event.is_set():
                slice_seconds = min(2.0, remaining)
                if self._stop_event.wait(slice_seconds):
                    return
                remaining -= slice_seconds
                self._drain_immediate_redis()

    def _refresh_one(
        self,
        stock_id: str,
        *,
        respect_soft_backoff: bool = False,
        apply_jitter: bool = True,
    ) -> str:
        market = stock_id.split(":", 1)[0]
        # Soft backoff is evaluated at cycle planning time for batch cycles so one
        # failure cannot abort sibling symbols already submitted. Immediate
        # refreshes honor soft backoff so Retry-After windows are respected.
        if self._hard_circuit_is_open(market):
            return "circuit"
        if respect_soft_backoff and self._circuit_is_open(market):
            return "circuit"
        # Cycle path jitters once at the start; immediate may still jitter lightly.
        if apply_jitter:
            delay = self.jitter_seconds()
            if delay > 0:
                self._sleep(delay)
        token = try_acquire_refresh_lock(stock_id)
        if token is None:
            return "locked"
        # Shared heartbeat renews held locks; skip an immediate renew round-trip (#274).
        self._hold_refresh_lock(stock_id, token)
        with self._in_flight_lock:
            self._in_flight += 1
            self._in_flight_peak = max(self._in_flight_peak, self._in_flight)
        set_provider_request_spacing(self._space_symbol_requests(market))
        try:
            # The hook waits once per upstream attempt (chart, suffix, v7) and
            # does not add a second wait in front of that fetch (#271).
            try:
                attempt = self._quote_source.fetch_quote_for_refresh(stock_id)
            except ProviderRequestAborted:
                self._note_timed_out_priority(stock_id)
                return "timed_out"
            if attempt.quote is not None:
                set_cached_quote(stock_id, attempt.quote)
                return "refreshed"
            if attempt.error is not None:
                raise attempt.error
            return "empty"
        finally:
            set_provider_request_spacing(None)
            self._drop_refresh_lock(stock_id, token)
            with self._in_flight_lock:
                self._in_flight = max(0, self._in_flight - 1)
            release_refresh_lock(stock_id, token)

    def _renew_lock_until(
        self,
        stock_id: str,
        token: str,
        stop_event: threading.Event,
    ) -> None:
        self._renew_locks_until({stock_id: token}, stop_event)

    def _renew_locks_until(
        self,
        locked_tokens: dict[str, str],
        stop_event: threading.Event,
    ) -> None:
        interval = max(1.0, float(refresh_lock_seconds()) / 3.0)
        while not stop_event.wait(interval):
            for stock_id, token in locked_tokens.items():
                if not renew_refresh_lock(stock_id, token):
                    return

    def _space_symbol_requests(self, market: str) -> Callable[[str], None]:
        def _space(_provider: str) -> None:
            self._reserve_provider_slot(market)

        return _space

    def _before_chunk_for_market(self, market: str) -> Callable[[], None]:
        def _before() -> None:
            self._reserve_provider_slot(market)

        return _before

    def _arm_cycle_deadline(self) -> None:
        timeout = float(settings.quote_refresher_cycle_timeout_seconds)
        self._deadline_local.deadline = time.monotonic() + timeout

    def _clear_cycle_deadline(self) -> None:
        self._deadline_local.deadline = None

    def _abort_batch_if_deadline(self) -> None:
        deadline = getattr(self._deadline_local, "deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            raise ProviderRequestAborted()

    def _reserve_provider_slot(self, market: str) -> None:
        self._abort_batch_if_deadline()
        self._wait_for_provider_slot(market)
        self._abort_batch_if_deadline()

    def _cycle_timeout_remaining(self) -> float:
        deadline = getattr(self._deadline_local, "deadline", None)
        if deadline is None:
            return float(settings.quote_refresher_cycle_timeout_seconds)
        return max(0.0, deadline - time.monotonic())

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
            self._cycle_leader_until = time.monotonic() + 10**9
            return "local"
        if not redis_enabled():
            logger.warning(
                "Quote refresher skipping cycle; Redis configured but unavailable",
                extra={"event": "quote_refresher.leader.redis_unavailable"},
            )
            return None
        from app.services.quote_redis_cache import run_redis

        # Hold the window for one poll. Do not delete the key when the cycle
        # returns, or the other worker starts the same interval immediately.
        ttl = max(int(self.select_poll_seconds()), 1)
        token = self._cycle_leader_token

        def _acquire(client) -> bool:
            if client.set(_LEADER_KEY, token, nx=True, ex=ttl):
                return True
            return bool(client.eval(_RENEW_LEADER_SCRIPT, 1, _LEADER_KEY, token, ttl))

        acquired = run_redis("quote refresher leader lock", _acquire, kind="write")
        if acquired is None:
            logger.warning(
                "Quote refresher leader lock failed; skipping cycle",
                extra={
                    "event": "quote_refresher.leader.failed",
                    "error_type": "redis_unavailable",
                },
            )
            return None
        if acquired:
            self._cycle_leader_until = time.monotonic() + float(ttl)
            return token
        return None

    def _ensure_refresh_lock_heartbeat(self) -> None:
        with self._held_refresh_locks_guard:
            if (
                self._heartbeat_thread is not None
                and self._heartbeat_thread.is_alive()
            ):
                return
            self._heartbeat_stop.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._refresh_lock_heartbeat_loop,
                daemon=True,
                name="quote-lock-heartbeat",
            )
            self._heartbeat_thread.start()

    def _hold_refresh_lock(self, stock_id: str, token: str) -> None:
        self._ensure_refresh_lock_heartbeat()
        with self._held_refresh_locks_guard:
            self._held_refresh_locks[stock_id] = token

    def _drop_refresh_lock(self, stock_id: str, token: str) -> None:
        with self._held_refresh_locks_guard:
            if self._held_refresh_locks.get(stock_id) == token:
                self._held_refresh_locks.pop(stock_id, None)

    def _refresh_lock_heartbeat_loop(self) -> None:
        interval = max(1.0, float(refresh_lock_seconds()) / 3.0)
        while not self._heartbeat_stop.wait(interval):
            with self._held_refresh_locks_guard:
                pairs = list(self._held_refresh_locks.items())
            for stock_id, token in pairs:
                if not renew_refresh_lock(stock_id, token):
                    self._drop_refresh_lock(stock_id, token)

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

    def _soft_backoff_remaining_seconds(self, market: str) -> float:
        with self._circuit_lock:
            state = self._circuits.setdefault(market, ProviderCircuitState())
            remaining = state.backoff_until_monotonic - time.monotonic()
            return max(0.0, remaining)

    def _note_provider_success(self, market: str) -> None:
        with self._circuit_lock:
            state = self._circuits.setdefault(market, ProviderCircuitState())
            state.consecutive_failures = 0
            state.backoff_until_monotonic = 0.0
            state.open_until_monotonic = 0.0
            state.last_classification = None

    def _note_symbol_empty(self, stock_id: str) -> None:
        """Backoff one symbol without opening the market circuit (#268)."""
        record_refresh_failure(
            stock_id,
            backoff_seconds=float(refresh_failure_backoff_seconds()),
        )

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
            # Cycle path may refresh an immediate ID; clear so collect_tracked drops it.
            with self._immediate_lock:
                self._immediate.discard(stock_id)
            return
        if outcome == "locked":
            if stats is not None:
                stats.skipped_locked += 1
            return
        if outcome == "circuit":
            if stats is not None:
                stats.skipped_circuit += 1
            return
        if outcome == "timed_out":
            if stats is not None:
                stats.timed_out += 1
            return
        # Empty / unusable symbol data is per-symbol, not a market outage (#268).
        if outcome == "empty":
            if stats is not None:
                stats.failed += 1
                stats.provider_requests[market] = (
                    stats.provider_requests.get(market, 0) + 1
                )
                stats.failure_by_classification["empty"] = (
                    stats.failure_by_classification.get("empty", 0) + 1
                )
            self._note_symbol_empty(stock_id)
            return
        classification = "http_error"
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
