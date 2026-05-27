from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import deps as deps_module
from app.core.config import settings
from app.core.diagnostics import current_log_context, set_user_id
from app.core.logging import JsonLogFormatter, configure_logging
from app.db.init_ids import DEFAULT_USER_ID
from app.middleware.request_context import install_request_context_middleware


configure_logging()


def test_json_log_formatter_includes_structured_fields() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    record.event = "test.event"
    record.request_id = "req-123"
    record.user_id = "user-456"
    record.stock_id = "US:AAPL"

    payload = json.loads(formatter.format(record))

    assert payload["event"] == "test.event"
    assert payload["request_id"] == "req-123"
    assert payload["user_id"] == "user-456"
    assert payload["stock_id"] == "US:AAPL"
    assert payload["message"] == "hello world"


def test_get_request_identity_binds_user_context(monkeypatch) -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/status",
        "headers": [],
        "session": {"user_id": "user-123"},
    }
    request = Request(scope)

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(
        deps_module,
        "get_user_by_id",
        lambda user_id: {"id": user_id, "username": "demo"},
    )

    identity = deps_module.get_request_identity(request)

    assert identity.user_id == "user-123"
    assert current_log_context()["user_id"] == "user-123"
    set_user_id(None)


def test_request_id_header_is_returned_and_logged(caplog) -> None:
    request_id = "req-health-123"
    caplog.set_level(logging.INFO)

    app = FastAPI()
    install_request_context_middleware(app)

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/health", headers={settings.request_id_header: request_id})

    assert response.headers[settings.request_id_header] == request_id
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http.request.completed"
        and getattr(record, "request_id", None) == request_id
    ]
    assert records
    assert records[-1].path == "/health"
    assert records[-1].status_code == 200


def test_request_log_includes_user_id_when_identity_is_resolved(caplog) -> None:
    request_id = "req-auth-status-123"
    caplog.set_level(logging.INFO)

    app = FastAPI()
    install_request_context_middleware(app)

    @app.get("/auth-status")
    def auth_status(request: Request) -> dict[str, str | bool]:
        identity = deps_module.get_request_identity(request)
        return {
            "authEnabled": identity.auth_enabled,
            "loggedIn": identity.is_authenticated,
        }

    with TestClient(app) as client:
        response = client.get(
            "/auth-status",
            headers={settings.request_id_header: request_id},
        )

    assert response.status_code == 200
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http.request.completed"
        and getattr(record, "request_id", None) == request_id
    ]
    assert records
    assert records[-1].user_id == DEFAULT_USER_ID
