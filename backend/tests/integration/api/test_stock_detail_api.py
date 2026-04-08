import pytest

from app.api.routes import history as history_routes
from app.services.news_service import NewsItem, news_service
from app.services.quote_service import quote_service
from tests.support import candle_record, stock_record


pytestmark = pytest.mark.integration


def test_history_endpoint_returns_quote_history_for_requested_range(monkeypatch, client) -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
        name_en="TSMC",
        name_zh_tw="台積電",
    )
    candles = [
        candle_record(
            time="2026-04-01T09:00:00+08:00",
            open_price=950.0,
            high=955.0,
            low=948.0,
            close=952.0,
            volume=1_000,
        ),
        candle_record(
            time="2026-04-02T09:00:00+08:00",
            open_price=952.0,
            high=960.0,
            low=951.0,
            close=958.0,
            volume=2_000,
        ),
    ]
    monkeypatch.setattr(quote_service, "get_history", lambda stock_id, range_name: (stock, candles))

    response = client.get("/api/stocks/TW:2330/history", params={"range": "1W"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stock"]["id"] == "TW:2330"
    assert payload["range"] == "1W"
    assert len(payload["candles"]) == 2
    assert payload["chartLayout"]["trendUp"] is True


def test_key_stats_and_news_endpoints_return_safe_payloads(monkeypatch, client) -> None:
    monkeypatch.setattr(
        history_routes,
        "get_key_statistics",
        lambda stock_id: {
            "open": 950.0,
            "previousClose": 945.0,
            "volume": 123456,
            "avgVolume10d": 120000,
            "marketCap": "24.1T",
            "sharesOutstanding": "25.9B",
            "beta": 1.1,
            "peTtm": 22.4,
            "epsTtm": 42.33,
            "dividendQuarterly": 4.5,
            "dividendYieldPercent": 1.8,
            "exDividendDate": "2026-06-20",
            "dividendPayDate": "2026-07-18",
            "nextEarningsDate": "2026-07-24",
            "dayHigh": 960.0,
            "dayLow": 948.0,
            "dayRangeMarkerPercent": 50.0,
            "fiftyTwoWeekHigh": 1100.0,
            "fiftyTwoWeekLow": 720.0,
            "fiftyTwoWeekHighDate": "2025-10-15",
            "fiftyTwoWeekLowDate": "2025-04-14",
            "week52RangeMarkerPercent": 60.0,
        },
    )
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol="2330",
            name="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
            name_en="TSMC",
            name_zh_tw="台積電",
        ),
    )
    monkeypatch.setattr(
        news_service,
        "get_recent_news",
        lambda stock: [
            NewsItem(
                title="TSMC expands production",
                url="https://example.com/news/tsmc",
                source="Example",
                published_at="2026-04-01T10:00:00+08:00",
            )
        ],
    )

    stats_response = client.get("/api/stocks/TW:2330/key-statistics")
    news_response = client.get("/api/stocks/TW:2330/news")

    assert stats_response.status_code == 200
    assert stats_response.json()["marketCap"] == "24.1T"
    assert news_response.status_code == 200
    assert news_response.json() == [
        {
            "title": "TSMC expands production",
            "url": "https://example.com/news/tsmc",
            "source": "Example",
            "publishedAt": "2026-04-01T10:00:00+08:00",
        }
    ]


def test_tw_insights_endpoint_only_allows_tw_stocks(monkeypatch, client) -> None:
    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock_record(
            stock_id,
            market="TW" if stock_id == "TW:2330" else "US",
            symbol="2330" if stock_id == "TW:2330" else "AAPL",
            name="台積電" if stock_id == "TW:2330" else "Apple Inc.",
            exchange="TWSE" if stock_id == "TW:2330" else "NASDAQ",
            currency="TWD" if stock_id == "TW:2330" else "USD",
            timezone="Asia/Taipei" if stock_id == "TW:2330" else "America/New_York",
            name_en="TSMC" if stock_id == "TW:2330" else "Apple Inc.",
            name_zh_tw="台積電" if stock_id == "TW:2330" else None,
        ),
    )
    monkeypatch.setattr(
        history_routes,
        "get_tw_stock_insights",
        lambda stock: {
            "revenue": {
                "lastUpdated": "2026-04-01T10:00:00+08:00",
                "latestRevenueK": 1000,
                "latestMomPercent": 1.2,
                "latestYoyPercent": 12.3,
                "points": [
                    {
                        "month": "2026/03",
                        "revenueK": 1000,
                        "momPercent": 1.2,
                        "yoyPercent": 12.3,
                    }
                ],
            },
            "peer": {
                "sector": "Semiconductors",
                "asOf": "2026-04-01",
                "companyCount": 42,
                "metrics": [
                    {
                        "label": "近1月",
                        "period": "1M",
                        "companyPercent": 10.1,
                        "industryAveragePercent": 6.5,
                        "rank": 2,
                        "total": 42,
                    }
                ],
                "compareUrl": None,
                "topMovers": [],
            },
            "dividends": {
                "avgCashYield2Y": 2.1,
                "years": [
                    {
                        "year": 2025,
                        "cashDividend": 4.0,
                        "stockDividend": 0.0,
                        "cashYieldPercent": 2.1,
                        "exDividendDate": "2026-06-20",
                        "payDate": "2026-07-18",
                    }
                ],
            },
        },
    )

    tw_response = client.get("/api/stocks/TW:2330/tw-insights")
    us_response = client.get("/api/stocks/US:AAPL/tw-insights")

    assert tw_response.status_code == 200
    assert tw_response.json()["peer"]["companyCount"] == 42
    assert us_response.status_code == 404
    assert us_response.json() == {
        "detail": "Taiwan insights are only available for TW stocks"
    }
