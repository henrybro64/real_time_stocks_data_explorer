import pytest

from app.services.providers import us_provider
from app.services.quote_service import QuoteBatchItem, quote_service
from app.services.search_service import search_service
from tests.support import quote_record, stock_record


pytestmark = pytest.mark.integration


def test_search_endpoint_returns_us_and_tw_results(monkeypatch, client) -> None:
    monkeypatch.setattr(
        search_service,
        "search",
        lambda query: [
            stock_record(
                "US:AAPL",
                market="US",
                symbol="AAPL",
                name="Apple Inc.",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                name_en="Apple Inc.",
            ),
            stock_record(
                "TW:2330",
                market="TW",
                symbol="2330",
                name="台積電",
                exchange="TWSE",
                currency="TWD",
                timezone="Asia/Taipei",
                name_en="TSMC",
                name_zh_tw="台積電",
            ),
        ],
    )

    response = client.get("/api/search", params={"q": "a", "limit": 10})

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "US:AAPL",
            "market": "US",
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASDAQ",
            "currency": "USD",
            "timezone": "America/New_York",
            "nameEn": "Apple Inc.",
            "nameZhTw": None,
        },
        {
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
    ]


def test_quotes_endpoint_returns_stock_and_quote_payload(monkeypatch, client) -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        name_en="Apple Inc.",
    )
    quote = quote_record(
        "US:AAPL",
        last_price=187.23,
        change=1.5,
        change_percent=0.81,
        previous_close=185.73,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
        open_price=186.1,
        high=188.0,
        low=185.2,
        volume=1_250_000,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quotes_cache_first",
        lambda ids: [
            QuoteBatchItem(
                stock=stock,
                quote=quote,
                cached_at="2026-04-01T13:30:00Z",
                age_seconds=5.0,
                stale=False,
                source="memory",
                refresh_status="idle",
            )
        ],
    )

    response = client.get("/api/quotes", params={"ids": "US:AAPL"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "stock": {
                "id": "US:AAPL",
                "market": "US",
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "exchange": "NASDAQ",
                "currency": "USD",
                "timezone": "America/New_York",
                "nameEn": "Apple Inc.",
                "nameZhTw": None,
            },
            "quote": {
                "id": "US:AAPL",
                "lastPrice": 187.23,
                "change": 1.5,
                "changePercent": 0.81,
                "previousClose": 185.73,
                "timestamp": "2026-04-01T09:30:00-04:00",
                "isMarketOpen": True,
                "open": 186.1,
                "high": 188.0,
                "low": 185.2,
                "volume": 1_250_000,
            },
            "freshness": {
                "cachedAt": "2026-04-01T13:30:00Z",
                "ageSeconds": 5.0,
                "stale": False,
                "source": "memory",
                "refreshStatus": "idle",
            },
        }
    ]


def test_search_endpoint_resolves_us_etf_missing_from_catalog(monkeypatch, client) -> None:
    monkeypatch.setattr(us_provider.USProvider, "_catalog_cache", None)
    monkeypatch.setattr(us_provider.USProvider, "_catalog_cached_at", None)
    monkeypatch.setattr(
        us_provider,
        "_load_catalog_payload",
        lambda: {
            "fields": ["ticker", "name", "exchange"],
            "data": [],
        },
    )

    def fake_load_json_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        del headers
        assert "/IBAT?" in url
        return {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": "IBAT",
                            "longName": "iShares Energy Storage & Materials ETF",
                            "fullExchangeName": "NYSEArca",
                            "exchangeTimezoneName": "America/New_York",
                            "currency": "USD",
                            "quoteType": "ETF",
                            "regularMarketPrice": 91.23,
                        }
                    }
                ],
                "error": None,
            }
        }

    monkeypatch.setattr(us_provider, "_load_json_get", fake_load_json_get)

    response = client.get("/api/search", params={"q": "IBAT", "limit": 10})

    assert response.status_code == 200
    assert response.json()[0] == {
        "id": "US:IBAT",
        "market": "US",
        "symbol": "IBAT",
        "name": "iShares Energy Storage & Materials ETF",
        "exchange": "AMEX",
        "currency": "USD",
        "timezone": "America/New_York",
        "nameEn": "iShares Energy Storage & Materials ETF",
        "nameZhTw": None,
    }


def test_single_quote_endpoint_rejects_unknown_stock_safely(monkeypatch, client) -> None:
    monkeypatch.setattr(quote_service, "get_quote_detail", lambda stock_id: None)

    response = client.get("/api/quotes/INVALID:404")

    assert response.status_code == 404
    assert response.json() == {"detail": "Stock not found"}
