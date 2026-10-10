"""Converted portfolio totals stay empty when the FX rate is a fallback."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.routes import portfolio as portfolio_routes
from tests.support import quote_record, stock_record


def _usd_twd_books():
    positions = [
        SimpleNamespace(stock_id="US:AAPL", quantity=2, average_cost=180),
        SimpleNamespace(stock_id="TW:2330", quantity=2, average_cost=900),
    ]
    stocks = {
        "US:AAPL": stock_record(
            "US:AAPL",
            market="US",
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            currency="USD",
            timezone="America/New_York",
            name_en="Apple Inc.",
        ),
        "TW:2330": stock_record(
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
    }
    quotes = {
        "US:AAPL": quote_record(
            "US:AAPL",
            last_price=200.0,
            change=1.0,
            change_percent=0.5,
            previous_close=199.0,
            timestamp="2026-04-01T09:30:00-04:00",
            is_market_open=True,
        ),
        "TW:2330": quote_record(
            "TW:2330",
            last_price=950.0,
            change=5.0,
            change_percent=0.53,
            previous_close=945.0,
            timestamp="2026-04-01T09:00:00+08:00",
            is_market_open=True,
        ),
    }
    return positions, stocks, quotes


def test_summary_omits_cross_currency_totals_when_fx_is_fallback(monkeypatch) -> None:
    positions, stocks, quotes = _usd_twd_books()
    monkeypatch.setattr(portfolio_routes.portfolio_service, "list_positions", lambda _user_id: positions)
    monkeypatch.setattr(portfolio_routes.quote_service, "get_stock", lambda stock_id: stocks.get(stock_id))
    monkeypatch.setattr(portfolio_routes.quote_service, "get_quote", lambda stock_id: quotes.get(stock_id))
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
            "status": "fallback",
        },
    )

    summary = portfolio_routes.get_portfolio_summary("user-1")

    assert summary.totalUsd == 400.0
    assert summary.totalTwd == 1900.0
    assert summary.combinedUsd is None
    assert summary.combinedTwd is None
    assert summary.combinedGainUsd is None
    assert summary.combinedGainPercent is None
    assert summary.usdTwdRate is None
    assert summary.fxStatus == "fallback"
    assert summary.incomplete is True
    assert [row.allocationPercent for row in summary.positions] == [None, None]


def test_asset_summary_omits_cross_currency_totals_when_fx_is_fallback(monkeypatch) -> None:
    positions, stocks, quotes = _usd_twd_books()
    monkeypatch.setattr(portfolio_routes.portfolio_service, "list_positions", lambda _user_id: positions)
    monkeypatch.setattr(portfolio_routes.portfolio_service, "list_assets", lambda _user_id: [])
    monkeypatch.setattr(portfolio_routes.quote_service, "get_stock", lambda stock_id: stocks.get(stock_id))
    monkeypatch.setattr(portfolio_routes.quote_service, "get_quote", lambda stock_id: quotes.get(stock_id))
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
            "status": "fallback",
        },
    )

    summary = portfolio_routes.get_portfolio_asset_summary("user-1")

    assert summary.totalUsd == 400.0
    assert summary.totalTwd == 1900.0
    assert summary.combinedUsd is None
    assert summary.combinedTwd is None
    assert summary.liquidCombinedUsd is None
    assert summary.liquidCombinedTwd is None
    assert summary.usdTwdRate is None


def test_summary_keeps_same_currency_total_when_fx_is_fallback(monkeypatch) -> None:
    position = SimpleNamespace(stock_id="US:AAPL", quantity=2, average_cost=180)
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
        last_price=200.0,
        change=1.0,
        change_percent=0.5,
        previous_close=199.0,
        timestamp="2026-04-01T09:30:00-04:00",
        is_market_open=True,
    )
    monkeypatch.setattr(portfolio_routes.portfolio_service, "list_positions", lambda _user_id: [position])
    monkeypatch.setattr(
        portfolio_routes.quote_service,
        "get_stock",
        lambda stock_id: stock if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        portfolio_routes.quote_service,
        "get_quote",
        lambda stock_id: quote if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(
        portfolio_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.0,
            "timestamp": "2026-04-01T01:00:00Z",
            "status": "fallback",
        },
    )

    summary = portfolio_routes.get_portfolio_summary("user-1")

    assert summary.combinedUsd == 400.0
    assert summary.combinedTwd is None
    assert summary.combinedGainUsd == 40.0
    assert summary.combinedGainTwd is None
    assert summary.usdTwdRate is None
    assert summary.positions[0].allocationPercent == 100.0
