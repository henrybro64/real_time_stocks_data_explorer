import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes.alerts import router as alerts_router
from app.api.routes.ai import router as ai_router
from app.api.routes.auth import router as auth_router
from app.api.routes.history import router as history_router
from app.api.routes.markets import router as markets_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.rankings import router as rankings_router
from app.api.routes.realized_trades import router as realized_trades_router
from app.api.routes.search import router as search_router
from app.api.routes.stream import router as stream_router
from app.api.routes.themes import router as themes_router
from app.api.routes.tw_institutional import router as tw_institutional_router
from app.api.routes.tw_margin_short import router as tw_margin_short_router
from app.api.routes.tw_ownership_concentration import router as tw_ownership_concentration_router
from app.api.routes.tw_profitability import router as tw_profitability_router
from app.api.routes.taiwan_market_data import router as taiwan_market_data_router
from app.api.routes.watchlist import router as watchlist_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.init_db import seed_default_user
from app.db.session import SessionLocal
from app.jobs.runtime import start_enabled_background_jobs, stop_background_jobs
from app.middleware.request_context import install_request_context_middleware
from app.services.local_auth_service import ensure_bootstrap_local_user


configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=bool(settings.session_cookie_secure),
)

app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(alerts_router, prefix=settings.api_prefix)
app.include_router(markets_router, prefix=settings.api_prefix)
app.include_router(rankings_router, prefix=settings.api_prefix)
app.include_router(themes_router, prefix=settings.api_prefix)
app.include_router(search_router, prefix=settings.api_prefix)
app.include_router(quotes_router, prefix=settings.api_prefix)
app.include_router(history_router, prefix=settings.api_prefix)
app.include_router(tw_institutional_router, prefix=settings.api_prefix)
app.include_router(tw_margin_short_router, prefix=settings.api_prefix)
app.include_router(tw_ownership_concentration_router, prefix=settings.api_prefix)
app.include_router(tw_profitability_router, prefix=settings.api_prefix)
app.include_router(taiwan_market_data_router, prefix=settings.api_prefix)
app.include_router(watchlist_router, prefix=settings.api_prefix)
app.include_router(portfolio_router, prefix=settings.api_prefix)
app.include_router(realized_trades_router, prefix=settings.api_prefix)
app.include_router(ai_router, prefix=settings.api_prefix)
app.include_router(stream_router, prefix=settings.api_prefix)

install_request_context_middleware(app, logger=logger)


def _unreachable_database_error(exc: BaseException) -> OperationalError | None:
    """The connection failure behind ``exc``, if that is what it is (#290).

    ``seed_default_user`` wraps driver errors in ``RuntimeError``, so check the cause.
    Schema errors and bugs return None and still stop startup.
    """
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, OperationalError):
            return candidate
    return None


def _log_bootstrap_skipped(message: str, event: str, error: OperationalError) -> None:
    # Log the type only: driver messages can carry the host and username.
    logger.error(message, extra={"event": event, "error_type": type(error).__name__})


@app.on_event("startup")
def validate_auth_settings() -> None:
    from app.core.auth_settings import apply_auth_runtime_settings

    # Clears placeholder capture tokens (soft) and refuses bad SESSION_SECRET (#284).
    apply_auth_runtime_settings(settings)


@app.on_event("startup")
def bootstrap_single_user_principal() -> None:
    if settings.auth_enabled or SessionLocal is None:
        return
    try:
        seed_default_user()
    except RuntimeError as exc:
        # Bootstrap is optional; an unreachable DB must not stop the API (#290).
        error = _unreachable_database_error(exc)
        if error is None:
            raise
        _log_bootstrap_skipped(
            "Default user seed skipped; database unreachable",
            "app.bootstrap.default_user_failed",
            error,
        )
        return
    logger.info(
        "Seeded default single-user principal",
        extra={"event": "app.bootstrap.default_user_seeded"},
    )


@app.on_event("startup")
def bootstrap_local_auth_user() -> None:
    if not (settings.auth_enabled and settings.local_auth_enabled):
        return
    try:
        outcome = ensure_bootstrap_local_user(
            settings.local_auth_username,
            settings.local_auth_password,
            reset_password=settings.local_auth_reset_password,
        )
    except OperationalError as exc:
        # Same as the default user: keep serving; the next restart creates it (#290).
        _log_bootstrap_skipped(
            "Bootstrap local-auth skipped; database unreachable",
            "app.bootstrap.local_auth_user_failed",
            exc,
        )
        return
    logger.info(
        "Bootstrap local-auth finished",
        extra={
            "event": "app.bootstrap.local_auth_user_ready",
            "outcome": outcome,
            "reset_password": settings.local_auth_reset_password,
        },
    )


@app.on_event("startup")
def start_background_jobs() -> None:
    # API replicas set RUN_BACKGROUND_JOBS=false; the worker Deployment runs jobs (#276).
    if not settings.run_background_jobs:
        logger.info(
            "Background jobs disabled in this process",
            extra={"event": "app.jobs.disabled", "run_background_jobs": False},
        )
        return
    start_enabled_background_jobs()


@app.on_event("shutdown")
def stop_runtime_background_jobs() -> None:
    if not settings.run_background_jobs:
        return
    stop_background_jobs()


@app.get("/health")
async def healthcheck() -> dict:
    from app.jobs.health import get_background_jobs_health
    from app.services.quote_redis_cache import get_quote_cache_health

    return {
        "status": "ok",
        "quote_cache": get_quote_cache_health(),
        "background_jobs": get_background_jobs_health(),
    }
