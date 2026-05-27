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
from app.api.routes.search import router as search_router
from app.api.routes.stream import router as stream_router
from app.api.routes.watchlist import router as watchlist_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.init_db import seed_default_user
from app.db.session import SessionLocal
from app.middleware.request_context import install_request_context_middleware
from app.services.alert_service import alert_service
from app.services.local_auth_service import ensure_bootstrap_local_user
from app.services.portfolio_history_service import portfolio_history_service


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
    https_only=False,
)

app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(alerts_router, prefix=settings.api_prefix)
app.include_router(markets_router, prefix=settings.api_prefix)
app.include_router(rankings_router, prefix=settings.api_prefix)
app.include_router(search_router, prefix=settings.api_prefix)
app.include_router(quotes_router, prefix=settings.api_prefix)
app.include_router(history_router, prefix=settings.api_prefix)
app.include_router(watchlist_router, prefix=settings.api_prefix)
app.include_router(portfolio_router, prefix=settings.api_prefix)
app.include_router(ai_router, prefix=settings.api_prefix)
app.include_router(stream_router, prefix=settings.api_prefix)

install_request_context_middleware(app, logger=logger)


@app.on_event("startup")
def validate_auth_settings() -> None:
    if settings.auth_enabled and len(settings.session_secret) < 16:
        raise RuntimeError(
            "AUTH_ENABLED requires SESSION_SECRET (or session_secret) "
            "with at least 16 characters.",
        )
    if settings.auth_enabled and not settings.local_auth_enabled:
        raise RuntimeError("AUTH_ENABLED requires LOCAL_AUTH_ENABLED=true in local auth mode.")
    # local_auth_username/password are optional bootstrap credentials; users can also self-register.
    if bool(settings.local_auth_username) ^ bool(settings.local_auth_password):
        raise RuntimeError(
            "Set both LOCAL_AUTH_USERNAME and LOCAL_AUTH_PASSWORD, or leave both empty.",
        )
    if settings.local_auth_password and len(settings.local_auth_password) < 8:
        raise RuntimeError("LOCAL_AUTH_PASSWORD must be at least 8 characters.")


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
def start_portfolio_history_sampler() -> None:
    if not settings.portfolio_history_sampler_enabled:
        logger.info(
            "Portfolio history sampler is disabled",
            extra={"event": "portfolio_history.sampler.disabled"},
        )
        return
    portfolio_history_service.start_background_sampler(
        settings.portfolio_history_poll_seconds,
    )


@app.on_event("startup")
def start_alert_evaluator() -> None:
    if not settings.alert_evaluator_enabled:
        logger.info(
            "Alert evaluator is disabled",
            extra={"event": "alerts.evaluator.disabled"},
        )
        return
    alert_service.start_background_evaluator(
        settings.alert_evaluator_poll_seconds,
    )


@app.on_event("shutdown")
def stop_portfolio_history_sampler() -> None:
    portfolio_history_service.stop_background_sampler()


@app.on_event("shutdown")
def stop_alert_evaluator() -> None:
    alert_service.stop_background_evaluator()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
