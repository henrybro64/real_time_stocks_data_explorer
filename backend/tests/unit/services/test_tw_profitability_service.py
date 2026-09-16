from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.stock import StockRecord
from app.services.tw_profitability_service import (
    TwProfitabilityService,
    build_profitability_points,
    fiscal_quarter_label,
    margin_percent,
)


class _FakeQuoteSource:
    def __init__(self, stock: StockRecord | None) -> None:
        self._stock = stock

    def get_stock(self, stock_id: str) -> StockRecord | None:
        if self._stock is None:
            return None
        return self._stock if stock_id == self._stock.id else None


def _tw_stock() -> StockRecord:
    return StockRecord(
        id="TW:2330",
        symbol="2330",
        name="台積電",
        market="TW",
        currency="TWD",
        exchange="TWSE",
        timezone="Asia/Taipei",
    )


def test_margin_percent_formulas() -> None:
    assert margin_percent(54.12, 100) == pytest.approx(54.12)
    assert margin_percent(42.3, 100) == pytest.approx(42.3)
    assert margin_percent(None, 100) is None
    assert margin_percent(10, None) is None
    assert margin_percent(10, 0) is None


def test_build_points_from_yfinance_style_mapping() -> None:
    statement = {
        "Total Revenue": {
            "2025-03-31": 100.0,
            "2025-06-30": 200.0,
            "2025-09-30": 150.0,
        },
        "Gross Profit": {
            "2025-03-31": 50.0,
            "2025-06-30": 80.0,
            "2025-09-30": 60.0,
        },
        "Operating Income": {
            "2025-03-31": 40.0,
            "2025-06-30": 70.0,
            "2025-09-30": 45.0,
        },
        "Pretax Income": {
            "2025-03-31": 38.0,
            "2025-06-30": 65.0,
            "2025-09-30": 44.0,
        },
        "Net Income": {
            "2025-03-31": 30.0,
            "2025-06-30": 50.0,
            "2025-09-30": 40.0,
        },
    }
    points = build_profitability_points(statement, quarters=12)
    assert [point.fiscal_quarter for point in points] == ["2025Q1", "2025Q2", "2025Q3"]
    assert points[0].gross_margin_percent == pytest.approx(50.0)
    assert points[0].operating_margin_percent == pytest.approx(40.0)
    assert points[0].pretax_margin_percent == pytest.approx(38.0)
    assert points[0].net_margin_percent == pytest.approx(30.0)
    assert points[0].quarter_end_close is None


def test_alias_priority_prefers_operating_income_and_net_income() -> None:
    statement = {
        "Total Revenue": {"2025-09-30": 100.0},
        "EBIT": {"2025-09-30": 10.0},
        "Operating Income": {"2025-09-30": 40.0},
        "Net Income Including Noncontrolling Interests": {"2025-09-30": 5.0},
        "Net Income": {"2025-09-30": 30.0},
        "Gross Profit": {"2025-09-30": 50.0},
        "Pretax Income": {"2025-09-30": 35.0},
    }
    points = build_profitability_points(statement, quarters=12)
    assert points[0].operating_margin_percent == pytest.approx(40.0)
    assert points[0].net_margin_percent == pytest.approx(30.0)

def test_ytd_cumulative_converted_to_quarterly_when_flagged() -> None:
    statement = {
        "Total Revenue": {
            "2025-03-31": 100.0,
            "2025-06-30": 250.0,
            "2025-09-30": 400.0,
        },
        "Gross Profit": {
            "2025-03-31": 40.0,
            "2025-06-30": 100.0,
            "2025-09-30": 160.0,
        },
        "Operating Income": {
            "2025-03-31": 20.0,
            "2025-06-30": 50.0,
            "2025-09-30": 80.0,
        },
        "Pretax Income": {
            "2025-03-31": 18.0,
            "2025-06-30": 45.0,
            "2025-09-30": 72.0,
        },
        "Net Income": {
            "2025-03-31": 10.0,
            "2025-06-30": 25.0,
            "2025-09-30": 40.0,
        },
    }
    points = build_profitability_points(statement, quarters=12, treat_as_ytd=True)
    # Q2 should be YTD2 - YTD1 => revenue 150, gross 60 => 40%
    assert points[1].gross_margin_percent == pytest.approx(40.0)
    assert points[2].net_margin_percent == pytest.approx(10.0)


def test_growing_quarterly_revenue_is_not_treated_as_ytd() -> None:
    """Rising QoQ revenue is common; must not auto-diff into bogus quarters."""
    statement = {
        "Total Revenue": {
            "2025-03-31": 100.0,
            "2025-06-30": 250.0,
            "2025-09-30": 400.0,
        },
        "Gross Profit": {
            "2025-03-31": 40.0,
            "2025-06-30": 100.0,
            "2025-09-30": 160.0,
        },
        "Net Income": {
            "2025-03-31": 10.0,
            "2025-06-30": 25.0,
            "2025-09-30": 40.0,
        },
    }
    points = build_profitability_points(statement, quarters=12)
    assert points[1].gross_margin_percent == pytest.approx(40.0)  # 100/250
    assert points[2].net_margin_percent == pytest.approx(10.0)  # 40/400


def test_null_gross_for_banks_and_zero_revenue() -> None:
    statement = [
        {
            "periodEnd": "2025-03-31",
            "revenue": 0,
            "gross": 10,
            "operating": 5,
            "pretax": 4,
            "net": 3,
        },
        {
            "periodEnd": "2025-06-30",
            "revenue": 100,
            "gross": None,
            "operating": 20,
            "pretax": 18,
            "net": 12,
        },
    ]
    points = build_profitability_points(statement, quarters=12)
    assert points[0].gross_margin_percent is None
    assert points[0].net_margin_percent is None
    assert points[1].gross_margin_percent is None
    assert points[1].operating_margin_percent == pytest.approx(20.0)


def test_quarters_limit() -> None:
    statement = {
        "Total Revenue": {f"202{year}-03-31": 100.0 for year in range(0, 5)},
        "Net Income": {f"202{year}-03-31": 10.0 for year in range(0, 5)},
    }
    # Fix years properly
    statement = {
        "Total Revenue": {
            "2021-03-31": 100.0,
            "2022-03-31": 100.0,
            "2023-03-31": 100.0,
            "2024-03-31": 100.0,
            "2025-03-31": 100.0,
        },
        "Net Income": {
            "2021-03-31": 10.0,
            "2022-03-31": 10.0,
            "2023-03-31": 10.0,
            "2024-03-31": 10.0,
            "2025-03-31": 10.0,
        },
    }
    points = build_profitability_points(statement, quarters=3)
    assert len(points) == 3
    assert points[0].period_end == "2023-03-31"


def test_profitability_service_cache_and_stale_fallback() -> None:
    calls = {"n": 0}
    statement = {
        "Total Revenue": {"2025-09-30": 100.0},
        "Gross Profit": {"2025-09-30": 54.12},
        "Operating Income": {"2025-09-30": 42.3},
        "Pretax Income": {"2025-09-30": 43.1},
        "Net Income": {"2025-09-30": 38.2},
    }

    def fetcher(_symbol: str):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("yfinance down")
        return statement

    now = {"value": datetime(2026, 9, 12, tzinfo=UTC)}
    service = TwProfitabilityService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        quarterly_income_fetcher=fetcher,
        now_provider=lambda: now["value"],
    )
    first = service.get_profitability("TW:2330", quarters=12)
    second = service.get_profitability("TW:2330", quarters=12)
    assert calls["n"] == 1
    assert first.points[0].gross_margin_percent == pytest.approx(54.12)
    assert second.stale is False

    from datetime import timedelta

    now["value"] = now["value"] + timedelta(hours=25)
    stale = service.get_profitability("TW:2330", quarters=12)
    assert stale.stale is True
    assert stale.source == "yahoo_finance_quarterly_income"
    assert calls["n"] == 2

    # Backoff: another request shortly after stale fallback must not re-hit yfinance.
    now["value"] = now["value"] + timedelta(minutes=5)
    again = service.get_profitability("TW:2330", quarters=12)
    assert again.stale is True
    assert calls["n"] == 2


def test_profitability_rejects_non_tw() -> None:
    us_stock = StockRecord(
        id="US:AAPL",
        symbol="AAPL",
        name="Apple",
        market="US",
        currency="USD",
        exchange="NASDAQ",
        timezone="America/New_York",
    )
    service = TwProfitabilityService(
        quote_source=_FakeQuoteSource(us_stock),
        quarterly_income_fetcher=lambda _symbol: {},
    )
    with pytest.raises(ValueError, match="only available for TW stocks"):
        service.get_profitability("US:AAPL", quarters=12)


def test_profitability_invalid_quarters() -> None:
    service = TwProfitabilityService(
        quote_source=_FakeQuoteSource(_tw_stock()),
        quarterly_income_fetcher=lambda _symbol: {},
    )
    with pytest.raises(ValueError, match="Unsupported profitability quarters"):
        service.get_profitability("TW:2330", quarters=10)


def test_negative_margins_preserved() -> None:
    statement = {
        "Total Revenue": {"2025-09-30": 100.0},
        "Gross Profit": {"2025-09-30": -5.0},
        "Operating Income": {"2025-09-30": -12.0},
        "Pretax Income": {"2025-09-30": -15.0},
        "Net Income": {"2025-09-30": -20.0},
    }
    points = build_profitability_points(statement, quarters=12)
    assert points[0].gross_margin_percent == pytest.approx(-5.0)
    assert points[0].operating_margin_percent == pytest.approx(-12.0)
    assert points[0].pretax_margin_percent == pytest.approx(-15.0)
    assert points[0].net_margin_percent == pytest.approx(-20.0)


def test_ytd_q4_uses_annual_minus_q3() -> None:
    """Q4 single-quarter = annual (or Q4 YTD) minus Q3 YTD."""
    statement = {
        "Total Revenue": {
            "2025-03-31": 100.0,
            "2025-06-30": 250.0,
            "2025-09-30": 400.0,
            "2025-12-31": 600.0,  # annual / Q4 YTD
        },
        "Gross Profit": {
            "2025-03-31": 40.0,
            "2025-06-30": 100.0,
            "2025-09-30": 160.0,
            "2025-12-31": 220.0,
        },
        "Operating Income": {
            "2025-03-31": 20.0,
            "2025-06-30": 50.0,
            "2025-09-30": 80.0,
            "2025-12-31": 110.0,
        },
        "Pretax Income": {
            "2025-03-31": 18.0,
            "2025-06-30": 45.0,
            "2025-09-30": 72.0,
            "2025-12-31": 100.0,
        },
        "Net Income": {
            "2025-03-31": 10.0,
            "2025-06-30": 25.0,
            "2025-09-30": 40.0,
            "2025-12-31": 55.0,
        },
    }
    points = build_profitability_points(statement, quarters=12, treat_as_ytd=True)
    assert [point.fiscal_quarter for point in points] == [
        "2025Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]
    # Q4 revenue 200, gross 60 => 30%
    assert points[3].gross_margin_percent == pytest.approx(30.0)
    assert points[3].net_margin_percent == pytest.approx(7.5)


def test_fiscal_quarter_label_snaps_early_boundary_dates() -> None:
    assert fiscal_quarter_label("2025-03-31") == "2025Q1"
    assert fiscal_quarter_label("2025-04-01") == "2025Q1"
    assert fiscal_quarter_label("2025-01-02") == "2024Q4"
    assert fiscal_quarter_label("2025-09-30") == "2025Q3"


def test_restated_period_prefers_later_period_end() -> None:
    statement = {
        "Total Revenue": {
            "2025-03-28": 100.0,
            "2025-03-31": 110.0,  # restatement for same Q1
        },
        "Net Income": {
            "2025-03-28": 10.0,
            "2025-03-31": 12.0,
        },
    }
    points = build_profitability_points(statement, quarters=12)
    assert len(points) == 1
    assert points[0].fiscal_quarter == "2025Q1"
    assert points[0].period_end == "2025-03-31"
    assert points[0].net_margin_percent == pytest.approx(10.91)


def test_yahoo_timeseries_payload_parses_to_statement() -> None:
    from app.services.tw_profitability_yahoo import _parse_yahoo_timeseries_payload

    payload = {
        "timeseries": {
            "result": [
                {
                    "meta": {"type": ["quarterlyTotalRevenue"]},
                    "quarterlyTotalRevenue": [
                        {
                            "asOfDate": "2025-06-30",
                            "reportedValue": {"raw": 100.0},
                        },
                        {
                            "asOfDate": "2025-09-30",
                            "reportedValue": {"raw": 200.0},
                        },
                    ],
                },
                {
                    "meta": {"type": ["quarterlyNetIncome"]},
                    "quarterlyNetIncome": [
                        {
                            "asOfDate": "2025-06-30",
                            "reportedValue": {"raw": 10.0},
                        },
                        {
                            "asOfDate": "2025-09-30",
                            "reportedValue": {"raw": 40.0},
                        },
                    ],
                },
            ]
        }
    }
    statement = _parse_yahoo_timeseries_payload(payload)
    assert statement is not None
    points = build_profitability_points(statement, quarters=12)
    assert [point.fiscal_quarter for point in points] == ["2025Q2", "2025Q3"]
    assert points[1].net_margin_percent == pytest.approx(20.0)
