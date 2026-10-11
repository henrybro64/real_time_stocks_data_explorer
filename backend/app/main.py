import logging
import threading
from collections.abc import Callable

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


# Retry spacing after an unreachable DB at startup; the last value repeats (#290).
_BOOTSTRAP_RETRY_DELAYS_SECONDS: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)
_bootstrap_stop = threading.Event()
_bootstrap_retry_threads: list[threading.Thread] = []


def _try_bootstrap(step: Callable[[], None], *, failed_event: str) -> bool:
    """Run one bootstrap step. False when the DB is unreachable; other errors raise."""
    try:
        step()
    except (OperationalError, RuntimeError) as exc:
        error = _unreachable_database_error(exc)
        if error is None:
            raise
        # Log the type only: driver messages can carry the host and username.
        logger.error(
            "Bootstrap step skipped; database unreachable",
            extra={"event": failed_event, "error_type": type(error).__name__},
        )
        return False
    return True


def _retry_bootstrap(step: Callable[[], None], *, failed_event: str) -> None:
    attempt = 0
    while True:
        delays = _BOOTSTRAP_RETRY_DELAYS_SECONDS
        if _bootstrap_stop.wait(delays[min(attempt, len(delays) - 1)]):
            return
        attempt += 1
        try:
            if _try_bootstrap(step, failed_event=failed_event):
                return
        except Exception as exc:  # noqa: BLE001 - nothing to raise to in a daemon thread
            logger.error(
                "Bootstrap retry stopped after a non-connection error",
                extra={
                    "event": "app.bootstrap.retry_stopped",
                    "failed_event": failed_event,
                    "error_type": type(exc).__name__,
                },
            )
            return


def _bootstrap(name: str, step: Callable[[], None], *, failed_event: str) -> None:
    """Run ``step`` now; if the DB is unreachable, keep serving and retry in the background.

    Retrying matters: with auth off every request writes as ``default-user``, and
    user-owned tables have a foreign key to it, so a skipped seed breaks writes (#290).
    """
    if _try_bootstrap(step, failed_event=failed_event):
        return
    _bootstrap_stop.clear()
    thread = threading.Thread(
        target=_retry_bootstrap,
        args=(step,),
        kwargs={"failed_event": failed_event},
        name=f"bootstrap-retry-{name}",
        daemon=True,
    )
    _bootstrap_retry_threads.append(thread)
    thread.start()


def _seed_default_user_step() -> None:
    seed_default_user()
    logger.info(
        "Seeded default single-user principal",
        extra={"event": "app.bootstrap.default_user_seeded"},
    )


def _local_auth_user_step() -> None:
    outcome = ensure_bootstrap_local_user(
        settings.local_auth_username,
        settings.local_auth_password,
        reset_password=settings.local_auth_reset_password,
    )
    logger.info(
        "Bootstrap local-auth finished",
        extra={
            "event": "app.bootstrap.local_auth_user_ready",
            "outcome": outcome,
            "reset_password": settings.local_auth_reset_password,
        },
    )


@app.on_event("startup")
def validate_auth_settings() -> None:
    from app.core.auth_settings import apply_auth_runtime_settings

    # Clears placeholder capture tokens (soft) and refuses bad SESSION_SECRET (#284).
    apply_auth_runtime_settings(settings)


@app.on_event("startup")
def bootstrap_single_user_principal() -> None:
    if settings.auth_enabled or SessionLocal is None:
        return
    _bootstrap(
        "default-user",
        _seed_default_user_step,
        failed_event="app.bootstrap.default_user_failed",
    )


@app.on_event("startup")
def bootstrap_local_auth_user() -> None:
    if not (settings.auth_enabled and settings.local_auth_enabled):
        return
    _bootstrap(
        "local-auth-user",
        _local_auth_user_step,
        failed_event="app.bootstrap.local_auth_user_failed",
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
def stop_bootstrap_retries() -> None:
    _bootstrap_stop.set()


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
