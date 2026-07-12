from fastapi import HTTPException

from app.api.routes import taiwan_market_data as taiwan_market_data_route
from app.services.taiwan_market_data_refresh_service import TaiwanMarketDataRefreshSummary


def test_capture_taiwan_market_data_requires_configured_token(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.taiwan_market_data_capture_token", "")

    try:
        taiwan_market_data_route.capture_taiwan_market_data(
            days=None,
            x_taiwan_market_data_token="any-token",
        )
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("Expected capture route to raise HTTPException")


def test_capture_taiwan_market_data_rejects_bad_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.config.settings.taiwan_market_data_capture_token",
        "secret-token",
    )

    try:
        taiwan_market_data_route.capture_taiwan_market_data(
            days=7,
            x_taiwan_market_data_token="wrong",
        )
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected capture route to raise HTTPException")


def test_capture_taiwan_market_data_delegates_to_refresh_service(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.config.settings.taiwan_market_data_capture_token",
        "secret-token",
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

    monkeypatch.setattr(
        taiwan_market_data_route.taiwan_market_data_refresh_service,
        "refresh",
        fake_refresh,
    )

    response = taiwan_market_data_route.capture_taiwan_market_data(
        days=None,
        x_taiwan_market_data_token="secret-token",
    )

    assert captured == {"days": 120, "trigger": "capture_endpoint"}
    assert response["days"] == 120
    assert response["rowsUpserted"] == 0
    assert response["errorCount"] == 0
