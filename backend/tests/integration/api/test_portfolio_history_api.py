from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import portfolio as portfolio_routes
from app.main import app
from app.db.init_ids import DEFAULT_USER_ID
from app.db.models import PortfolioAsset, PortfolioHistorySnapshot, PortfolioPosition
from app.services.portfolio_history_service import portfolio_history_service
from app.services.quote_service import quote_service
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


def test_portfolio_history_endpoints_return_usd_and_twd_series(
    monkeypatch,
    db_session,
) -> None:
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            PortfolioHistorySnapshot(
                user_id=DEFAULT_USER_ID,
                snapshot_hour=now - timedelta(hours=2),
                captured_at=now - timedelta(hours=2),
                source_window="TW",
                usd_twd_rate=32.0,
                holdings_value_usd=120.0,
                holdings_value_twd=3840.0,
                combined_assets_usd=180.0,
                combined_assets_twd=5760.0,
            ),
            PortfolioHistorySnapshot(
                user_id=DEFAULT_USER_ID,
                snapshot_hour=now - timedelta(hours=1),
                captured_at=now - timedelta(hours=1),
                source_window="US",
                usd_twd_rate=32.1,
                holdings_value_usd=128.0,
                holdings_value_twd=4108.8,
                combined_assets_usd=188.0,
                combined_assets_twd=6034.8,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        portfolio_routes.portfolio_history_service,
        "capture_due_snapshots_for_user",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "app.core.config.settings.portfolio_history_sampler_enabled",
        False,
    )

    with TestClient(app) as client:
        holdings_response = client.get("/api/portfolio/history/holdings?range=1W")
        combined_response = client.get("/api/portfolio/history/combined-assets?range=1W")

    assert holdings_response.status_code == 200
    holdings_payload = holdings_response.json()
    assert holdings_payload["metric"] == "holdings"
    assert [point["sourceWindow"] for point in holdings_payload["usd"]["points"]] == ["TW", "US"]
    assert [point["value"] for point in holdings_payload["usd"]["points"]] == [120.0, 128.0]
    assert holdings_payload["twd"]["points"][1]["value"] == 4108.8
    assert holdings_payload["usd"]["chartLayout"]["trendUp"] is True

    assert combined_response.status_code == 200
    combined_payload = combined_response.json()
    assert combined_payload["metric"] == "combinedAssets"
    assert [point["value"] for point in combined_payload["usd"]["points"]] == [180.0, 188.0]
    assert combined_payload["twd"]["points"][0]["value"] == 5760.0


def test_capture_due_snapshots_upserts_current_hour(
    monkeypatch,
    db_session,
) -> None:
    fixed_now = datetime.fromisoformat("2026-05-05T15:45:00+00:00")
    snapshot_hour = datetime.fromisoformat("2026-05-05T15:00:00+00:00")
    db_session.add(
        PortfolioPosition(
            user_id=DEFAULT_USER_ID,
            stock_id="US:AAPL",
            quantity=2.0,
            average_cost=100.0,
            currency="USD",
            notes=None,
        )
    )
    db_session.add(
        PortfolioAsset(
            user_id=DEFAULT_USER_ID,
            asset_type="cash",
            name="Cash reserve",
            currency="USD",
            value=500.0,
            notes=None,
        )
    )
    db_session.commit()

    quote_box = {"last_price": 120.0}

    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock_record(
            "US:AAPL",
            market="US",
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            name_en="Apple Inc.",
        )
        if stock_id == "US:AAPL"
        else None,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: quote_record(
            "US:AAPL",
            last_price=quote_box["last_price"],
            change=1.0,
            change_percent=0.5,
            previous_close=quote_box["last_price"] - 1.0,
            timestamp="2026-05-05T11:45:00-04:00",
            is_market_open=True,
        )
        if stock_id == "US:AAPL"
        else None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_history_service.iter_open_snapshot_windows",
        lambda now=None: [("US", snapshot_hour)],
    )
    monkeypatch.setattr(
        "app.services.portfolio_history_service.get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-05-05T15:45:00+00:00",
            "status": "ok",
        },
    )

    portfolio_history_service.capture_due_snapshots_for_user(
        DEFAULT_USER_ID,
        now=fixed_now,
    )
    snapshots = list(
        db_session.execute(
            select(PortfolioHistorySnapshot).order_by(PortfolioHistorySnapshot.snapshot_hour.asc())
        ).scalars()
    )

    assert len(snapshots) == 1
    assert snapshots[0].snapshot_hour == snapshot_hour
    assert snapshots[0].holdings_value_usd == 240.0
    assert snapshots[0].holdings_value_twd == 7680.0
    assert snapshots[0].combined_assets_usd == 740.0
    assert snapshots[0].combined_assets_twd == 23680.0

    quote_box["last_price"] = 135.0
    portfolio_history_service.capture_due_snapshots_for_user(
        DEFAULT_USER_ID,
        now=fixed_now,
    )
    db_session.expire_all()
    refreshed = list(
        db_session.execute(
            select(PortfolioHistorySnapshot).order_by(PortfolioHistorySnapshot.snapshot_hour.asc())
        ).scalars()
    )

    assert len(refreshed) == 1
    assert refreshed[0].holdings_value_usd == 270.0
    assert refreshed[0].holdings_value_twd == 8640.0
    assert refreshed[0].combined_assets_usd == 770.0
    assert refreshed[0].combined_assets_twd == 24640.0


def test_capture_endpoint_requires_token_and_triggers_capture(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.portfolio_history_capture_token", "test-token")
    monkeypatch.setattr("app.core.config.settings.portfolio_history_sampler_enabled", False)
    monkeypatch.setattr(
        portfolio_routes.portfolio_history_service,
        "capture_due_snapshots_for_all_users",
        lambda **_: 3,
    )

    with TestClient(app) as client:
        forbidden = client.post("/api/portfolio/history/capture")
        assert forbidden.status_code == 403

        response = client.post(
            "/api/portfolio/history/capture",
            headers={"X-Portfolio-History-Token": "test-token"},
        )
        assert response.status_code == 200
        assert response.json() == {"captured": 3}
