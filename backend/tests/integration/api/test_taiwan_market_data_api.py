from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.taiwan_market_data_refresh_service import (
    TaiwanMarketDataRefreshSummary,
    taiwan_market_data_refresh_service,
)


pytestmark = pytest.mark.integration


def test_taiwan_market_data_capture_endpoint_requires_token_and_triggers_refresh(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.core.config.settings.taiwan_market_data_refresh_enabled",
        False,
    )
    monkeypatch.setattr(
        "app.core.config.settings.taiwan_market_data_capture_token",
        "test-token",
    )
    monkeypatch.setattr(
        "app.core.config.settings.taiwan_market_data_refresh_days",
        120,
    )

    captured: dict[str, object] = {}

    def fake_refresh(*, days, trigger):
        captured["days"] = days
        captured["trigger"] = trigger
        return TaiwanMarketDataRefreshSummary(
            trigger=trigger,
            days=days,
            started_at="2026-07-10T00:00:00+00:00",
            finished_at="2026-07-10T00:01:00+00:00",
            results=(),
        )

    monkeypatch.setattr(taiwan_market_data_refresh_service, "refresh", fake_refresh)

    with TestClient(app) as client:
        unconfigured_forbidden = client.post("/api/taiwan-market-data/capture")
        assert unconfigured_forbidden.status_code == 403

        response = client.post(
            "/api/taiwan-market-data/capture",
            headers={"X-Taiwan-Market-Data-Token": "test-token"},
            params={"days": 30},
        )

    assert response.status_code == 200
    assert response.json()["days"] == 30
    assert response.json()["rowsUpserted"] == 0
    assert captured == {"days": 30, "trigger": "capture_endpoint"}
