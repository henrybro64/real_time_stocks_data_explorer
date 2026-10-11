"""Background-job worker entrypoint (#276).

Same image as the API. Starts enabled jobs and exposes a small health app so
Kubernetes can probe the worker Deployment independently of FastAPI request
serving.

Usage::

    RUN_BACKGROUND_JOBS=true python -m app.worker
"""

from __future__ import annotations

import logging
import threading

import uvicorn
from fastapi import FastAPI, Response

from app.core.config import settings
from app.core.logging import configure_logging
from app.jobs.runtime import start_enabled_background_jobs, stop_background_jobs

configure_logging()
logger = logging.getLogger(__name__)

health_app = FastAPI(title=f"{settings.app_name} Worker")


@health_app.get("/health")
async def worker_health(response: Response) -> dict:
    from app.jobs.health import get_background_jobs_health, unhealthy_job_names
    from app.services.quote_redis_cache import get_quote_cache_health

    unhealthy = unhealthy_job_names()
    if unhealthy:
        response.status_code = 503
    return {
        "status": "ok" if not unhealthy else "degraded",
        "role": "worker",
        "unhealthy_jobs": unhealthy,
        "quote_cache": get_quote_cache_health(),
        "background_jobs": get_background_jobs_health(),
    }


@health_app.get("/livez")
async def worker_liveness(response: Response) -> dict:
    """Liveness: 503 when a job thread died or is stuck. Stale jobs stay on /health (#289)."""
    from app.jobs.health import dead_job_names, hung_job_names

    dead = dead_job_names()
    hung = hung_job_names()
    if dead or hung:
        response.status_code = 503
    return {
        "status": "ok" if not (dead or hung) else "stuck",
        "role": "worker",
        "dead_jobs": dead,
        "hung_jobs": hung,
    }


def main() -> None:
    from app.core.auth_settings import apply_auth_runtime_settings

    # Same validation as the API so both processes agree on secrets (#284).
    apply_auth_runtime_settings(settings)
    logger.info(
        "Worker entrypoint runs background jobs in this process",
        extra={
            "event": "worker.jobs_enabled",
            "run_background_jobs": settings.run_background_jobs,
        },
    )
    start_enabled_background_jobs()
    logger.info(
        "Background worker started",
        extra={"event": "worker.started"},
    )
    try:
        uvicorn.run(
            health_app,
            host=settings.worker_health_host,
            port=8001,
            log_level=settings.log_level.lower(),
        )
    finally:
        stop_background_jobs()


if __name__ == "__main__":
    main()
