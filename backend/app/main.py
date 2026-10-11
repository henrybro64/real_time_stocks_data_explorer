import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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


@app.on_event("startup")
def validate_auth_settings() -> None:
    from app.core.auth_settings import validate_auth_runtime_settings

    validate_auth_runtime_settings(
        auth_enabled=settings.auth_enabled,
        local_auth_enabled=settings.local_auth_enabled,
        session_secret=settings.session_secret,
        local_auth_username=settings.local_auth_username,
        local_auth_password=settings.local_auth_password,
        portfolio_history_capture_token=settings.portfolio_history_capture_token,
        rankings_capture_token=settings.rankings_capture_token,
        rankings_archive_capture_token=settings.rankings_archive_capture_token,
        taiwan_market_data_capture_token=settings.taiwan_market_data_capture_token,
        entra_external_id_enabled=settings.entra_external_id_enabled,
        entra_tenant_id=settings.entra_tenant_id,
        entra_client_id=settings.entra_client_id,
        entra_redirect_uri=settings.entra_redirect_uri,
        entra_authority=settings.entra_authority,
    )


@app.on_event("startup")
def bootstrap_single_user_principal() -> None:
    if settings.auth_enabled or SessionLocal is None:
        return
    seed_default_user()
    logger.info(
        "Seeded default single-user principal",
        extra={"event": "app.bootstrap.default_user_seeded"},
    )


@app.on_event("startup")
def bootstrap_local_auth_user() -> None:
    if not (settings.auth_enabled and settings.local_auth_enabled):
        return
    ensure_bootstrap_local_user(
        settings.local_auth_username,
        settings.local_auth_password,
    )
    logger.info(
        "Ensured bootstrap local-auth user",
        extra={"event": "app.bootstrap.local_auth_user_ready"},
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
