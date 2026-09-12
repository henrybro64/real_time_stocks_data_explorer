from app.services.stock_analysis_fundamental import (
    build_fundamental_outlook,
    classify_monthly_revenue_direction,
)
from tests.support import stock_record


def test_classify_monthly_revenue_direction_cases() -> None:
    assert classify_monthly_revenue_direction([1.0, 2.0, 3.0]) == "improving"
    assert classify_monthly_revenue_direction([-1.0, -2.0, -0.5]) == "weakening"
    assert classify_monthly_revenue_direction([1.0, -1.0, 0.0]) == "mixed"
    assert classify_monthly_revenue_direction([1.0]) == "insufficient"


def test_build_fundamental_outlook_us_preserves_estimates_and_labels() -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    payload = build_fundamental_outlook(
        stock,
        {
            "trailingPE": 30.0,
            "forwardPE": 25.0,
            "trailingEps": 6.1,
            "forwardEps": 7.2,
            "revenueGrowth": 0.08,
            "earningsGrowth": 0.12,
            "targetMeanPrice": 200.0,
            "recommendationKey": "buy",
        },
    )

    assert payload["availability"] == "ok"
    assert payload["trailingEps"] == 6.1
    assert payload["forwardEps"] == 7.2
    assert payload["analystConsensus"]["label"] == "market_consensus_context_not_intrinsic_value"
    assert payload["analystConsensus"]["companyGuidance"] is None
    assert payload["analystConsensus"]["targetMeanPrice"] == 200.0
    assert payload["growthKind"] == "historical_reported_yoy"
    assert payload["revenueGrowthYoy"] == 0.08
    assert payload["earningsGrowthYoy"] == 0.12
    assert "revenueGrowthEstimate" not in payload
    assert "earningsGrowthEstimate" not in payload
    assert "forwardPeBelowTrailingPe" in payload["positiveFactors"]
    assert "positiveRevenueGrowthYoy" in payload["positiveFactors"]
    assert payload["revenueTrend"] is None


def test_build_fundamental_outlook_missing_us_estimates_stay_absent() -> None:
    stock = stock_record(
        "US:AAPL",
        market="US",
        symbol="AAPL",
        name="Apple Inc.",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )
    payload = build_fundamental_outlook(stock, {})
    assert payload["availability"] == "insufficient"
    assert payload["trailingEps"] is None
    assert payload["forwardEps"] is None
    assert "trailingEps" in payload["missingInputs"]
    assert payload["analystConsensus"]["targetMeanPrice"] is None


def test_build_fundamental_outlook_tw_revenue_trend_and_soft_fail() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    def fake_insights(_stock):
        return {
            "revenue": {
                "latestRevenueK": 1000,
                "latestMomPercent": 2.0,
                "latestYoyPercent": 10.0,
                "points": [
                    {"month": "2026/01", "revenueK": 900, "momPercent": 1.0, "yoyPercent": 8.0},
                    {"month": "2026/02", "revenueK": 950, "momPercent": 2.0, "yoyPercent": 9.0},
                    {"month": "2026/03", "revenueK": 1000, "momPercent": 3.0, "yoyPercent": 10.0},
                ],
            },
            "dividends": {"avgCashYield2Y": 1.5, "years": [{}, {}]},
            "peer": {"sector": "Semi", "asOf": "2026-03-01", "companyCount": 10},
        }

    payload = build_fundamental_outlook(
        stock,
        {"trailingPE": 20.0, "forwardPE": 18.0},
        tw_insights_loader=fake_insights,
    )
    assert payload["revenueTrend"]["yoyDirection"] == "improving"
    assert payload["revenueTrend"]["momDirection"] == "improving"
    assert "monthlyRevenueYoyImproving" in payload["positiveFactors"]
    assert payload["dividendSummary"]["avgCashYield2Y"] == 1.5

    failed = build_fundamental_outlook(
        stock,
        {"trailingPE": 20.0},
        tw_insights_loader=lambda _stock: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert "twInsights" in failed["missingInputs"]
    assert failed["twInsightsUnavailableReason"] == "tw_insights_failed:RuntimeError"
    assert failed["trailingPe"] == 20.0


def test_placeholder_tw_insights_never_reach_the_payload() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    # No loader injected, so the real (still synthetic) TW insights source applies.
    payload = build_fundamental_outlook(stock, {"trailingPE": 20.0, "forwardPE": 18.0})

    assert payload["revenueTrend"] is None
    assert payload["dividendSummary"] is None
    assert payload["peerContext"] is None
    assert payload["sources"] == ["yfinance.info"]
    assert payload["twInsightsUnavailableReason"] == "tw_insights_source_is_placeholder_data"
    for key in ("monthlyRevenueTrend", "dividendTrend", "peerContext"):
        assert key in payload["missingInputs"]
    assert not any("Revenue" in factor for factor in payload["positiveFactors"])
    # Fundamental gaps must not block the rest of the payload.
    assert payload["trailingPe"] == 20.0
