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
        """Derive the trading session date proved by the provider payload."""
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

        item_dates = {
            inferred
            for item in response.items
            if (
                inferred := self._parse_timestamp_market_date(
                    item.timestamp,
                    market=market,
                )
            )
            is not None
        }
        if len(item_dates) == 1:
            return next(iter(item_dates))
        if len(item_dates) > 1:
            return None

        # Institutional responses stamp every item (and often the envelope) with
        # the report day's 18:00 Taipei bucket even when enrichment quotes differ.
        if ranking_type in {RankingType.net_buy, RankingType.net_sell}:
            return self._parse_timestamp_market_date(
                response.timestamp,
                market=market,
            )
        return None

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
        gating but never bypasses date validation.
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

            fetches_remaining = (
                len(candidate_dates)
                if trading_date is not None
                else _BACKFILL_FETCHES_PER_QUERY
            )

            for resolved_date in candidate_dates:
                if fetches_remaining <= 0:
                    break

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

                existing = self._repository.load_eod_snapshot(
                    market=query.market,
                    universe=universe,
                    ranking_type=query.ranking_type,
                    trading_date=resolved_date,
                )
                if existing is not None:
                    skipped += 1
                    continue

                fetches_remaining -= 1
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
                    failed += 1
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
                    continue

                if response.twRankingsScope == "fallback":
                    skipped += 1
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
                    continue
                if response.sourceType == RankingSourceType.unsupported:
                    skipped += 1
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
                    continue
                if not response.items:
                    skipped += 1
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
                    continue

                matches, inferred_date = self._provider_date_matches_target(
                    response,
                    market=query_market,
                    ranking_type=query.ranking_type,
                    target_date=resolved_date,
                )
                if not matches:
                    skipped += 1
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
                                inferred_date.isoformat() if inferred_date else None
                            ),
                            "source": response.source,
                        },
                    )
                    continue

                try:
                    envelope = self._repository.upsert_eod_snapshot(
                        market=query.market,
                        universe=universe,
                        ranking_type=query.ranking_type,
                        trading_date=resolved_date,
                        response=response,
                        captured_at=current,
                        capture_scope=CAPTURE_SCOPE_END_OF_DAY,
                    )
                except Exception:  # noqa: BLE001
                    failed += 1
                    logger.exception(
                        "Rankings archive persist raised",
                        extra={
                            "event": "rankings_archive.capture.persist_raised",
                            "trigger": trigger,
                            "market": query.market,
                            "universe": universe,
                            "ranking_type": query.ranking_type.value,
                            "trading_date": resolved_date.isoformat(),
                        },
                    )
                    continue
                if envelope is None:
                    failed += 1
                    logger.error(
                        "Rankings archive persist failed",
                        extra={
                            "event": "rankings_archive.capture.persist_failed",
                            "trigger": trigger,
                            "market": query.market,
                            "universe": universe,
                            "ranking_type": query.ranking_type.value,
                            "trading_date": resolved_date.isoformat(),
                        },
                    )
                    continue

                captured += 1
                logger.info(
                    "Rankings archive capture succeeded",
                    extra={
                        "event": "rankings_archive.capture.succeeded",
                        "trigger": trigger,
                        "market": query.market,
                        "universe": universe,
                        "ranking_type": query.ranking_type.value,
                        "trading_date": resolved_date.isoformat(),
                        "archive_snapshot_id": envelope.snapshot.id,
                        "item_count": len(envelope.items),
                        "source": response.source,
                        "duration_ms": elapsed_ms(target_started),
                        "backfill": resolved_date
                        != market_local_time(query_market, current).date(),
                    },
                )

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
