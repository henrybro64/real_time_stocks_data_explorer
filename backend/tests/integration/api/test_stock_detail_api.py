import pytest
from fastapi.testclient import TestClient

from app.api.routes import history as history_routes
from app.core.config import settings
from app.main import app
from app.services import key_stats_service
from app.services.news_service import NewsItem, news_service
from app.services.quote_service import quote_service
from tests.support import quote_record
from app.services.stock_analysis_service import (
    ComputedStockAnalysisData,
    StockAnalysisResult,
)
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
    assert payload["chartLayout"]["minY"] == 948.0
    assert payload["chartLayout"]["maxY"] == 960.0
    assert payload["chartLayout"]["trendUp"] is True


@pytest.mark.parametrize(
    ("stock_id", "symbol", "exchange", "name"),
    [
        ("TW:2330", "2330", "TWSE", "台積電"),
        ("TW:6488", "6488", "TPEx", "環球晶"),
    ],
)
@pytest.mark.parametrize("range_name", ["1D", "1W", "1M", "3M", "6M", "1Y"])
def test_history_endpoint_accepts_declared_ranges_for_twse_and_tpex(
    monkeypatch,
    client,
    stock_id: str,
    symbol: str,
    exchange: str,
    name: str,
    range_name: str,
) -> None:
    stock = stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=name,
        exchange=exchange,
        currency="TWD",
        timezone="Asia/Taipei",
        name_en=name,
        name_zh_tw=name,
    )
    candles = [
        candle_record(
            time="2026-07-01T13:30:00+08:00",
            open_price=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1_000_000,
        ),
        candle_record(
            time="2026-07-02T13:30:00+08:00",
            open_price=105.0,
            high=112.0,
            low=104.0,
            close=110.0,
            volume=1_200_000,
        ),
    ]
    monkeypatch.setattr(
        quote_service,
        "get_history",
        lambda requested_id, requested_range: (stock, candles)
        if requested_id == stock_id and requested_range == range_name
        else None,
    )

    response = client.get(f"/api/stocks/{stock_id}/history", params={"range": range_name})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stock"]["id"] == stock_id
    assert payload["stock"]["exchange"] == exchange
    assert payload["range"] == range_name
    assert len(payload["candles"]) == 2
    assert payload["chartLayout"] is not None
    assert payload["chartLayout"]["minY"] == pytest.approx(95.0)
    assert payload["chartLayout"]["maxY"] == pytest.approx(112.0)


def test_history_endpoint_returns_502_when_provider_is_temporarily_unavailable(
    monkeypatch,
    client,
) -> None:
    def failing_history(stock_id: str, range_name: str):
        raise RuntimeError(
            f"Taiwan daily history temporarily unavailable for {stock_id} (TPEx)"
        )

    monkeypatch.setattr(quote_service, "get_history", failing_history)

    response = client.get("/api/stocks/TW:6488/history", params={"range": "1W"})

    assert response.status_code == 502
    assert "temporarily unavailable" in response.json()["detail"]


def test_key_statistics_endpoint_returns_twse_official_pe_via_service(monkeypatch, client) -> None:
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
    quote = quote_record(
        "TW:2330",
        last_price=950.0,
        change=5.0,
        change_percent=0.53,
        previous_close=945.0,
        timestamp="2026-07-03T13:30:00+08:00",
        is_market_open=False,
        open_price=942.0,
        high=960.0,
        low=948.0,
        volume=123_456,
    )

    monkeypatch.setattr(
        quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "TW:2330" else None,
    )
    monkeypatch.setattr(
        quote_service,
        "get_quote",
        lambda stock_id: quote if stock_id == "TW:2330" else None,
    )
    monkeypatch.setattr(key_stats_service, "_tw_from_candles", lambda current_stock, base: dict(base))
    monkeypatch.setattr(
        key_stats_service,
        "_finnhub_enrich",
        lambda payload, current_stock: dict(payload),
    )
    monkeypatch.setattr(
        key_stats_service,
        "_fetch_twse_daily_pe_snapshot",
        lambda target_day: (
            {"2330": {"peRatio": 22.4, "peStatus": None, "priceUsed": 950.0}},
            "2026-07-03",
        ),
    )

    response = client.get("/api/stocks/TW:2330/key-statistics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["peRatio"] == 22.4
    assert payload["peTtm"] == 22.4
    assert payload["peRatioType"] == "official_daily"
    assert payload["peRatioSource"] == "TWSE official daily P/E"
    assert payload["priceUsed"] == 950.0
    assert payload["priceAsOf"] == "2026-07-03"
    assert payload["valuationAsOf"] == "2026-07-03"
    assert payload["valuationWarning"] is None
    assert payload["lastPrice"] == 950.0


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
            "peRatio": 22.4,
            "peRatioType": "official_daily",
            "peRatioSource": "TWSE official daily P/E",
            "priceUsed": 950.0,
            "priceAsOf": "2026-07-03",
            "peTtm": 22.4,
            "epsTtm": 42.33,
            "epsAsOf": None,
            "valuationAsOf": "2026-07-03",
            "valuationWarning": None,
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


def test_analysis_endpoint_returns_computed_metrics_when_ai_is_unavailable(
    monkeypatch,
    client,
) -> None:
    def fake_analyze(
        stock_id: str,
        *,
        provider_override=None,
        run_ai=True,
    ):
        assert run_ai is False
        return StockAnalysisResult(
            stock_id=stock_id,
            ticker="AAPL",
            computed_data=ComputedStockAnalysisData(
                ticker="AAPL",
                company_name="Apple Inc.",
                currency="USD",
                sector="Technology",
                industry="Consumer Electronics",
                market_cap=3_000_000_000_000,
                trailing_pe=29.4,
                forward_pe=27.1,
                current_price=210.5,
                return_1m_percent=4.2,
                return_3m_percent=8.7,
                return_6m_percent=12.4,
                return_1y_percent=22.1,
                sma_50=205.2,
                sma_200=188.4,
                price_vs_sma_50_percent=2.58,
                price_vs_sma_200_percent=11.73,
                rsi_14=61.4,
                volatility_30d_annualized=24.6,
                max_drawdown_1y_percent=-14.8,
                average_volume_30d=75_000_000,
            ),
            ai_provider="openai",
            ai_model="gpt-4o-mini",
            ai_analysis=None,
            warnings=(),
            errors=(),
        )

    monkeypatch.setattr(history_routes.stock_analysis_service, "analyze_stock", fake_analyze)

    response = client.get("/api/stocks/US:AAPL/analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["stockId"] == "US:AAPL"
    assert payload["ticker"] == "AAPL"
    assert payload["computedData"]["currentPrice"] == 210.5
    assert payload["computedData"]["averageVolume30d"] == 75_000_000
    assert payload["aiAnalysis"] is None
    assert payload["warnings"] == []


def test_analysis_endpoint_rejects_anonymous_when_auth_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "local_auth_enabled", True)

    with TestClient(app) as unauthenticated_client:
        response = unauthenticated_client.get("/api/stocks/US:AAPL/analysis")

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


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


def test_institutional_investors_endpoint_returns_lots_summary_and_history(monkeypatch, client) -> None:
    from app.api.routes import tw_institutional as institutional_routes
    from app.services.tw_institutional_service import (
        TaiwanInstitutionalInvestorRecord,
        TaiwanInstitutionalInvestorsResult,
    )

    record = TaiwanInstitutionalInvestorRecord(
        date="2026-07-09",
        symbol="2330",
        name="台積電",
        foreign_net_shares=12_345_000,
        investment_trust_net_shares=1_200_000,
        dealer_self_net_shares=-500_000,
        dealer_hedge_net_shares=-300_000,
        dealer_net_shares=-800_000,
        total_net_shares=12_745_000,
    )
    result = TaiwanInstitutionalInvestorsResult(
        stock_id="TW:2330",
        symbol="2330",
        market="TWSE",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TWSE T86 daily institutional data",
    )

    monkeypatch.setattr(
        institutional_routes.tw_institutional_service,
        "get_institutional_investors",
        lambda stock_id, *, days: result,
    )

    response = client.get("/api/stocks/TW:2330/institutional-investors", params={"days": 90})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stockId"] == "TW:2330"
    assert payload["symbol"] == "2330"
    assert payload["market"] == "TWSE"
    assert payload["latestDate"] == "2026-07-09"
    assert payload["sharesPerLot"] == 1000
    assert payload["summary"] == {
        "foreignNetLots": 12345.0,
        "investmentTrustNetLots": 1200.0,
        "dealerNetLots": -800.0,
        "totalNetLots": 12745.0,
        "dealerSelfNetLots": -500.0,
        "dealerHedgeNetLots": -300.0,
    }
    assert payload["history"][0]["date"] == "2026-07-09"
    assert payload["source"] == "TWSE T86 daily institutional data"


def test_institutional_investors_endpoint_applies_100_unit_lots_for_fx_etf(
    monkeypatch,
    client,
) -> None:
    from app.api.routes import tw_institutional as institutional_routes
    from app.services.tw_institutional_service import (
        TaiwanInstitutionalInvestorRecord,
        TaiwanInstitutionalInvestorsResult,
    )

    record = TaiwanInstitutionalInvestorRecord(
        date="2026-07-09",
        symbol="00677K",
        name="富邦VIX",
        foreign_net_shares=1_000,
        investment_trust_net_shares=0,
        dealer_self_net_shares=0,
        dealer_hedge_net_shares=0,
        dealer_net_shares=0,
        total_net_shares=1_000,
    )
    result = TaiwanInstitutionalInvestorsResult(
        stock_id="TW:00677K",
        symbol="00677K",
        market="TWSE",
        latest_date="2026-07-09",
        summary=record,
        history=(record,),
        source="TWSE T86 daily institutional data",
    )

    monkeypatch.setattr(
        institutional_routes.tw_institutional_service,
        "get_institutional_investors",
        lambda stock_id, *, days: result,
    )

    response = client.get("/api/stocks/TW:00677K/institutional-investors", params={"days": 90})

    assert response.status_code == 200
    payload = response.json()
    assert payload["sharesPerLot"] == 100
    # 1000 shares / 100 = 10 張 (not 1.0 from a hardcoded /1000).
    assert payload["summary"]["foreignNetLots"] == pytest.approx(10.0)
    assert payload["summary"]["totalNetLots"] == pytest.approx(10.0)
    assert payload["history"][0]["foreignNetLots"] == pytest.approx(10.0)


def test_institutional_investors_endpoint_rejects_non_taiwan_stocks(monkeypatch, client) -> None:
    from app.api.routes import tw_institutional as institutional_routes

    def raise_non_tw(stock_id: str, *, days: int):
        raise ValueError("Taiwan institutional data is only available for TW stocks")

    monkeypatch.setattr(
        institutional_routes.tw_institutional_service,
        "get_institutional_investors",
        raise_non_tw,
    )

    response = client.get("/api/stocks/US:AAPL/institutional-investors")

    assert response.status_code == 404
    assert response.json()["detail"] == "Taiwan institutional data is only available for TW stocks"


def test_institutional_investors_endpoint_serves_from_persisted_store(
    monkeypatch,
    client,
) -> None:
    from datetime import UTC, datetime

    from app.api.routes import tw_institutional as institutional_routes
    from app.repositories.taiwan_market_daily_repository import (
        taiwan_institutional_daily_repository,
    )
    from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord
    from tests.support import stock_record

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

    class FakeQuoteSource:
        def get_stock(self, stock_id: str):
            return stock if stock.id == stock_id else None

        def list_stocks(self):
            return [stock]

    live_calls: list = []

    taiwan_institutional_daily_repository.upsert_records(
        market="TWSE",
        source="TWSE T86 daily institutional data",
        records=[
            TaiwanInstitutionalInvestorRecord(
                date="2026-07-08",
                symbol="2330",
                name="台積電",
                foreign_net_shares=1_000_000,
                investment_trust_net_shares=2_000_000,
                dealer_self_net_shares=100_000,
                dealer_hedge_net_shares=200_000,
                dealer_net_shares=300_000,
                total_net_shares=3_300_000,
            ),
            TaiwanInstitutionalInvestorRecord(
                date="2026-07-07",
                symbol="2330",
                name="台積電",
                foreign_net_shares=4_000_000,
                investment_trust_net_shares=5_000_000,
                dealer_self_net_shares=100_000,
                dealer_hedge_net_shares=500_000,
                dealer_net_shares=600_000,
                total_net_shares=9_600_000,
            ),
        ],
    )

    service = institutional_routes.tw_institutional_service
    service._result_cache.clear()
    monkeypatch.setattr(service, "_quote_source", FakeQuoteSource())
    monkeypatch.setattr(
        service,
        "_twse_fetcher",
        lambda target_date: live_calls.append(target_date) or {"stat": "查無資料"},
    )
    monkeypatch.setattr(
        service,
        "_now_provider",
        lambda: datetime(2026, 7, 10, 1, 0, tzinfo=UTC),
    )

    response = client.get("/api/stocks/TW:2330/institutional-investors", params={"days": 90})

    assert response.status_code == 200
    payload = response.json()
    assert payload["market"] == "TWSE"
    assert payload["latestDate"] == "2026-07-08"
    assert [point["date"] for point in payload["history"]] == ["2026-07-07", "2026-07-08"]
    assert payload["summary"]["foreignNetLots"] == pytest.approx(1000.0)
    assert payload["summary"]["totalNetLots"] == pytest.approx(3300.0)
    assert payload["sharesPerLot"] == 1000
    assert payload["source"].startswith("Persisted ")
    # Fresh store-backed reads must not probe official full-market payloads.
    assert live_calls == []
