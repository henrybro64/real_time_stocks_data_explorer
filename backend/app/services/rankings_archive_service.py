"""End-of-day ranking archive capture (Azure SQL), isolated from GET /api/rankings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Literal

from app.core.config import settings
from app.core.diagnostics import elapsed_ms
from app.db.archive_session import (
    invalidate_rankings_archive_engine,
    reset_rankings_archive_session_state,
)
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


class RankingsArchiveUnavailableError(RuntimeError):
    """Archive DB is configured but a history query failed (operational outage)."""


@dataclass(frozen=True)
class ArchiveCaptureTarget:
    market: Literal["US", "TW"]
    universe: str
    ranking_type: RankingType
    trading_date: date


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

    def _provider_date_matches_target(
        self,
        response: RankingResponse,
        *,
        market: Literal["US", "TW"],
        ranking_type: RankingType,
        target_date: date,
    ) -> tuple[bool, date | None]:
        inferred = self._infer_provider_trading_date(
            response,
            market=market,
            ranking_type=ranking_type,
        )
        return inferred == target_date, inferred

    def _archive_exists(
        self,
        *,
        market: str,
        universe: str,
        ranking_type: RankingType,
        trading_date: date,
    ) -> bool:
        probe = getattr(self._repository, "has_eod_snapshot", None)
        if callable(probe):
            return bool(
                probe(
                    market=market,
                    universe=universe,
                    ranking_type=ranking_type,
                    trading_date=trading_date,
                )
            )
        return (
            self._repository.load_eod_snapshot(
                market=market,
                universe=universe,
                ranking_type=ranking_type,
                trading_date=trading_date,
            )
            is not None
        )

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
                extra={
                    "event": "rankings_archive.capture.fetch_failed",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": resolved_date.isoformat(),
                },
            )
            return "failed"

        if response.twRankingsScope == "fallback":
            logger.warning(
                "Rankings archive skipped hardcoded TW fallback",
                extra={
                    "event": "rankings_archive.capture.fallback_skipped",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": resolved_date.isoformat(),
                    "source": response.source,
                },
            )
            return "skipped"
        if response.sourceType == RankingSourceType.unsupported:
            logger.info(
                "Rankings archive skipped unsupported provider",
                extra={
                    "event": "rankings_archive.capture.unsupported_skipped",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": resolved_date.isoformat(),
                },
            )
            return "skipped"
        if not response.items:
            logger.warning(
                "Rankings archive skipped empty response",
                extra={
                    "event": "rankings_archive.capture.empty",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": resolved_date.isoformat(),
                    "source": response.source,
                },
            )
            return "skipped"

        inferred = self._infer_provider_trading_date(
            response,
            market=query_market,
            ranking_type=query.ranking_type,
        )
        if allowed_dates is None:
            persist_date = resolved_date if inferred == resolved_date else None
        else:
            persist_date = inferred if inferred in allowed_dates else None

        if persist_date is None:
            logger.warning(
                "Rankings archive skipped provider date mismatch",
                extra={
                    "event": "rankings_archive.capture.date_mismatch",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": resolved_date.isoformat(),
                    "provider_trading_date": (
                        inferred.isoformat() if inferred else None
                    ),
                    "allowed_dates": sorted(d.isoformat() for d in (allowed_dates or set())),
                    "source": response.source,
                },
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
                extra={
                    "event": "rankings_archive.capture.persist_raised",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": persist_date.isoformat(),
                },
            )
            return "failed"
        if envelope is None:
            logger.error(
                "Rankings archive persist failed",
                extra={
                    "event": "rankings_archive.capture.persist_failed",
                    "trigger": trigger,
                    "market": query.market,
                    "universe": universe,
                    "ranking_type": query.ranking_type.value,
                    "trading_date": persist_date.isoformat(),
                },
            )
            return "failed"

        logger.info(
            "Rankings archive capture succeeded",
            extra={
                "event": "rankings_archive.capture.succeeded",
                "trigger": trigger,
                "market": query.market,
                "universe": universe,
                "ranking_type": query.ranking_type.value,
                "trading_date": persist_date.isoformat(),
                "requested_trading_date": resolved_date.isoformat(),
                "archive_snapshot_id": envelope.snapshot.id,
                "item_count": len(envelope.items),
                "source": response.source,
                "duration_ms": elapsed_ms(target_started),
                "backfill": persist_date
                != market_local_time(query_market, current).date(),
            },
        )
        return "captured"

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
        """
        started_at = perf_counter()
        captured = 0
        skipped = 0
        failed = 0
        if not self.is_available():
            logger.info(
                "Rankings archive capture skipped; archive DB unavailable",
                extra={
                    "event": "rankings_archive.capture.skipped",
                    "trigger": trigger,
                    "reason": "archive_unavailable",
                },
            )
            return {"captured": 0, "skipped": 0, "failed": 0}

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self._repository.ensure_schema():
            logger.error(
                "Rankings archive capture aborted; schema ensure failed",
                extra={
                    "event": "rankings_archive.capture.schema_failed",
                    "trigger": trigger,
                },
            )
            return {"captured": 0, "skipped": 0, "failed": 0}

        for query in self.eod_archive_queries():
            if market is not None and query.market != market:
                continue
            universe = query.universe or query.tw_board or (
                "DJIA" if query.market == "US" else "TWSE"
            )
            query_market: Literal["US", "TW"] = query.market  # type: ignore[assignment]

            if trading_date is not None:
                candidate_dates = [trading_date]
            else:
                candidate_dates = self._settled_trading_dates(
                    query_market,
                    current,
                    ranking_type=query.ranking_type,
                )
                if not candidate_dates and force:
                    fallback = market_local_time(query_market, current).date()
                    candidate_dates = [fallback]

            missing_dates: list[date] = []
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
                ):
                    skipped += 1
                    continue
                missing_dates.append(resolved_date)

            if not missing_dates:
                continue

            if trading_date is not None:
                # Explicit date: require an exact match; do not remap.
                for resolved_date in missing_dates:
                    outcome = self._persist_provider_archive(
                        query=query,
                        query_market=query_market,
                        universe=universe,
                        resolved_date=resolved_date,
                        current=current,
                        trigger=trigger,
                        allowed_dates=None,
                    )
                    if outcome == "captured":
                        captured += 1
                    elif outcome == "failed":
                        failed += 1
                    else:
                        skipped += 1
                continue

            # Auto path: one provider fetch, remap onto any missing candidate.
            for _ in range(_BACKFILL_FETCHES_PER_QUERY):
                if not missing_dates:
                    break
                newest_missing = missing_dates[0]
                outcome = self._persist_provider_archive(
                    query=query,
                    query_market=query_market,
                    universe=universe,
                    resolved_date=newest_missing,
                    current=current,
                    trigger=trigger,
                    allowed_dates=set(missing_dates),
                )
                if outcome == "captured":
                    captured += 1
                    # Drop whatever date was filled; re-probe remaining holes next cycle.
                    missing_dates = [
                        d
                        for d in missing_dates
                        if not self._archive_exists(
                            market=query.market,
                            universe=universe,
                            ranking_type=query.ranking_type,
                            trading_date=d,
                        )
                    ]
                elif outcome == "failed":
                    failed += 1
                    break
                else:
                    skipped += 1
                    break

        logger.info(
            "Rankings archive capture cycle completed",
            extra={
                "event": "rankings_archive.capture.completed",
                "trigger": trigger,
                "captured": captured,
                "skipped": skipped,
                "failed": failed,
                "duration_ms": elapsed_ms(started_at),
            },
        )
        return {"captured": captured, "skipped": skipped, "failed": failed}

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
                "Rankings archive history dates lookup failed",
                extra={
                    "event": "rankings_archive.history.dates_failed",
                    "market": market,
                    "universe": universe,
                    "ranking_type": ranking_type.value,
                },
            )
            # Mid-life AutoPause leaves a dead cached engine; drop it so the next
            # request re-enters the cold-start wake budget (#177).
            invalidate_rankings_archive_engine(reason="history_dates_failed")
            raise RankingsArchiveUnavailableError(
                "Rankings archive history dates lookup failed",
            ) from exc

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
                "Rankings archive history load failed",
                extra={
                    "event": "rankings_archive.history.load_failed",
                    "market": market,
                    "universe": universe,
                    "ranking_type": ranking_type.value,
                    "trading_date": trading_date.isoformat(),
                },
            )
            invalidate_rankings_archive_engine(reason="history_load_failed")
            raise RankingsArchiveUnavailableError(
                "Rankings archive history load failed",
            ) from exc
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

    def _run_sampler(self, poll_seconds: int) -> None:
        while not self._stop_event.is_set():
            try:
                if settings.rankings_archive_eod_enabled and self.is_available():
                    self.capture_eod_archives(trigger="sampler")
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Rankings archive sampler cycle failed",
                    extra={"event": "rankings_archive.sampler.failed"},
                )
            self._stop_event.wait(poll_seconds)


rankings_archive_service = RankingsArchiveService()


def reset_rankings_archive_for_tests() -> None:
    reset_rankings_archive_session_state()
