from __future__ import annotations

import logging

from app.core.config import settings
from app.services.alert_service import alert_service
from app.services.portfolio_history_service import portfolio_history_service
from app.services.quote_refresher_service import quote_refresher_service
from app.services.rankings_snapshot_service import rankings_snapshot_service
from app.services.taiwan_market_data_refresh_service import (
    taiwan_market_data_refresh_service,
)


logger = logging.getLogger(__name__)


def start_enabled_background_jobs() -> None:
    if not settings.portfolio_history_sampler_enabled:
        logger.info(
            "Portfolio history sampler is disabled",
            extra={"event": "portfolio_history.sampler.disabled"},
        )
    else:
        portfolio_history_service.start_background_sampler(
            settings.portfolio_history_poll_seconds,
        )

    if not settings.alert_evaluator_enabled:
        logger.info(
            "Alert evaluator is disabled",
            extra={"event": "alerts.evaluator.disabled"},
        )
    else:
        alert_service.start_background_evaluator(
            settings.alert_evaluator_poll_seconds,
        )

    if not settings.rankings_snapshot_enabled:
        logger.info(
            "Rankings snapshot sampler is disabled",
            extra={"event": "rankings_snapshot.sampler.disabled"},
        )
    else:
        rankings_snapshot_service.start_background_sampler(
            settings.rankings_refresh_seconds,
        )

    if not settings.taiwan_market_data_refresh_enabled:
        logger.info(
            "Taiwan market data refresh sampler is disabled",
            extra={"event": "taiwan_market_data.sampler.disabled"},
        )
    else:
        taiwan_market_data_refresh_service.start_background_sampler(
            settings.taiwan_market_data_refresh_poll_seconds,
            refresh_hour=settings.taiwan_market_data_refresh_hour,
            recent_days=settings.taiwan_market_data_refresh_recent_days,
        )

    if not settings.quote_refresher_enabled:
        logger.info(
            "Quote refresher is disabled",
            extra={"event": "quote_refresher.sampler.disabled"},
        )
    else:
        quote_refresher_service.start_background_sampler()


def stop_background_jobs() -> None:
    portfolio_history_service.stop_background_sampler()
    alert_service.stop_background_evaluator()
    rankings_snapshot_service.stop_background_sampler()
    taiwan_market_data_refresh_service.stop_background_sampler()
    quote_refresher_service.stop_background_sampler()
