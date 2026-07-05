from datetime import date

import pytest

from app.api.routes import rankings as rankings_route
from app.schemas.rankings import (
    RankingItem,
    RankingResponse,
    RankingSourceType,
    RankingType,
)
from app.schemas.stock import StockResponse
from app.services.quote_service import quote_service
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


def _sample_response() -> RankingResponse:
    return RankingResponse(
        market="TW",
        rankingType=RankingType.volume,
        twBoard="TWSE",
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


def _install_tpex_institutional_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    *,
    net_buy_sell_shares: int,
) -> None:
    tpex_stock = stock_record(
        "TW:6488",
        market="TW",
        symbol="6488",
        name="環球晶",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="GlobalWafers",
        name_zh_tw="環球晶",
    )
    tpex_quote = quote_record(
        "TW:6488",
        last_price=480.0,
        change=1.5,
        change_percent=1.5,
        previous_close=478.5,
        timestamp="2026-05-12T10:00:00+08:00",
        is_market_open=True,
        volume=654321,
    )

    def get_stock(stock_id: str):
        return tpex_stock if stock_id == "TW:6488" else None

    def get_quotes(stock_ids: list[str]):
        if "TW:6488" in stock_ids:
            return [(tpex_stock, tpex_quote)]
        return []

    monkeypatch.setattr(quote_service, "get_stock", get_stock)
    monkeypatch.setattr(quote_service, "get_quotes", get_quotes)
    monkeypatch.setattr(
        rankings_route.rankings_service,
        "_fetch_tw_institutional_snapshot",
        lambda now, tw_board="TWSE": (
            date(2026, 5, 11),
            {"TW:6488": net_buy_sell_shares},
        ),
    )


def test_rankings_endpoint_returns_response_shape(monkeypatch, client) -> None:
    monkeypatch.setattr(
        rankings_route.rankings_service,
        "get_rankings",
        lambda market, ranking_type, limit, tw_board=None: _sample_response(),
    )

    response = client.get("/api/rankings", params={"market": "TW", "type": "volume", "limit": 1})

    assert response.status_code == 200
    assert response.json() == {
        "market": "TW",
        "rankingType": "volume",
        "twBoard": "TWSE",
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

    def fake_get_rankings(market, ranking_type, limit, tw_board=None):
        captured["market"] = market
        captured["ranking_type"] = ranking_type
        captured["limit"] = limit
        captured["tw_board"] = tw_board
        return _sample_response()

    monkeypatch.setattr(rankings_route.rankings_service, "get_rankings", fake_get_rankings)

    response = client.get("/api/rankings", params={"market": "TW", "type": "volume", "limit": 7})

    assert response.status_code == 200
    assert captured == {
        "market": "TW",
        "ranking_type": RankingType.volume,
        "limit": 7,
        "tw_board": None,
    }


@pytest.mark.parametrize(
    ("ranking_type", "net_buy_sell_shares"),
    [
        ("net_buy", 500000),
        ("net_sell", -500000),
    ],
)
def test_rankings_endpoint_returns_tpex_institutional_response_shape(
    monkeypatch,
    client,
    ranking_type: str,
    net_buy_sell_shares: int,
) -> None:
    _install_tpex_institutional_fixtures(
        monkeypatch,
        net_buy_sell_shares=net_buy_sell_shares,
    )

    response = client.get(
        "/api/rankings",
        params={"market": "TW", "type": ranking_type, "limit": 50, "twBoard": "TPEx"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "TW"
    assert payload["rankingType"] == ranking_type
    assert payload["twBoard"] == "TPEx"
    assert payload["items"][0]["stock"]["symbol"] == "6488"
    assert payload["items"][0]["stock"]["exchange"] == "TPEx"
    assert payload["items"][0]["netBuySellShares"] == net_buy_sell_shares
    assert payload["items"][0]["lastPrice"] == 480.0
    assert payload["source"] == "TPEx institutional daily data"
    assert payload["sourceType"] == "free-public"
    assert "TPEx daily institutional ranking" in payload["dataFreshnessLabel"]
    assert payload["warningMessage"] == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
    assert payload["unsupportedReason"] is None
    assert payload["usRankingsScope"] is None
    assert isinstance(payload["timestamp"], str) and payload["timestamp"]
    assert isinstance(payload["isMarketOpen"], bool)


@pytest.mark.parametrize("ranking_type", ["net_buy", "net_sell"])
def test_rankings_endpoint_returns_tpex_institutional_unavailable_shape(
    monkeypatch,
    client,
    ranking_type: str,
) -> None:
    monkeypatch.setattr(
        rankings_route.rankings_service,
        "_fetch_tw_institutional_snapshot",
        lambda now, tw_board="TWSE": None,
    )

    response = client.get(
        "/api/rankings",
        params={"market": "TW", "type": ranking_type, "limit": 50, "twBoard": "TPEx"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "TW"
    assert payload["rankingType"] == ranking_type
    assert payload["twBoard"] == "TPEx"
    assert payload["items"] == []
    assert payload["source"] == "TPEx institutional daily data"
    assert payload["sourceType"] == "unsupported"
    assert (
        payload["dataFreshnessLabel"]
        == "TPEx institutional daily ranking is unavailable from the free public source right now."
    )
    assert payload["warningMessage"] == "上櫃（TPEx）三大法人買賣超為日資料，非盤中即時資料。"
    assert (
        payload["unsupportedReason"]
        == "TPEx institutional daily data is temporarily unavailable from the free public source."
    )
    assert payload["usRankingsScope"] is None


def test_rankings_endpoint_validates_market(client) -> None:
    response = client.get("/api/rankings", params={"market": "JP", "type": "volume", "limit": 5})

    assert response.status_code == 422


def test_rankings_endpoint_validates_ranking_type(client) -> None:
    response = client.get("/api/rankings", params={"market": "TW", "type": "turnover", "limit": 5})

    assert response.status_code == 422
