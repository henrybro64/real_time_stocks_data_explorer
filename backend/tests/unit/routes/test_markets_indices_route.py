from __future__ import annotations

from datetime import datetime, timezone

from app.api.routes import markets as markets_route
from app.services.market_index_service import MarketIndexRecord, MarketIndexService


def test_markets_indices_route_serializes_unavailable_placeholder(monkeypatch) -> None:
    service = MarketIndexService(
        twse_fetcher=lambda: None,
        yahoo_taiwan_fetcher=lambda: None,
        stooq_fetcher=lambda symbol, label: MarketIndexRecord(
            id=symbol,
            market="US",
            label=label,
            value=100.0,
            change=1.0,
            change_percent=1.0,
            timestamp=datetime(2026, 7, 13, tzinfo=timezone.utc).isoformat(),
            is_market_open=False,
        ),
    )
    monkeypatch.setattr(markets_route, "market_index_service", service)

    response = markets_route.get_market_indices()

    assert len(response) == 4
    assert response[0]["id"] == "TWSE:TAIEX"
    assert response[0]["status"] == "unavailable"
    assert response[0]["value"] is None
    assert response[0]["unavailableReason"] == "taiwan_provider_unavailable"
    assert response[1]["status"] == "ok"
