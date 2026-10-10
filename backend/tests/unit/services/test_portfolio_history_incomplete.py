"""Portfolio history must not store invented valuations (#265)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.portfolio_history_service import PortfolioHistoryService


def _stock(stock_id: str = "US:AAPL") -> StockRecord:
    return StockRecord(
        id=stock_id,
        market="US",
        symbol=stock_id.split(":", 1)[1],
        name="Apple",
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


def _quote(stock_id: str = "US:AAPL") -> QuoteRecord:
    return QuoteRecord(
        id=stock_id,
        last_price=100.0,
        change=1.0,
        change_percent=1.0,
        previous_close=99.0,
        timestamp="2026-10-07T15:00:00-04:00",
        is_market_open=True,
    )


@pytest.fixture
def service(monkeypatch) -> PortfolioHistoryService:
    svc = PortfolioHistoryService()
    monkeypatch.setattr(
        "app.services.portfolio_history_service.portfolio_service.list_positions",
        lambda _user_id: [
            SimpleNamespace(stock_id="US:AAPL", quantity=2.0, average_cost=90.0),
        ],
    )
    monkeypatch.setattr(
        "app.services.portfolio_history_service.portfolio_service.list_assets",
        lambda _user_id: [],
    )
    monkeypatch.setattr(
        "app.services.portfolio_history_service.quote_service.get_stock",
        lambda stock_id: _stock(stock_id),
    )
    return svc


def test_snapshot_metrics_skip_fallback_fx(service, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.portfolio_history_service.get_usd_twd_fx_snapshot",
        lambda: {"rate": 32.0, "status": "fallback", "timestamp": "t"},
    )
    monkeypatch.setattr(service, "_resolve_quote", lambda _position: _quote())
    assert service._build_snapshot_metrics("u1") is None


def test_snapshot_metrics_skip_unknown_stock(service, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.portfolio_history_service.get_usd_twd_fx_snapshot",
        lambda: {"rate": 32.0, "status": "ok", "timestamp": "t"},
    )
    monkeypatch.setattr(
        "app.services.portfolio_history_service.quote_service.get_stock",
        lambda _stock_id: None,
    )
    monkeypatch.setattr(service, "_resolve_quote", lambda _position: _quote())
    assert service._build_snapshot_metrics("u1") is None


def test_snapshot_metrics_skip_missing_quote(service, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.portfolio_history_service.get_usd_twd_fx_snapshot",
        lambda: {"rate": 32.0, "status": "ok", "timestamp": "t"},
    )
    monkeypatch.setattr(service, "_resolve_quote", lambda _position: None)
    assert service._build_snapshot_metrics("u1") is None


def test_snapshot_metrics_ok_with_quote_and_live_fx(service, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.portfolio_history_service.get_usd_twd_fx_snapshot",
        lambda: {"rate": 32.0, "status": "ok", "timestamp": "t"},
    )
    monkeypatch.setattr(service, "_resolve_quote", lambda _position: _quote())
    metrics = service._build_snapshot_metrics("u1")
    assert metrics is not None
    assert metrics.holdings_value_usd == pytest.approx(200.0)


def test_capture_skips_write_when_incomplete(service, monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "app.services.portfolio_history_service.portfolio_history_repository.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.portfolio_history_service.iter_open_snapshot_windows",
        lambda _now=None: [("US", datetime(2026, 10, 7, 19, 0, tzinfo=timezone.utc))],
    )
    monkeypatch.setattr(service, "_build_snapshot_metrics", lambda _user_id: None)
    monkeypatch.setattr(
        "app.services.portfolio_history_service.portfolio_history_repository.upsert_snapshots",
        lambda **kwargs: calls.append(kwargs) or 1,
    )
    assert service.capture_due_snapshots_for_user("u1") == 0
    assert calls == []
