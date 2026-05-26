from __future__ import annotations

import logging
from time import perf_counter

from fastapi import FastAPI, Request

from app.core.config import settings
from app.core.diagnostics import (
    elapsed_ms,
    new_request_id,
    reset_request_id,
    reset_user_id,
    set_request_id,
    set_user_id,
)


def install_request_context_middleware(
    app: FastAPI,
    *,
    logger: logging.Logger | None = None,
) -> None:
    request_logger = logger or logging.getLogger(__name__)

    @app.middleware("http")
    async def log_request_lifecycle(request: Request, call_next):
        request_id = (
            request.headers.get(settings.request_id_header, "").strip()
            or new_request_id()
        )
        request.state.request_id = request_id
        started_at = perf_counter()
        request_id_token = set_request_id(request_id)
        user_id_token = set_user_id(None)
        status_code = 500
        client_ip = request.client.host if request.client is not None else None

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[settings.request_id_header] = request_id
            return response
        except Exception as exc:
            request_logger.exception(
                "Unhandled request error",
                extra={
                    "event": "http.request.unhandled_error",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": elapsed_ms(started_at),
                    "client_ip": client_ip,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            user_id = getattr(request.state, "user_id", None)
            request_logger.log(
                logging.ERROR if status_code >= 500 else logging.INFO,
                "Request completed",
                extra={
                    "event": "http.request.completed",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": elapsed_ms(started_at),
                    "client_ip": client_ip,
                    "user_id": user_id,
                },
            )
            reset_user_id(user_id_token)
            reset_request_id(request_id_token)
