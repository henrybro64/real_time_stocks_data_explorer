from __future__ import annotations

from datetime import datetime, timezone

from app.api.routes import markets as markets_route
from app.services.market_index_service import (
    MarketIndexRecord,
    MarketIndexService,
    _INDEX_CATALOG,
)


def test_markets_indices_route_serializes_unavailable_placeholder(monkeypatch) -> None:
    def yahoo_us(_yahoo_symbol: str, record_id: str, label: str) -> MarketIndexRecord:
        return MarketIndexRecord(
            id=record_id,
            market="US",
            label=label,
            value=100.0,
            change=1.0,
            change_percent=1.0,
            timestamp=datetime(2026, 7, 13, tzinfo=timezone.utc).isoformat(),
            is_market_open=False,
        )

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
        yahoo_us_fetcher=yahoo_us,
    )
    monkeypatch.setattr(markets_route, "market_index_service", service)

    response = markets_route.get_market_indices()
    catalog_ids = [slot.id for slot in _INDEX_CATALOG]
    sox = next(slot for slot in _INDEX_CATALOG if slot.id == "^SOX")

    assert [item["id"] for item in response] == catalog_ids
    assert response[0]["id"] == catalog_ids[0]
    assert response[0]["status"] == "unavailable"
    assert response[0]["value"] is None
    assert response[0]["unavailableReason"] == "taiwan_provider_unavailable"
    assert response[1]["status"] == "ok"
    by_id = {item["id"]: item for item in response}
    assert by_id[sox.id]["label"] == sox.label
    assert by_id[sox.id]["status"] == "ok"
    assert by_id["BTC-USD"]["market"] == "CRYPTO"
    assert by_id["BTC-USD"]["isMarketOpen"] is True
    assert by_id["^VIX"]["market"] == "US"
    assert by_id["GC=F"]["market"] == "COMMODITY"
    assert by_id["GC=F"]["isMarketOpen"] is False
