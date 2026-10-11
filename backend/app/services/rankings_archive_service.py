"""End-of-day ranking archive capture (Azure SQL), isolated from GET /api/rankings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Literal

from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)
from sqlalchemy.exc import TimeoutError as SATimeoutError

from app.core.config import settings
from app.core.diagnostics import elapsed_ms
from app.db.archive_session import invalidate_rankings_archive_engine
from app.repositories.rankings_archive_repository import (
    CAPTURE_SCOPE_END_OF_DAY,
    rankings_archive_repository,
)
from app.schemas.rankings import RankingResponse, RankingSourceType, RankingType
from app.services.market_clock import (
    MARKET_TIMEZONES,
    TW_DAILY_PUBLISH_TIME,
    is_trading_day,
    market_local_time,
    market_session_close,
)
from app.services.ranking_data_providers import (
    RankingProviderRegistry,
    RankingQuery,
    ranking_provider_registry,
)


logger = logging.getLogger(__name__)

_MAX_ARCHIVE_LIMIT = 50
_US_SETTLE_MINUTES = 45
_TW_QUOTE_SETTLE_MINUTES = 30
# Bounded recent settled trading days to inspect for missed archives (#178).
_BACKFILL_TRADING_DAY_WINDOW = 7
# Cap provider fetches per query/cycle while filling holes.
_BACKFILL_FETCHES_PER_QUERY = 1
# After a settle becomes due, retry capture for this long before sleeping until
# the next session (covers provider lag without 15-minute forever polling) (#187).
_SAME_DAY_RETRY_BUDGET = timedelta(minutes=90)
# Opportunistic retention: at most about weekly per process, never a standalone wake (#188).
_RETENTION_PURGE_MIN_INTERVAL = timedelta(days=7)
_HISTORY_DATES_FAILED = "Rankings archive history dates lookup failed"
_HISTORY_LOAD_FAILED = "Rankings archive history load failed"


@dataclass
class _CaptureCycleState:
    """Per-invocation capture state (never shared across concurrent cycles)."""

    existence_cache: set[tuple[str, str, str, date]] | None = None
    sql_round_trips: int = 0


def _empty_capture_result(*, sql_round_trips: int = 0, failed: int = 0) -> dict[str, int]:
    return {
        "captured": 0,
        "skipped": 0,
        "failed": failed,
        "had_missing": 0,
        "still_missing": 0,
        "sql_round_trips": sql_round_trips,
    }


def _is_archive_connectivity_failure(exc: BaseException) -> bool:
    """True for connection/transport failures; false for SQL syntax/logic errors.

    Mid-life AutoPause / dead sockets should invalidate the cached engine (#177).
    ``ProgrammingError`` and similar app/SQL bugs must not (#181).
    """
    # Syntax / constraint / logic bugs must not drop a healthy engine.
    if isinstance(exc, ProgrammingError):
        return False
    if isinstance(
        exc,
        (OperationalError, InterfaceError, SATimeoutError, ConnectionError, TimeoutError),
    ):
        return True
    if isinstance(exc, DBAPIError):
        # SQLAlchemy may mark a dropped socket even when the driver message
        # does not match our curated connectivity tokens (#181 / Codex).
        if getattr(exc, "connection_invalidated", False):
            return True
        message = str(getattr(exc, "orig", None) or exc).lower()
        # Prefer specific connectivity phrases over a bare "connection" substring
        # (that can appear in unrelated ODBC/SQL text).
        tokens = (
            "login timeout",
            "communication link failure",
            "connection refused",
            "connection reset",
            "connection timed out",
            "connection is busy",
            "not connected",
            "could not open a connection",
            "server is not found",
            "tcp provider",
            "08001",
            "08s01",
            "hyt00",
            "08s02",
        )
        return any(token in message for token in tokens)
    return False


class RankingsArchiveUnavailableError(RuntimeError):
    """Archive DB is configured but temporarily unreachable (AutoPause / network)."""


class RankingsArchiveQueryError(RuntimeError):
    """Archive query failed due to SQL/app logic (not a reconnectable outage)."""


class RankingsArchiveService:
    def __init__(
        self,
        *,
        provider_registry: RankingProviderRegistry | None = None,
        repository=rankings_archive_repository,
    ) -> None:
        self._provider_registry = provider_registry or ranking_provider_registry
        self._repository = repository
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._lock = Lock()
        # Per-process only: each backend replica tracks its own weekly purge cadence (#188).
        self._last_retention_purge_at: datetime | None = None

    def is_available(self) -> bool:
        return self._repository.is_available()

    def is_configured(self) -> bool:
        probe = getattr(self._repository, "is_configured", None)
        if callable(probe):
            return bool(probe())
        return self.is_available()

    def eod_archive_queries(self) -> list[RankingQuery]:
        """UI-aligned ranking combinations for EOD archive (#167)."""
        queries: list[RankingQuery] = []
        for universe in ("DJIA", "NASDAQ100"):
            for ranking_type in (
                RankingType.volume,
                RankingType.valuation_low_pe,
                RankingType.change_up,
                RankingType.change_down,
            ):
                queries.append(RankingQuery("US", ranking_type, universe=universe))
        for board in ("TWSE", "TPEx"):
            for ranking_type in (
                RankingType.volume,
                RankingType.valuation_low_pe,
                RankingType.change_up,
                RankingType.change_down,
                RankingType.net_buy,
                RankingType.net_sell,
            ):
                queries.append(
                    RankingQuery("TW", ranking_type, tw_board=board, universe=board),
                )
        return queries

    def ready_trading_date(
        self,
        market: Literal["US", "TW"],
        now: datetime | None = None,
        *,
        ranking_type: RankingType | None = None,
    ) -> date | None:
        """Return the trading date eligible for EOD archive at ``now``, else None."""
        current = market_local_time(market, now)
        return self._latest_settled_trading_date(
            market,
            current,
            ranking_type=ranking_type,
        )

    def any_active_capture_window(self, now: datetime | None = None) -> bool:
        """True when a settle is within the same-day retry budget (no DB I/O).

        Historical settled dates outside the retry budget do **not** keep the
        sampler probing Azure — missed days are recovered on the next settle
        via the bounded backfill window (#187).
        """
        current_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        for query in self.eod_archive_queries():
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]
            local_now = current_utc.astimezone(MARKET_TIMEZONES[query_market])
            settled = self._latest_settled_trading_date(
                query_market,
                local_now,
                ranking_type=query.ranking_type,
            )
            if settled is None:
                continue
            ready_at = self._settle_ready_at(
                query_market,
                settled,
                ranking_type=query.ranking_type,
            ).astimezone(timezone.utc)
            if ready_at <= current_utc <= ready_at + _SAME_DAY_RETRY_BUDGET:
                return True
        return False

    def next_sampler_wake_at(self, now: datetime | None = None) -> datetime:
        """Earliest future UTC settle time across EOD archive queries (no DB I/O).

        Used after a sampler cycle (or when no window is open) so the thread can
        sleep until the next useful market event instead of probing Azure every
        ``RANKINGS_ARCHIVE_EOD_POLL_SECONDS`` (#187).
        """
        current_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        soonest: datetime | None = None
        for query in self.eod_archive_queries():
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]
            local_now = current_utc.astimezone(MARKET_TIMEZONES[query_market])
            for day_offset in range(0, 14):
                candidate = local_now.date() + timedelta(days=day_offset)
                probe = datetime.combine(
                    candidate,
                    time(12, 0),
                    tzinfo=MARKET_TIMEZONES[query_market],
                )
                if not is_trading_day(query_market, probe):
                    continue
                ready_at = self._settle_ready_at(
                    query_market,
                    candidate,
                    ranking_type=query.ranking_type,
                ).astimezone(timezone.utc)
                if ready_at <= current_utc:
                    continue
                if soonest is None or ready_at < soonest:
                    soonest = ready_at
                break
        if soonest is None:
            return current_utc + timedelta(hours=12)
        return soonest

    def _settle_ready_at(
        self,
        market: Literal["US", "TW"],
        trading_date: date,
        *,
        ranking_type: RankingType | None,
    ) -> datetime:
        if ranking_type in {RankingType.net_buy, RankingType.net_sell}:
            return datetime.combine(
                trading_date,
                TW_DAILY_PUBLISH_TIME,
                tzinfo=MARKET_TIMEZONES[market],
            )
        close = market_session_close(market, trading_date)
        settle = _US_SETTLE_MINUTES if market == "US" else _TW_QUOTE_SETTLE_MINUTES
        return datetime.combine(
            trading_date,
            close,
            tzinfo=MARKET_TIMEZONES[market],
        ) + timedelta(minutes=settle)

    def _latest_settled_trading_date(
        self,
        market: Literal["US", "TW"],
        current: datetime,
        *,
        ranking_type: RankingType | None,
    ) -> date | None:
        for day_offset in range(0, 10):
            candidate = current.date() - timedelta(days=day_offset)
            probe = datetime.combine(candidate, time(12, 0), tzinfo=MARKET_TIMEZONES[market])
            if not is_trading_day(market, probe):
                continue
            ready_at = self._settle_ready_at(
                market,
                candidate,
                ranking_type=ranking_type,
            )
            if current >= ready_at:
                return candidate
        return None

    def _settled_trading_dates(
        self,
        market: Literal["US", "TW"],
        current: datetime,
        *,
        ranking_type: RankingType | None,
        limit: int = _BACKFILL_TRADING_DAY_WINDOW,
    ) -> list[date]:
        """Newest-first settled trading dates within a bounded calendar walk."""
        local_now = current.astimezone(MARKET_TIMEZONES[market])
        dates: list[date] = []
        for day_offset in range(0, 21):
            candidate = local_now.date() - timedelta(days=day_offset)
            probe = datetime.combine(
                candidate,
                time(12, 0),
                tzinfo=MARKET_TIMEZONES[market],
            )
            if not is_trading_day(market, probe):
                continue
            ready_at = self._settle_ready_at(
                market,
                candidate,
                ranking_type=ranking_type,
            )
            if local_now < ready_at:
                continue
            dates.append(candidate)
            if len(dates) >= limit:
                break
        return dates

    @staticmethod
    def _parse_timestamp_market_date(
        raw: str | None,
        *,
        market: Literal["US", "TW"],
    ) -> date | None:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(MARKET_TIMEZONES[market]).date()

    def _infer_provider_trading_date(
        self,
        response: RankingResponse,
        *,
        market: Literal["US", "TW"],
        ranking_type: RankingType,
    ) -> date | None:
        """Derive the trading session date proved by the provider payload.

        Prefer explicit ``tradingDate``, then a strict majority of item quote
        timestamps (so one stale constituent does not void an otherwise current
        ranking), then the envelope timestamp.
        """
        if response.tradingDate:
            try:
                return date.fromisoformat(response.tradingDate[:10])
            except ValueError:
                logger.warning(
                    "Rankings archive ignored invalid tradingDate on response",
                    extra={
                        "event": "rankings_archive.capture.invalid_trading_date",
                        "trading_date": response.tradingDate,
                        "market": market,
                        "ranking_type": ranking_type.value,
                    },
                )

        item_dates = [
            inferred
            for item in response.items
            if (
                inferred := self._parse_timestamp_market_date(
                    item.timestamp,
                    market=market,
                )
            )
            is not None
        ]
        if item_dates:
            counts: dict[date, int] = {}
            for item_date in item_dates:
                counts[item_date] = counts.get(item_date, 0) + 1
            mode_date, mode_count = max(counts.items(), key=lambda pair: pair[1])
            # Strict majority: tolerate a minority of stale constituent quotes.
            if mode_count * 2 > len(item_dates):
                return mode_date

        # Envelope / institutional report bucket as last resort.
        return self._parse_timestamp_market_date(
            response.timestamp,
            market=market,
        )

    def _archive_exists(
        self,
        *,
        market: str,
        universe: str,
        ranking_type: RankingType,
        trading_date: date,
        state: _CaptureCycleState | None = None,
    ) -> bool:
        ranking_type_value = ranking_type.value
        key = (market, universe, ranking_type_value, trading_date)
        if state is not None and state.existence_cache is not None:
            return key in state.existence_cache
        batch = getattr(self._repository, "list_existing_eod_trading_dates", None)
        if callable(batch):
            if state is not None:
                state.sql_round_trips += 1
            existing = batch(
                market=market,
                universe=universe,
                ranking_type=ranking_type,
                trading_dates=[trading_date],
            )
            return trading_date in existing
        probe = getattr(self._repository, "has_eod_snapshot", None)
        if callable(probe):
            if state is not None:
                state.sql_round_trips += 1
            return bool(
                probe(
                    market=market,
                    universe=universe,
                    ranking_type=ranking_type,
                    trading_date=trading_date,
                )
            )
        if state is not None:
            state.sql_round_trips += 1
        return (
            self._repository.load_eod_snapshot(
                market=market,
                universe=universe,
                ranking_type=ranking_type,
                trading_date=trading_date,
            )
            is not None
        )

    def _prime_existence_cache(
        self,
        *,
        candidate_dates_by_query: list[tuple[RankingQuery, str, list[date]]],
        state: _CaptureCycleState,
    ) -> None:
        """Load existence keys for this capture cycle in as few SQL calls as possible."""
        all_dates = [
            trading_date
            for _query, _universe, dates in candidate_dates_by_query
            for trading_date in dates
        ]
        if not all_dates:
            state.existence_cache = set()
            return
        range_loader = getattr(self._repository, "list_eod_snapshot_keys_in_range", None)
        if callable(range_loader):
            state.sql_round_trips += 1
            state.existence_cache = range_loader(
                min_trading_date=min(all_dates),
                max_trading_date=max(all_dates),
            )
            return
        state.existence_cache = self._existence_cache_via_batches(
            candidate_dates_by_query,
            state=state,
        )

    def _existence_cache_via_batches(
        self,
        candidate_dates_by_query: list[tuple[RankingQuery, str, list[date]]],
        *,
        state: _CaptureCycleState,
    ) -> set[tuple[str, str, str, date]]:
        cache: set[tuple[str, str, str, date]] = set()
        batch = getattr(self._repository, "list_existing_eod_trading_dates", None)
        for query, universe, dates in candidate_dates_by_query:
            if not dates:
                continue
            if callable(batch):
                state.sql_round_trips += 1
                for trading_date in batch(
                    market=query.market,
                    universe=universe,
                    ranking_type=query.ranking_type,
                    trading_dates=dates,
                ):
                    cache.add((query.market, universe, query.ranking_type.value, trading_date))
                continue
            for trading_date in dates:
                if self._archive_exists(
                    market=query.market,
                    universe=universe,
                    ranking_type=query.ranking_type,
                    trading_date=trading_date,
                    state=state,
                ):
                    cache.add((query.market, universe, query.ranking_type.value, trading_date))
        return cache

    def _note_archive_persisted(
        self,
        *,
        market: str,
        universe: str,
        ranking_type: RankingType,
        trading_date: date,
        state: _CaptureCycleState | None = None,
    ) -> None:
        if state is not None and state.existence_cache is not None:
            state.existence_cache.add(
                (market, universe, ranking_type.value, trading_date)
            )

    @staticmethod
    def _capture_log_extra(
        *,
        event: str,
        trigger: str,
        query: RankingQuery,
        universe: str,
        trading_date: date,
        **more: object,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "event": event,
            "trigger": trigger,
            "market": query.market,
            "universe": universe,
            "ranking_type": query.ranking_type.value,
            "trading_date": trading_date.isoformat(),
        }
        payload.update(more)
        return payload

    @staticmethod
    def _tally_outcome(
        outcome: Literal["captured", "skipped", "failed"],
        *,
        captured: int,
        skipped: int,
        failed: int,
    ) -> tuple[int, int, int]:
        if outcome == "captured":
            return captured + 1, skipped, failed
        if outcome == "failed":
            return captured, skipped, failed + 1
        return captured, skipped + 1, failed

    def _resolve_persist_date(
        self,
        *,
        inferred: date | None,
        resolved_date: date,
        allowed_dates: set[date] | None,
    ) -> date | None:
        if allowed_dates is None:
            return resolved_date if inferred == resolved_date else None
        return inferred if inferred in allowed_dates else None

    def _persist_provider_archive(
        self,
        *,
        query: RankingQuery,
        query_market: Literal["US", "TW"],
        universe: str,
        resolved_date: date,
        current: datetime,
        trigger: str,
        allowed_dates: set[date] | None = None,
        state: _CaptureCycleState | None = None,
    ) -> Literal["captured", "skipped", "failed"]:
        """Fetch provider data once and persist under the matching archive date.

        When ``allowed_dates`` is set (auto backfill), the inferred provider date
        may remapped onto any missing candidate in that set so a newer missing
        day that still returns an older session does not burn the fetch budget.
        When ``allowed_dates`` is None, the payload must match ``resolved_date``.
        """
        target_started = perf_counter()
        try:
            provider = self._provider_registry.get_provider(query)
            response = provider.fetch(
                query,
                limit=_MAX_ARCHIVE_LIMIT,
                now=current,
                allow_hardcoded_fallback=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings archive fetch failed",
                extra=self._capture_log_extra(
                    event="rankings_archive.capture.fetch_failed",
                    trigger=trigger,
                    query=query,
                    universe=universe,
                    trading_date=resolved_date,
                ),
            )
            return "failed"

        skip_reason = self._provider_skip_reason(response)
        if skip_reason is not None:
            event, level, message = skip_reason
            extra = self._capture_log_extra(
                event=event,
                trigger=trigger,
                query=query,
                universe=universe,
                trading_date=resolved_date,
                source=response.source,
            )
            if level == "info":
                logger.info(message, extra=extra)
            else:
                logger.warning(message, extra=extra)
            return "skipped"

        inferred = self._infer_provider_trading_date(
            response,
            market=query_market,
            ranking_type=query.ranking_type,
        )
        persist_date = self._resolve_persist_date(
            inferred=inferred,
            resolved_date=resolved_date,
            allowed_dates=allowed_dates,
        )
        if persist_date is None:
            logger.warning(
                "Rankings archive skipped provider date mismatch",
                extra=self._capture_log_extra(
                    event="rankings_archive.capture.date_mismatch",
                    trigger=trigger,
                    query=query,
                    universe=universe,
                    trading_date=resolved_date,
                    provider_trading_date=(
                        inferred.isoformat() if inferred else None
                    ),
                    allowed_dates=sorted(
                        d.isoformat() for d in (allowed_dates or set())
                    ),
                    source=response.source,
                ),
            )
            return "skipped"

        try:
            envelope = self._repository.upsert_eod_snapshot(
                market=query.market,
                universe=universe,
                ranking_type=query.ranking_type,
                trading_date=persist_date,
                response=response,
                captured_at=current,
                capture_scope=CAPTURE_SCOPE_END_OF_DAY,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Rankings archive persist raised",
                extra=self._capture_log_extra(
                    event="rankings_archive.capture.persist_raised",
                    trigger=trigger,
                    query=query,
                    universe=universe,
                    trading_date=persist_date,
                ),
            )
            return "failed"
        if envelope is None:
            logger.error(
                "Rankings archive persist failed",
                extra=self._capture_log_extra(
                    event="rankings_archive.capture.persist_failed",
                    trigger=trigger,
                    query=query,
                    universe=universe,
                    trading_date=persist_date,
                ),
            )
            return "failed"

        logger.info(
            "Rankings archive capture succeeded",
            extra=self._capture_log_extra(
                event="rankings_archive.capture.succeeded",
                trigger=trigger,
                query=query,
                universe=universe,
                trading_date=persist_date,
                requested_trading_date=resolved_date.isoformat(),
                archive_snapshot_id=envelope.snapshot.id,
                item_count=len(envelope.items),
                source=response.source,
                duration_ms=elapsed_ms(target_started),
                backfill=persist_date
                != market_local_time(query_market, current).date(),
            ),
        )
        if state is not None:
            state.sql_round_trips += 1
        self._note_archive_persisted(
            market=query.market,
            universe=universe,
            ranking_type=query.ranking_type,
            trading_date=persist_date,
            state=state,
        )
        return "captured"

    @staticmethod
    def _provider_skip_reason(
        response: RankingResponse,
    ) -> tuple[str, Literal["info", "warning"], str] | None:
        if response.twRankingsScope == "fallback":
            return (
                "rankings_archive.capture.fallback_skipped",
                "warning",
                "Rankings archive skipped hardcoded TW fallback",
            )
        if response.sourceType == RankingSourceType.unsupported:
            return (
                "rankings_archive.capture.unsupported_skipped",
                "info",
                "Rankings archive skipped unsupported provider",
            )
        if not response.items:
            return (
                "rankings_archive.capture.empty",
                "warning",
                (
                    "Rankings archive skipped empty response "
                    "(quote/provider path returned no ranking items)"
                ),
            )
        return None

    def _candidate_trading_dates(
        self,
        *,
        query_market: Literal["US", "TW"],
        ranking_type: RankingType,
        current: datetime,
        trading_date: date | None,
        force: bool,
    ) -> list[date]:
        if trading_date is not None:
            return [trading_date]
        candidate_dates = self._settled_trading_dates(
            query_market,
            current,
            ranking_type=ranking_type,
        )
        if not candidate_dates and force:
            return [market_local_time(query_market, current).date()]
        return candidate_dates

    def _missing_archive_dates(
        self,
        *,
        query: RankingQuery,
        query_market: Literal["US", "TW"],
        universe: str,
        candidate_dates: list[date],
        current: datetime,
        force: bool,
        state: _CaptureCycleState | None = None,
    ) -> tuple[list[date], int]:
        """Return missing eligible dates and how many candidates were skipped."""
        missing_dates: list[date] = []
        skipped = 0
        for resolved_date in candidate_dates:
            probe = datetime.combine(
                resolved_date,
                time(12, 0),
                tzinfo=MARKET_TIMEZONES[query_market],
            )
            if not is_trading_day(query_market, probe):
                skipped += 1
                continue
            if (
                not force
                and current.astimezone(MARKET_TIMEZONES[query_market])
                < self._settle_ready_at(
                    query_market,
                    resolved_date,
                    ranking_type=query.ranking_type,
                )
            ):
                skipped += 1
                continue
            if self._archive_exists(
                market=query.market,
                universe=universe,
                ranking_type=query.ranking_type,
                trading_date=resolved_date,
                state=state,
            ):
                skipped += 1
                continue
            missing_dates.append(resolved_date)
        return missing_dates, skipped

    def _capture_explicit_missing(
        self,
        *,
        query: RankingQuery,
        query_market: Literal["US", "TW"],
        universe: str,
        missing_dates: list[date],
        current: datetime,
        trigger: str,
        state: _CaptureCycleState | None = None,
    ) -> tuple[int, int, int]:
        captured = skipped = failed = 0
        for resolved_date in missing_dates:
            outcome = self._persist_provider_archive(
                query=query,
                query_market=query_market,
                universe=universe,
                resolved_date=resolved_date,
                current=current,
                trigger=trigger,
                allowed_dates=None,
                state=state,
            )
            captured, skipped, failed = self._tally_outcome(
                outcome,
                captured=captured,
                skipped=skipped,
                failed=failed,
            )
        return captured, skipped, failed

    def _capture_auto_backfill(
        self,
        *,
        query: RankingQuery,
        query_market: Literal["US", "TW"],
        universe: str,
        missing_dates: list[date],
        current: datetime,
        trigger: str,
        state: _CaptureCycleState | None = None,
    ) -> tuple[int, int, int]:
        captured = skipped = failed = 0
        remaining = list(missing_dates)
        for _ in range(_BACKFILL_FETCHES_PER_QUERY):
            if not remaining:
                break
            outcome = self._persist_provider_archive(
                query=query,
                query_market=query_market,
                universe=universe,
                resolved_date=remaining[0],
                current=current,
                trigger=trigger,
                allowed_dates=set(remaining),
                state=state,
            )
            captured, skipped, failed = self._tally_outcome(
                outcome,
                captured=captured,
                skipped=skipped,
                failed=failed,
            )
            if outcome == "captured":
                remaining = [
                    d
                    for d in remaining
                    if not self._archive_exists(
                        market=query.market,
                        universe=universe,
                        ranking_type=query.ranking_type,
                        trading_date=d,
                        state=state,
                    )
                ]
                continue
            break
        return captured, skipped, failed

    def _plan_capture_queries(
        self,
        *,
        market: Literal["US", "TW"] | None,
        trading_date: date | None,
        current: datetime,
        force: bool,
    ) -> list[tuple[RankingQuery, str, list[date]]]:
        planned: list[tuple[RankingQuery, str, list[date]]] = []
        for query in self.eod_archive_queries():
            if market is not None and query.market != market:
                continue
            universe = query.universe or query.tw_board or (
                "DJIA" if query.market == "US" else "TWSE"
            )
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]
            candidate_dates = self._candidate_trading_dates(
                query_market=query_market,
                ranking_type=query.ranking_type,
                current=current,
                trading_date=trading_date,
                force=force,
            )
            planned.append((query, universe, candidate_dates))
        return planned

    def _still_missing_after_cycle(
        self,
        *,
        planned: list[tuple[RankingQuery, str, list[date]]],
        current: datetime,
        force: bool,
        state: _CaptureCycleState,
    ) -> int:
        for query, universe, candidate_dates in planned:
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]
            remaining, _ = self._missing_archive_dates(
                query=query,
                query_market=query_market,
                universe=universe,
                candidate_dates=candidate_dates,
                current=current,
                force=force,
                state=state,
            )
            if remaining:
                return 1
        return 0

    def capture_eod_archives(
        self,
        now: datetime | None = None,
        *,
        trigger: str = "manual",
        market: Literal["US", "TW"] | None = None,
        trading_date: date | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        """Capture EOD archives. Failures are logged and never raised to callers.

        Missed prior trading days may be backfilled only when the provider payload
        proves it belongs to that target date (#178). ``force`` skips settle-time
        gating but never bypasses date validation. Auto cycles fetch once per
        query and remap the inferred provider date onto any missing candidate
        in the settled window.

        Return keys include ``had_missing`` / ``still_missing`` (0/1) and
        ``sql_round_trips`` for sampler scheduling / observability (#187).
        """
        started_at = perf_counter()
        state = _CaptureCycleState()
        state.sql_round_trips += 1
        if not self.is_available():
            logger.info(
                "Rankings archive capture skipped; archive DB unavailable",
                extra={
                    "event": "rankings_archive.capture.skipped",
                    "trigger": trigger,
                    "reason": "archive_unavailable",
                    "sql_round_trips": state.sql_round_trips,
                },
            )
            # Mark failed so the sampler retries inside the same-day window (#187).
            return _empty_capture_result(sql_round_trips=state.sql_round_trips, failed=1)

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        state.sql_round_trips += 1
        if not self._repository.ensure_schema():
            logger.error(
                "Rankings archive capture aborted; schema ensure failed",
                extra={
                    "event": "rankings_archive.capture.schema_failed",
                    "trigger": trigger,
                    "sql_round_trips": state.sql_round_trips,
                },
            )
            return _empty_capture_result(sql_round_trips=state.sql_round_trips, failed=1)

        planned = self._plan_capture_queries(
            market=market,
            trading_date=trading_date,
            current=current,
            force=force,
        )
        self._prime_existence_cache(candidate_dates_by_query=planned, state=state)

        captured = skipped = failed = had_missing = 0
        for query, universe, candidate_dates in planned:
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]
            missing_dates, skipped_here = self._missing_archive_dates(
                query=query,
                query_market=query_market,
                universe=universe,
                candidate_dates=candidate_dates,
                current=current,
                force=force,
                state=state,
            )
            skipped += skipped_here
            if not missing_dates:
                continue
            had_missing = 1
            if trading_date is not None:
                c, s, f = self._capture_explicit_missing(
                    query=query,
                    query_market=query_market,
                    universe=universe,
                    missing_dates=missing_dates,
                    current=current,
                    trigger=trigger,
                    state=state,
                )
            else:
                c, s, f = self._capture_auto_backfill(
                    query=query,
                    query_market=query_market,
                    universe=universe,
                    missing_dates=missing_dates,
                    current=current,
                    trigger=trigger,
                    state=state,
                )
            captured += c
            skipped += s
            failed += f

        still_missing = self._still_missing_after_cycle(
            planned=planned,
            current=current,
            force=force,
            state=state,
        )
        logger.info(
            "Rankings archive capture cycle completed",
            extra={
                "event": "rankings_archive.capture.completed",
                "trigger": trigger,
                "captured": captured,
                "skipped": skipped,
                "failed": failed,
                "had_missing": bool(had_missing),
                "still_missing": bool(still_missing),
                "sql_round_trips": state.sql_round_trips,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        # DB is already awake from this capture cycle — opportunistic retention (#188).
        self.maybe_purge_expired_archives(now=current, already_awake=True)
        return {
            "captured": captured,
            "skipped": skipped,
            "failed": failed,
            "had_missing": had_missing,
            "still_missing": still_missing,
            "sql_round_trips": state.sql_round_trips,
        }

    @staticmethod
    def retention_cutoff_date(
        *,
        retention_days: int,
        as_of: date | None = None,
    ) -> date | None:
        """UTC calendar cutoff: keep ``trading_date >= as_of - retention_days``.

        Returns ``None`` when retention is disabled (``retention_days == 0``).
        """
        if retention_days <= 0:
            return None
        day = as_of or datetime.now(timezone.utc).date()
        return day - timedelta(days=retention_days)

    def preview_retention_purge(
        self,
        now: datetime | None = None,
    ) -> dict[str, int | str] | None:
        """Read-only count of rows that would be purged (does not delete) (#188).

        Opens the archive DB like a normal read — use from an operator shell only,
        not from request handlers or scheduled jobs.
        """
        retention_days = int(settings.rankings_archive_retention_days)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = self.retention_cutoff_date(
            retention_days=retention_days,
            as_of=current.date(),
        )
        if cutoff is None:
            return None
        if not self.is_available():
            logger.debug(
                "Rankings archive retention preview skipped; archive unavailable",
                extra={"event": "rankings_archive.retention.preview_unavailable"},
            )
            return None
        preview = self._repository.count_snapshots_older_than(
            cutoff_trading_date=cutoff,
        )
        preview["retention_days"] = retention_days
        return preview

    def maybe_purge_expired_archives(
        self,
        now: datetime | None = None,
        *,
        already_awake: bool = False,
        force: bool = False,
    ) -> dict[str, int | str] | None:
        """Purge expired archives only when Azure SQL is already legitimately awake.

        ``already_awake=True`` means the caller just touched the archive DB
        (e.g. end of ``capture_eod_archives``). Without that flag this method
        refuses to open a connection — retention must not defeat AutoPause (#187/#188).
        """
        if not already_awake:
            logger.debug(
                "Rankings archive retention skipped; refusing standalone wake",
                extra={
                    "event": "rankings_archive.retention.skipped",
                    "reason": "not_already_awake",
                },
            )
            return None

        retention_days = int(settings.rankings_archive_retention_days)
        if retention_days <= 0:
            logger.debug(
                "Rankings archive retention disabled",
                extra={
                    "event": "rankings_archive.retention.skipped",
                    "reason": "disabled",
                    "retention_days": retention_days,
                },
            )
            return None

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        previous_last: datetime | None
        with self._lock:
            last = self._last_retention_purge_at
            if (
                not force
                and last is not None
                and current - last < _RETENTION_PURGE_MIN_INTERVAL
            ):
                logger.debug(
                    "Rankings archive retention skipped; within min interval",
                    extra={
                        "event": "rankings_archive.retention.skipped",
                        "reason": "min_interval",
                        "last_purge_at": last.isoformat(),
                        "retention_days": retention_days,
                    },
                )
                return None
            # Claim the weekly slot before purge so concurrent capture threads cannot
            # all pass the interval check and run overlapping delete batches (#188).
            previous_last = self._last_retention_purge_at
            self._last_retention_purge_at = current

        cutoff = self.retention_cutoff_date(
            retention_days=retention_days,
            as_of=current.date(),
        )
        assert cutoff is not None
        if not self.is_available():
            with self._lock:
                self._last_retention_purge_at = previous_last
            return None

        purge_fn = getattr(self._repository, "purge_snapshots_older_than", None)
        if not callable(purge_fn):
            with self._lock:
                self._last_retention_purge_at = previous_last
            return None

        started_at = perf_counter()
        try:
            result = purge_fn(cutoff_trading_date=cutoff)
        except Exception:  # noqa: BLE001
            with self._lock:
                self._last_retention_purge_at = previous_last
            logger.exception(
                "Rankings archive retention purge failed",
                extra={
                    "event": "rankings_archive.retention.failed",
                    "retention_days": retention_days,
                    "cutoff_date": cutoff.isoformat(),
                },
            )
            return None

        result = {
            **result,
            "retention_days": retention_days,
        }
        logger.info(
            "Rankings archive retention purge completed",
            extra={
                "event": "rankings_archive.retention.completed",
                "retention_days": retention_days,
                "cutoff_date": result.get("cutoff_date"),
                "snapshots_deleted": result.get("snapshots_deleted"),
                "items_deleted": result.get("items_deleted"),
                "purge_duration_ms": elapsed_ms(started_at),
                "dry_run": bool(result.get("dry_run")),
            },
        )
        return result

    def list_history_dates(
        self,
        *,
        market: Literal["US", "TW"],
        universe: str,
        ranking_type: RankingType,
        limit: int = 5,
    ) -> list[date]:
        """Return newest archived trading dates for market/universe/type (UI discovery)."""
        if not self.is_available():
            return []
        try:
            return self._repository.list_available_trading_dates(
                market=market,
                universe=universe,
                ranking_type=ranking_type,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                _HISTORY_DATES_FAILED,
                extra={
                    "event": "rankings_archive.history.dates_failed",
                    "market": market,
                    "universe": universe,
                    "ranking_type": ranking_type.value,
                },
            )
            # Mid-life AutoPause leaves a dead cached engine; drop it so the next
            # request re-enters the cold-start wake budget (#177). Do not invalidate
            # on SQL syntax/logic errors (those are app bugs, not a paused server).
            if _is_archive_connectivity_failure(exc):
                invalidate_rankings_archive_engine(reason="history_dates_failed")
                raise RankingsArchiveUnavailableError(_HISTORY_DATES_FAILED) from exc
            raise RankingsArchiveQueryError(_HISTORY_DATES_FAILED) from exc

    def get_history_rankings(
        self,
        *,
        market: Literal["US", "TW"],
        universe: str,
        ranking_type: RankingType,
        trading_date: date,
        limit: int = 50,
    ) -> RankingResponse | None:
        """Load one EOD archive snapshot. Returns None when missing; raises on outage."""
        if not self.is_available():
            return None
        try:
            envelope = self._repository.load_eod_snapshot(
                market=market,
                universe=universe,
                ranking_type=ranking_type,
                trading_date=trading_date,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                _HISTORY_LOAD_FAILED,
                extra={
                    "event": "rankings_archive.history.load_failed",
                    "market": market,
                    "universe": universe,
                    "ranking_type": ranking_type.value,
                    "trading_date": trading_date.isoformat(),
                },
            )
            if _is_archive_connectivity_failure(exc):
                invalidate_rankings_archive_engine(reason="history_load_failed")
                raise RankingsArchiveUnavailableError(_HISTORY_LOAD_FAILED) from exc
            raise RankingsArchiveQueryError(_HISTORY_LOAD_FAILED) from exc
        if envelope is None:
            return None
        response = self._repository.to_ranking_response(envelope)
        if limit < len(response.items):
            return response.model_copy(update={"items": response.items[:limit]})
        return response

    def start_background_sampler(self, poll_seconds: int) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run_sampler,
                args=(poll_seconds,),
                name="rankings-archive-eod",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Rankings archive EOD sampler started",
                extra={
                    "event": "rankings_archive.sampler.started",
                    "poll_seconds": poll_seconds,
                },
            )

    def stop_background_sampler(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        logger.info(
            "Rankings archive EOD sampler stopped",
            extra={"event": "rankings_archive.sampler.stopped"},
        )

    def _active_capture_window_end(self, now: datetime) -> datetime | None:
        """Latest UTC end of any currently open same-day retry budget."""
        current_utc = now.astimezone(timezone.utc)
        latest: datetime | None = None
        for query in self.eod_archive_queries():
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]
            local_now = current_utc.astimezone(MARKET_TIMEZONES[query_market])
            settled = self._latest_settled_trading_date(
                query_market,
                local_now,
                ranking_type=query.ranking_type,
            )
            if settled is None:
                continue
            ready_at = self._settle_ready_at(
                query_market,
                settled,
                ranking_type=query.ranking_type,
            ).astimezone(timezone.utc)
            window_end = ready_at + _SAME_DAY_RETRY_BUDGET
            if ready_at <= current_utc <= window_end:
                if latest is None or window_end > latest:
                    latest = window_end
        return latest

    def _sampler_next_wake_after_capture(
        self,
        *,
        now: datetime,
        poll_seconds: int,
        result: dict[str, int],
    ) -> tuple[datetime, str]:
        still_missing = bool(result.get("still_missing"))
        failed = int(result.get("failed") or 0)
        current = datetime.now(timezone.utc)
        window_end = self._active_capture_window_end(current)
        if (still_missing or failed > 0) and window_end is not None and current < window_end:
            retry_at = current + timedelta(seconds=poll_seconds)
            if retry_at > window_end:
                retry_at = window_end
            return retry_at, "same_day_retry"
        next_wake = self.next_sampler_wake_at(now)
        if next_wake <= now:
            next_wake = now + timedelta(seconds=poll_seconds)
        return next_wake, "next_settle"

    def _run_sampler(self, poll_seconds: int) -> None:
        """Market-aware EOD sampler: sleep until settle; touch Azure only when due (#187).

        ``poll_seconds`` is the interrupt granularity for stop/disable checks while
        idle — and the same-day retry spacing inside ``_SAME_DAY_RETRY_BUDGET``.
        It is not a fixed Azure wake interval outside an active capture window.
        """
        next_wake: datetime | None = None
        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            if next_wake is not None and now < next_wake:
                remaining = (next_wake - now).total_seconds()
                self._stop_event.wait(min(float(poll_seconds), max(1.0, remaining)))
                continue
            from app.jobs.health import note_job_failure, note_job_success

            try:
                if not settings.rankings_archive_eod_enabled:
                    next_wake = now + timedelta(seconds=poll_seconds)
                    continue
                if not self.is_configured():
                    logger.info(
                        "Rankings archive sampler idle; archive URL not configured",
                        extra={
                            "event": "rankings_archive.sampler.idle",
                            "reason": "not_configured",
                            "azure_sql_touched": False,
                        },
                    )
                    next_wake = now + timedelta(seconds=poll_seconds)
                    continue
                if not self.any_active_capture_window(now):
                    next_wake = self.next_sampler_wake_at(now)
                    if next_wake <= now:
                        next_wake = now + timedelta(seconds=poll_seconds)
                    logger.info(
                        "Rankings archive sampler idle until next settle",
                        extra={
                            "event": "rankings_archive.sampler.idle",
                            "reason": "outside_capture_window",
                            "next_wake_at": next_wake.isoformat(),
                            "azure_sql_touched": False,
                        },
                    )
                    continue
                from app.services.job_leader_lock import (
                    release_job_leadership,
                    try_acquire_job_leadership,
                )

                leader_token = try_acquire_job_leadership(
                    "rankings-archive-eod",
                    ttl_seconds=max(120, int(poll_seconds)),
                )
                if leader_token is None:
                    next_wake = now + timedelta(seconds=poll_seconds)
                    continue
                try:
                    result = self.capture_eod_archives(trigger="sampler")
                except Exception:
                    release_job_leadership("rankings-archive-eod", leader_token)
                    raise
                failed = int(result.get("failed") or 0)
                still_missing = int(result.get("still_missing") or 0)
                if failed or still_missing:
                    release_job_leadership("rankings-archive-eod", leader_token)
                next_wake, reason = self._sampler_next_wake_after_capture(
                    now=now,
                    poll_seconds=poll_seconds,
                    result=result,
                )
                logger.info(
                    "Rankings archive sampler sleeping after capture",
                    extra={
                        "event": "rankings_archive.sampler.next_wake",
                        "reason": reason,
                        "next_wake_at": next_wake.isoformat(),
                        "azure_sql_touched": True,
                        "captured": result.get("captured"),
                        "skipped": result.get("skipped"),
                        "failed": result.get("failed"),
                        "had_missing": bool(result.get("had_missing")),
                        "still_missing": bool(result.get("still_missing")),
                        "sql_round_trips": result.get("sql_round_trips"),
                    },
                )
                failed = int(result.get("failed") or 0)
                still_missing = int(result.get("still_missing") or 0)
                if failed == 0 and still_missing == 0:
                    note_job_success("rankings-archive-eod")
            except Exception as exc:  # noqa: BLE001
                note_job_failure("rankings-archive-eod", exc)
                logger.exception(
                    "Rankings archive sampler cycle failed",
                    extra={"event": "rankings_archive.sampler.failed"},
                )
                next_wake = now + timedelta(seconds=poll_seconds)


rankings_archive_service = RankingsArchiveService()
