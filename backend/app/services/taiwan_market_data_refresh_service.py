from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from app.core.diagnostics import elapsed_ms
from app.repositories.taiwan_institutional_daily_repository import (
    taiwan_institutional_daily_repository,
)
from app.repositories.taiwan_margin_short_daily_repository import (
    taiwan_margin_short_daily_repository,
)
from app.services.http_client import HttpFetchError
from app.services.taiwan_history_scan import (
    candidate_start_date,
    iter_candidate_weekdays,
)
from app.services.taiwan_market_data import is_taiwan_no_data_payload
from app.services.tw_institutional_service import (
    fetch_institutional_market_payload,
    institutional_source_for_market,
    parse_all_institutional_records,
)
from app.services.tw_margin_short_service import (
    _fetch_tpex_borrowed_short_payload,
    _fetch_tpex_margin_payload,
    _fetch_twse_borrowed_short_payload,
    _fetch_twse_close_payload,
    _fetch_twse_margin_payload,
    margin_short_source_for_market,
    parse_all_margin_short_records,
)

logger = logging.getLogger(__name__)

TWBoard = Literal["TWSE", "TPEx"]
DatasetKind = Literal["institutional", "margin_short"]
_TW_TIMEZONE = ZoneInfo("Asia/Taipei")
_DEFAULT_RECENT_DAYS = 7
_MARKETS: tuple[TWBoard, ...] = ("TWSE", "TPEx")

PayloadFetcher = Callable[[date], object]
NowProvider = Callable[[], datetime]


@dataclass(frozen=True)
class DatasetDayResult:
    dataset: DatasetKind
    market: TWBoard
    requested_date: str
    resolved_date: str | None
    rows_fetched: int
    rows_inserted: int
    rows_updated: int
    skipped_no_data: bool
    error: str | None = None


@dataclass(frozen=True)
class TaiwanMarketDataRefreshSummary:
    trigger: str
    days: int
    started_at: str
    finished_at: str
    results: tuple[DatasetDayResult, ...]

    @property
    def rows_upserted(self) -> int:
        return sum(item.rows_inserted + item.rows_updated for item in self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.results if item.error)


class TaiwanMarketDataRefreshService:
    def __init__(
        self,
        *,
        institutional_repository=taiwan_institutional_daily_repository,
        margin_short_repository=taiwan_margin_short_daily_repository,
        now_provider: NowProvider | None = None,
        twse_institutional_fetcher: PayloadFetcher | None = None,
        tpex_institutional_fetcher: PayloadFetcher | None = None,
        twse_margin_fetcher: PayloadFetcher | None = None,
        twse_borrowed_short_fetcher: PayloadFetcher | None = None,
        twse_close_fetcher: PayloadFetcher | None = None,
        tpex_margin_fetcher: PayloadFetcher | None = None,
        tpex_borrowed_short_fetcher: PayloadFetcher | None = None,
    ) -> None:
        self._institutional_repository = institutional_repository
        self._margin_short_repository = margin_short_repository
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._twse_institutional_fetcher = (
            twse_institutional_fetcher
            or (lambda current_date: fetch_institutional_market_payload("TWSE", current_date))
        )
        self._tpex_institutional_fetcher = (
            tpex_institutional_fetcher
            or (lambda current_date: fetch_institutional_market_payload("TPEx", current_date))
        )
        self._twse_margin_fetcher = twse_margin_fetcher or _fetch_twse_margin_payload
        self._twse_borrowed_short_fetcher = (
            twse_borrowed_short_fetcher or _fetch_twse_borrowed_short_payload
        )
        self._twse_close_fetcher = twse_close_fetcher or _fetch_twse_close_payload
        self._tpex_margin_fetcher = tpex_margin_fetcher or _fetch_tpex_margin_payload
        self._tpex_borrowed_short_fetcher = (
            tpex_borrowed_short_fetcher or _fetch_tpex_borrowed_short_payload
        )
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start_background_sampler(
        self,
        poll_seconds: int,
        *,
        refresh_hour: int,
        recent_days: int,
    ) -> None:
        if (
            not self._institutional_repository.is_available()
            or not self._margin_short_repository.is_available()
        ):
            logger.info(
                "Taiwan market data refresh sampler skipped because the database is unavailable",
                extra={
                    "event": "taiwan_market_data.sampler.skipped",
                    "reason": "database_unavailable",
                },
            )
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run_sampler,
                args=(poll_seconds, refresh_hour, recent_days),
                name="taiwan-market-data-refresh",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Taiwan market data refresh sampler started",
                extra={
                    "event": "taiwan_market_data.sampler.started",
                    "poll_seconds": poll_seconds,
                    "refresh_hour": refresh_hour,
                    "recent_days": recent_days,
                },
            )

    def stop_background_sampler(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None

    def refresh(
        self,
        *,
        days: int,
        trigger: str,
        include_institutional: bool = True,
        include_margin_short: bool = True,
    ) -> TaiwanMarketDataRefreshSummary:
        started = self._now_provider()
        started_at_perf = perf_counter()
        requested_days = max(days, 1)
        start_date = candidate_start_date(started)
        window_days = max(requested_days * 2, requested_days + 14)
        results: list[DatasetDayResult] = []

        logger.info(
            "Taiwan market data refresh started",
            extra={
                "event": "taiwan_market_data.refresh.started",
                "trigger": trigger,
                "days": requested_days,
                "start_date": start_date.isoformat(),
                "include_institutional": include_institutional,
                "include_margin_short": include_margin_short,
            },
        )

        for current_date in iter_candidate_weekdays(start_date, window_days):
            if include_institutional:
                for market in _MARKETS:
                    results.append(self._refresh_institutional_day(market, current_date))
            if include_margin_short:
                for market in _MARKETS:
                    results.append(self._refresh_margin_short_day(market, current_date))
            # Stop once we have covered enough weekday probes for the requested history.
            weekday_count = sum(
                1
                for offset in range((start_date - current_date).days + 1)
                if (start_date - timedelta(days=offset)).weekday() < 5
            )
            if weekday_count >= requested_days:
                break

        finished = self._now_provider()
        summary = TaiwanMarketDataRefreshSummary(
            trigger=trigger,
            days=requested_days,
            started_at=started.astimezone(UTC).isoformat(),
            finished_at=finished.astimezone(UTC).isoformat(),
            results=tuple(results),
        )
        logger.info(
            "Taiwan market data refresh completed",
            extra={
                "event": "taiwan_market_data.refresh.completed",
                "trigger": trigger,
                "days": requested_days,
                "day_results": len(summary.results),
                "rows_upserted": summary.rows_upserted,
                "error_count": summary.error_count,
                "duration_ms": elapsed_ms(started_at_perf),
            },
        )
        return summary

    def refresh_recent(self, *, trigger: str, recent_days: int = _DEFAULT_RECENT_DAYS) -> (
        TaiwanMarketDataRefreshSummary
    ):
        return self.refresh(days=max(recent_days, 1), trigger=trigger)

    def _refresh_institutional_day(self, market: TWBoard, current_date: date) -> DatasetDayResult:
        if not self._institutional_repository.is_available():
            return DatasetDayResult(
                dataset="institutional",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=False,
                error="database_unavailable",
            )

        fetcher = (
            self._tpex_institutional_fetcher
            if market == "TPEx"
            else self._twse_institutional_fetcher
        )
        try:
            payload = fetcher(current_date)
        except HttpFetchError as exc:
            logger.warning(
                "Institutional refresh fetch failed",
                extra={
                    "event": "taiwan_market_data.institutional.fetch_error",
                    "market": market,
                    "requested_date": current_date.isoformat(),
                    "error": str(exc),
                },
            )
            return DatasetDayResult(
                dataset="institutional",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=False,
                error=str(exc),
            )

        if is_taiwan_no_data_payload(payload):
            return DatasetDayResult(
                dataset="institutional",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=True,
            )

        try:
            records = parse_all_institutional_records(
                payload,
                market=market,
                current_date=current_date,
            )
            source = institutional_source_for_market(market)
            stats = self._institutional_repository.upsert_records(
                market=market,
                source=source,
                records=records,
            )
        except Exception as exc:  # noqa: BLE001 - continue job on single-day failure
            logger.exception(
                "Institutional refresh upsert failed",
                extra={
                    "event": "taiwan_market_data.institutional.upsert_error",
                    "market": market,
                    "requested_date": current_date.isoformat(),
                },
            )
            return DatasetDayResult(
                dataset="institutional",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=False,
                error=str(exc),
            )

        resolved = records[0].date if records else None
        return DatasetDayResult(
            dataset="institutional",
            market=market,
            requested_date=current_date.isoformat(),
            resolved_date=resolved,
            rows_fetched=len(records),
            rows_inserted=stats.inserted,
            rows_updated=stats.updated,
            skipped_no_data=False,
        )

    def _refresh_margin_short_day(self, market: TWBoard, current_date: date) -> DatasetDayResult:
        if not self._margin_short_repository.is_available():
            return DatasetDayResult(
                dataset="margin_short",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=False,
                error="database_unavailable",
            )

        try:
            if market == "TPEx":
                margin_payload = self._tpex_margin_fetcher(current_date)
                borrowed_payload = self._tpex_borrowed_short_fetcher(current_date)
            else:
                margin_payload = self._twse_margin_fetcher(current_date)
                borrowed_payload = self._twse_borrowed_short_fetcher(current_date)
        except HttpFetchError as exc:
            logger.warning(
                "Margin-short refresh fetch failed",
                extra={
                    "event": "taiwan_market_data.margin_short.fetch_error",
                    "market": market,
                    "requested_date": current_date.isoformat(),
                    "error": str(exc),
                },
            )
            return DatasetDayResult(
                dataset="margin_short",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=False,
                error=str(exc),
            )

        # Close supplement is optional (matches live read path); never abort core upsert.
        close_payload: object | None
        try:
            close_payload = self._twse_close_fetcher(current_date)
        except HttpFetchError as exc:
            logger.warning(
                "Margin-short close supplement fetch failed; continuing without close",
                extra={
                    "event": "taiwan_market_data.margin_short.close_fetch_error",
                    "market": market,
                    "requested_date": current_date.isoformat(),
                    "error": str(exc),
                },
            )
            close_payload = None

        if is_taiwan_no_data_payload(margin_payload) and is_taiwan_no_data_payload(
            borrowed_payload
        ):
            return DatasetDayResult(
                dataset="margin_short",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=True,
            )

        try:
            records = parse_all_margin_short_records(
                market=market,
                current_date=current_date,
                margin_payload=margin_payload,
                borrowed_payload=borrowed_payload,
                close_payload=close_payload,
            )
            source = margin_short_source_for_market(market)
            stats = self._margin_short_repository.upsert_records(
                market=market,
                source=source,
                records=records,
            )
        except Exception as exc:  # noqa: BLE001 - continue job on single-day failure
            logger.exception(
                "Margin-short refresh upsert failed",
                extra={
                    "event": "taiwan_market_data.margin_short.upsert_error",
                    "market": market,
                    "requested_date": current_date.isoformat(),
                },
            )
            return DatasetDayResult(
                dataset="margin_short",
                market=market,
                requested_date=current_date.isoformat(),
                resolved_date=None,
                rows_fetched=0,
                rows_inserted=0,
                rows_updated=0,
                skipped_no_data=False,
                error=str(exc),
            )

        resolved = records[0].date if records else None
        return DatasetDayResult(
            dataset="margin_short",
            market=market,
            requested_date=current_date.isoformat(),
            resolved_date=resolved,
            rows_fetched=len(records),
            rows_inserted=stats.inserted,
            rows_updated=stats.updated,
            skipped_no_data=False,
        )

    def _run_sampler(self, poll_seconds: int, refresh_hour: int, recent_days: int) -> None:
        from app.core.config import settings

        timezone_name = settings.taiwan_market_data_timezone or "Asia/Taipei"
        local_tz = ZoneInfo(timezone_name)
        while not self._stop_event.wait(0):
            now = self._now_provider()
            local_hour = now.astimezone(local_tz).hour
            if local_hour >= refresh_hour:
                self.refresh_recent(trigger="scheduler", recent_days=recent_days)
            else:
                logger.info(
                    "Taiwan market data refresh skipped before local publish hour",
                    extra={
                        "event": "taiwan_market_data.scheduler.skipped_before_hour",
                        "local_hour": local_hour,
                        "refresh_hour": refresh_hour,
                        "timezone": timezone_name,
                    },
                )
            if self._stop_event.wait(poll_seconds):
                return


taiwan_market_data_refresh_service = TaiwanMarketDataRefreshService()
