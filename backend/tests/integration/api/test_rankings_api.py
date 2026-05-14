import pytest

from app.api.routes import rankings as rankings_route
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse


pytestmark = pytest.mark.integration


def _sample_response() -> RankingResponse:
    return RankingResponse(
        market="TW",
        rankingType=RankingType.volume,
        items=[
            RankingItem(
                rank=1,
                stock=StockResponse(
                    id="TW:2330",
                    market="TW",
                    symbol="2330",
                    name="台積電",
                    exchange="TWSE",
                    currency="TWD",
                    timezone="Asia/Taipei",
                    nameEn="TSMC",
                    nameZhTw="台積電",
                ),
                lastPrice=950.0,
                changePercent=1.2,
                volume=123456,
                volumeDeltaPercent=5.0,
                netBuySellShares=None,
                timestamp="2026-05-11T09:30:00+08:00",
            )
        ],
        timestamp="2026-05-11T01:30:00Z",
        source="Quote rankings from the app's free best-effort quote providers.",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=True,
        dataFreshnessLabel="Market open; rankings use the latest quote snapshot.",
        warningMessage="Free unofficial quote source; data may be delayed or unavailable.",
        unsupportedReason=None,
        usRankingsScope=None,
    )


def test_rankings_endpoint_returns_response_shape(monkeypatch, client) -> None:
    monkeypatch.setattr(rankings_route.rankings_service, "get_rankings", lambda market, ranking_type, limit: _sample_response())

    response = client.get("/api/rankings", params={"market": "TW", "type": "volume", "limit": 1})

    assert response.status_code == 200
    assert response.json() == {
        "market": "TW",
        "rankingType": "volume",
        "items": [
            {
                "rank": 1,
                "stock": {
                    "id": "TW:2330",
                    "market": "TW",
                    "symbol": "2330",
                    "name": "台積電",
                    "exchange": "TWSE",
                    "currency": "TWD",
                    "timezone": "Asia/Taipei",
                    "nameEn": "TSMC",
                    "nameZhTw": "台積電",
                },
                "lastPrice": 950.0,
                "changePercent": 1.2,
                "volume": 123456,
                "volumeDeltaPercent": 5.0,
                "netBuySellShares": None,
                "timestamp": "2026-05-11T09:30:00+08:00",
            }
        ],
        "timestamp": "2026-05-11T01:30:00Z",
        "source": "Quote rankings from the app's free best-effort quote providers.",
        "sourceType": "free-unofficial",
        "isMarketOpen": True,
        "dataFreshnessLabel": "Market open; rankings use the latest quote snapshot.",
        "warningMessage": "Free unofficial quote source; data may be delayed or unavailable.",
        "unsupportedReason": None,
        "usRankingsScope": None,
    }


def test_rankings_endpoint_passes_limit(monkeypatch, client) -> None:
    captured: dict[str, object] = {}

    def fake_get_rankings(market, ranking_type, limit):
        captured["market"] = market
        captured["ranking_type"] = ranking_type
        captured["limit"] = limit
        return _sample_response()

    monkeypatch.setattr(rankings_route.rankings_service, "get_rankings", fake_get_rankings)

    response = client.get("/api/rankings", params={"market": "TW", "type": "volume", "limit": 7})

    assert response.status_code == 200
    assert captured == {
        "market": "TW",
        "ranking_type": RankingType.volume,
        "limit": 7,
    }


def test_rankings_endpoint_validates_market(client) -> None:
    response = client.get("/api/rankings", params={"market": "JP", "type": "volume", "limit": 5})

    assert response.status_code == 422


def test_rankings_endpoint_validates_ranking_type(client) -> None:
    response = client.get("/api/rankings", params={"market": "TW", "type": "turnover", "limit": 5})

    assert response.status_code == 422
