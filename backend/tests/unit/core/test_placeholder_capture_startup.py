"""API starts with a placeholder capture token (#284)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from app.core.auth_settings import apply_auth_runtime_settings


def test_placeholder_archive_token_does_not_block_startup(caplog) -> None:
    settings = SimpleNamespace(
        auth_enabled=True,
        local_auth_enabled=True,
        session_secret="9f" * 16,
        local_auth_username="",
        local_auth_password="",
        portfolio_history_capture_token="",
        rankings_capture_token="",
        rankings_archive_capture_token="replace-with-long-random-token",
        taiwan_market_data_capture_token="",
        entra_external_id_enabled=False,
        entra_tenant_id="",
        entra_client_id="",
        entra_redirect_uri="",
        entra_authority="",
    )
    with caplog.at_level(logging.ERROR):
        soft = apply_auth_runtime_settings(settings)
    assert soft == ["RANKINGS_ARCHIVE_CAPTURE_TOKEN"]
    assert settings.rankings_archive_capture_token == ""
    assert any(
        getattr(record, "token_name", None) == "RANKINGS_ARCHIVE_CAPTURE_TOKEN"
        for record in caplog.records
    )


def test_worker_entrypoint_applies_the_same_auth_check(monkeypatch) -> None:
    import app.worker as worker

    seen: list[object] = []

    def _apply(settings):
        seen.append(settings)
        return []

    monkeypatch.setattr("app.core.auth_settings.apply_auth_runtime_settings", _apply)
    monkeypatch.setattr(worker, "start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr(worker.uvicorn, "run", lambda *args, **kwargs: None)

    worker.main()

    assert seen == [worker.settings]
