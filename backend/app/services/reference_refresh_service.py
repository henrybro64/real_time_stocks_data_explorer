"""Background refresh for catalogs, holidays, and the closed-market daily month."""

from __future__ import annotations

import logging
from datetime import datetime
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

from app.core.config import settings

logger = logging.getLogger(__name__)


class ReferenceRefreshService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start_background_sampler(self, poll_seconds: int) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = Event()
            self._thread = Thread(
                target=self._run,
                args=(poll_seconds,),
                daemon=True,
                name="reference-refresh",
            )
            self._thread.start()

    def stop_background_sampler(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._thread = None
            self._stop_event.set()
        thread.join(timeout=1.0)

    def _run(self, poll_seconds: int) -> None:
        from app.jobs.health import run_guarded_tick
        from app.services.job_leader_lock import next_tick_wait_seconds, run_leader_tick

        job_name = "reference-refresh"
        while not self._stop_event.is_set():
            run_guarded_tick(
                job_name,
                event="reference_refresh.failed",
                tick=lambda: run_leader_tick(job_name, self.run_once),
            )
            # A tick skipped for leadership retries soon, not after a full poll (#292).
            if self._stop_event.wait(next_tick_wait_seconds(job_name, poll_seconds)):
                return

    def run_once(self) -> None:
        from app.services.job_leader_lock import (
            release_job_leadership,
            try_acquire_job_leadership,
        )

        token = try_acquire_job_leadership(
            "reference-refresh",
            ttl_seconds=max(60, int(settings.reference_refresh_poll_seconds)),
        )
        if token is None:
            return
        try:
            self.refresh_reference_data()
            self.refresh_closed_daily_months()
        except Exception:
            release_job_leadership("reference-refresh", token)
            raise

    def refresh_reference_data(self) -> None:
        from app.services.market_clock import refresh_tw_market_holidays
        from app.services.providers.tw_provider import TWProvider
        from app.services.providers.us_provider import USProvider

        TWProvider().refresh_catalog()
        USProvider().refresh_catalog()
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        refresh_tw_market_holidays(now.year)
        if now.month == 12:
            refresh_tw_market_holidays(now.year + 1)

    def refresh_closed_daily_months(self) -> None:
        from app.services.daily_price_history_service import daily_price_history_service
        from app.services.providers.tw_provider import TWProvider
        from app.services.providers.us_provider import USProvider
        from app.services.quote_refresher_service import quote_refresher_service

        try:
            stock_ids, _duplicates = quote_refresher_service.collect_tracked_stock_ids()
        except Exception as exc:  # noqa: BLE001 - the catalog refresh still stands
            logger.debug(
                "Tracked symbol list unavailable for daily-month refresh",
                extra={
                    "event": "reference_refresh.tracked_ids_failed",
                    "error_type": type(exc).__name__,
                },
            )
            return
        providers = {"TW": TWProvider(), "US": USProvider()}
        for stock_id in stock_ids:
            market = stock_id.split(":", 1)[0]
            provider = providers.get(market)
            if provider is None:
                continue
            stock = provider.get_stock(stock_id)
            if stock is None:
                continue
            try:
                daily_price_history_service.refresh_closed_current_month(stock, provider)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Closed-market daily month refresh failed for symbol",
                    extra={
                        "event": "reference_refresh.month_failed",
                        "stock_id": stock_id,
                        "error_type": type(exc).__name__,
                    },
                )


reference_refresh_service = ReferenceRefreshService()
