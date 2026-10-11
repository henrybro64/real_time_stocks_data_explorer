from __future__ import annotations

import logging

from app.core.config import settings
from app.jobs.health import register_background_job
from app.services.alert_service import alert_service
from app.services.portfolio_history_service import portfolio_history_service
from app.services.quote_refresher_service import quote_refresher_service
from app.services.rankings_archive_service import rankings_archive_service
from app.services.reference_refresh_service import reference_refresh_service
from app.services.rankings_snapshot_service import rankings_snapshot_service
from app.services.taiwan_market_data_refresh_service import (
    taiwan_market_data_refresh_service,
)


logger = logging.getLogger(__name__)


def _max_tick_gap(poll_seconds: float) -> float:
    # Each loop wakes at least once per poll; 3 polls (1h floor) means stuck (#289).
    return max(3600.0, 3.0 * float(poll_seconds))


def start_enabled_background_jobs() -> None:
    if not settings.portfolio_history_sampler_enabled:
        logger.info(
            "Portfolio history sampler is disabled",
            extra={"event": "portfolio_history.sampler.disabled"},
        )
        register_background_job("portfolio-history-sampler", enabled=False)
    else:
        portfolio_history_service.start_background_sampler(
            settings.portfolio_history_poll_seconds,
        )
        register_background_job(
            "portfolio-history-sampler",
            enabled=True,
            thread=portfolio_history_service._thread,
            max_stale_seconds=max(120, settings.portfolio_history_poll_seconds * 4),
            max_tick_gap_seconds=_max_tick_gap(settings.portfolio_history_poll_seconds),
        )

    if not settings.alert_evaluator_enabled:
        logger.info(
            "Alert evaluator is disabled",
            extra={"event": "alerts.evaluator.disabled"},
        )
        register_background_job("alert-rule-evaluator", enabled=False)
    else:
        alert_service.start_background_evaluator(
            settings.alert_evaluator_poll_seconds,
        )
        register_background_job(
            "alert-rule-evaluator",
            enabled=True,
            thread=alert_service._thread,
            max_stale_seconds=max(120, settings.alert_evaluator_poll_seconds * 4),
            max_tick_gap_seconds=_max_tick_gap(settings.alert_evaluator_poll_seconds),
        )

    if not settings.rankings_snapshot_enabled:
        logger.info(
            "Rankings snapshot sampler is disabled",
            extra={"event": "rankings_snapshot.sampler.disabled"},
        )
        register_background_job("rankings-snapshot-sampler", enabled=False)
    else:
        rankings_snapshot_service.start_background_sampler(
            settings.rankings_refresh_seconds,
        )
        register_background_job(
            "rankings-snapshot-sampler",
            enabled=True,
            thread=rankings_snapshot_service._thread,
            max_stale_seconds=max(300, settings.rankings_refresh_seconds * 3),
            max_tick_gap_seconds=_max_tick_gap(settings.rankings_refresh_seconds),
        )

    if not settings.taiwan_market_data_refresh_enabled:
        logger.info(
            "Taiwan market data refresh sampler is disabled",
            extra={"event": "taiwan_market_data.sampler.disabled"},
        )
        register_background_job("taiwan-market-data-refresh", enabled=False)
    else:
        taiwan_market_data_refresh_service.start_background_sampler(
            settings.taiwan_market_data_refresh_poll_seconds,
            refresh_hour=settings.taiwan_market_data_refresh_hour,
            recent_days=settings.taiwan_market_data_refresh_recent_days,
        )
        register_background_job(
            "taiwan-market-data-refresh",
            enabled=True,
            thread=taiwan_market_data_refresh_service._thread,
            max_stale_seconds=26 * 60 * 60,
            max_tick_gap_seconds=_max_tick_gap(
                settings.taiwan_market_data_refresh_poll_seconds
            ),
        )

    if not settings.quote_refresher_enabled:
        logger.info(
            "Quote refresher is disabled",
            extra={"event": "quote_refresher.sampler.disabled"},
        )
        register_background_job("quote-refresher", enabled=False)
    else:
        quote_refresher_service.start_background_sampler()
        register_background_job(
            "quote-refresher",
            enabled=True,
            thread=quote_refresher_service._thread,
            max_stale_seconds=max(
                180,
                settings.quote_refresher_open_poll_seconds,
                settings.quote_refresher_closed_poll_seconds,
            )
            * 3,
            max_tick_gap_seconds=_max_tick_gap(
                max(
                    settings.quote_refresher_open_poll_seconds,
                    settings.quote_refresher_closed_poll_seconds,
                )
            ),
        )

    if not settings.reference_refresh_enabled:
        logger.info(
            "Reference refresh sampler is disabled",
            extra={"event": "reference_refresh.sampler.disabled"},
        )
        register_background_job("reference-refresh", enabled=False)
    else:
        reference_refresh_service.start_background_sampler(
            settings.reference_refresh_poll_seconds,
        )
        register_background_job(
            "reference-refresh",
            enabled=True,
            thread=reference_refresh_service._thread,
            max_stale_seconds=max(300, settings.reference_refresh_poll_seconds * 3),
            max_tick_gap_seconds=_max_tick_gap(settings.reference_refresh_poll_seconds),
        )

    if not settings.rankings_archive_eod_enabled:
        logger.info(
            "Rankings archive EOD sampler is disabled",
            extra={"event": "rankings_archive.sampler.disabled"},
        )
        register_background_job("rankings-archive-eod", enabled=False)
    else:
        rankings_archive_service.start_background_sampler(
            settings.rankings_archive_eod_poll_seconds,
        )
        register_background_job(
            "rankings-archive-eod",
            enabled=True,
            thread=rankings_archive_service._thread,
            # Backstop if the loop stops waking. Idle time until the next
            # settle records success on each poll, so a weekend is healthy.
            max_stale_seconds=26 * 60 * 60,
            max_tick_gap_seconds=_max_tick_gap(settings.rankings_archive_eod_poll_seconds),
        )


def stop_background_jobs() -> None:
    portfolio_history_service.stop_background_sampler()
    alert_service.stop_background_evaluator()
    rankings_snapshot_service.stop_background_sampler()
    taiwan_market_data_refresh_service.stop_background_sampler()
    quote_refresher_service.stop_background_sampler()
    reference_refresh_service.stop_background_sampler()
    rankings_archive_service.stop_background_sampler()
    for job_name in (
        "portfolio-history-sampler",
        "alert-rule-evaluator",
        "rankings-snapshot-sampler",
        "taiwan-market-data-refresh",
        "quote-refresher",
        "reference-refresh",
        "rankings-archive-eod",
    ):
        register_background_job(job_name, enabled=False, thread=None)
