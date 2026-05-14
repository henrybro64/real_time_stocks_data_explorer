from datetime import datetime, timezone

from app.api.routes import rankings as rankings_route
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse


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
        timestamp=datetime(2026, 5, 11, 1, 30, tzinfo=timezone.utc).isoformat(),
        source="Quote rankings from the app's free best-effort quote providers.",
        sourceType=RankingSourceType.free_unofficial,
        isMarketOpen=True,
        dataFreshnessLabel="Market open; rankings use the latest quote snapshot.",
        warningMessage="Free unofficial quote source; data may be delayed or unavailable.",
        unsupportedReason=None,
        usRankingsScope=None,
        twBoard="TWSE",
    )


def test_get_rankings_route_delegates_to_service(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_rankings(market, ranking_type, limit, tw_board=None):
        captured["market"] = market
        captured["ranking_type"] = ranking_type
        captured["limit"] = limit
        return _sample_response()

    monkeypatch.setattr(rankings_route.rankings_service, "get_rankings", fake_get_rankings)

    response = rankings_route.get_rankings(market="TW", ranking_type=RankingType.volume, limit=7)

    assert captured["market"] == "TW"
    assert captured["ranking_type"] == RankingType.volume
    assert captured["limit"] == 7
    assert response.rankingType == RankingType.volume
    assert response.items[0].stock.symbol == "2330"
