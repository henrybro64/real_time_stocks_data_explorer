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
from app.db.archive_session import reset_rankings_archive_session_state
from app.repositories.rankings_archive_repository import (
    CAPTURE_SCOPE_END_OF_DAY,
    rankings_archive_repository,
)
from app.schemas.rankings import RankingSourceType, RankingType
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

    def capture_eod_archives(
        self,
        now: datetime | None = None,
        *,
        trigger: str = "manual",
        market: Literal["US", "TW"] | None = None,
        trading_date: date | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        """Capture EOD archives. Failures are logged and never raised to callers."""
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
            resolved_date = trading_date
            if resolved_date is None:
                if force:
                    resolved_date = self.ready_trading_date(
                        query.market,  # type: ignore[arg-type]
                        current,
                        ranking_type=query.ranking_type,
                    ) or market_local_time(query.market, current).date()
                else:
                    resolved_date = self.ready_trading_date(
                        query.market,  # type: ignore[arg-type]
                        current,
                        ranking_type=query.ranking_type,
                    )
            if resolved_date is None:
                skipped += 1
                continue
            local_today = market_local_time(query.market, current).date()
            # Live quote providers reflect the current session only. Never stamp
            # today's intraday/EOD quotes under a prior trading-date archive key.
            if resolved_date != local_today:
                skipped += 1
                logger.warning(
                    "Rankings archive skipped prior trading date without historical provider",
                    extra={
                        "event": "rankings_archive.capture.prior_date_skipped",
                        "trigger": trigger,
                        "market": query.market,
                        "universe": universe,
                        "ranking_type": query.ranking_type.value,
                        "trading_date": resolved_date.isoformat(),
                        "local_today": local_today.isoformat(),
                    },
                )
                continue
            probe = datetime.combine(
                resolved_date,
                time(12, 0),
                tzinfo=MARKET_TIMEZONES[query.market],
            )
            if not is_trading_day(query.market, probe):
                skipped += 1
                continue
            if (
                not force
                and current.astimezone(MARKET_TIMEZONES[query.market])
                < self._settle_ready_at(
                    query.market,  # type: ignore[arg-type]
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
