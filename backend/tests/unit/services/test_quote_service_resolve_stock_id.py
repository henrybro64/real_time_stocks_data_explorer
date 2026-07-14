from __future__ import annotations

import pytest

from app.models.stock import StockRecord
from app.services.quote_service import QuoteService


def _stock(*, stock_id: str, symbol: str, market: str) -> StockRecord:
    return StockRecord(
        id=stock_id,
        market=market,
        symbol=symbol,
        name=symbol,
        exchange="TEST",
        currency="USD" if market == "US" else "TWD",
        timezone="UTC",
        name_en=symbol,
        name_zh_tw=symbol,
    )


def test_resolve_stock_id_accepts_canonical_and_bare_us_tickers(monkeypatch) -> None:
    service = QuoteService()
    apple = _stock(stock_id="US:AAPL", symbol="AAPL", market="US")

    monkeypatch.setattr(
        service,
        "get_stock",
        lambda stock_id: apple if stock_id == "US:AAPL" else None,
    )
    monkeypatch.setattr(service, "search", lambda _query: [])

    assert service.resolve_stock_id("US:AAPL") == "US:AAPL"
    assert service.resolve_stock_id("  aapl  ") == "US:AAPL"
    assert service.resolve_stock_id("AAPL") == "US:AAPL"


def test_resolve_stock_id_accepts_taiwan_numeric_symbols(monkeypatch) -> None:
    service = QuoteService()
    tsmc = _stock(stock_id="TW:2330", symbol="2330", market="TW")

    monkeypatch.setattr(
        service,
        "get_stock",
        lambda stock_id: tsmc if stock_id == "TW:2330" else None,
    )
    monkeypatch.setattr(service, "search", lambda _query: [])

    assert service.resolve_stock_id("2330") == "TW:2330"
    assert service.resolve_stock_id("TW:2330") == "TW:2330"


def test_resolve_stock_id_rejects_unknown_ids(monkeypatch) -> None:
    service = QuoteService()
    monkeypatch.setattr(service, "get_stock", lambda _stock_id: None)
    monkeypatch.setattr(service, "search", lambda _query: [])

    with pytest.raises(ValueError, match="Unknown stock id: US:NOPE"):
        service.resolve_stock_id("US:NOPE")
    with pytest.raises(ValueError, match="Unknown stock id: ZZZZZ"):
        service.resolve_stock_id("ZZZZZ")


def test_resolve_stock_id_does_not_silently_pick_among_ambiguous_matches(monkeypatch) -> None:
    service = QuoteService()
    matches = [
        _stock(stock_id="TW:1111", symbol="FOO", market="TW"),
        _stock(stock_id="TW:2222", symbol="FOO", market="TW"),
    ]
    monkeypatch.setattr(service, "get_stock", lambda _stock_id: None)
    monkeypatch.setattr(service, "search", lambda _query: matches)

    with pytest.raises(ValueError, match="Ambiguous stock symbol"):
        service.resolve_stock_id("FOO")


def test_resolve_stock_id_prefers_us_when_get_stock_finds_us(monkeypatch) -> None:
    service = QuoteService()
    us = _stock(stock_id="US:FOO", symbol="FOO", market="US")
    tw = _stock(stock_id="TW:1234", symbol="FOO", market="TW")

    monkeypatch.setattr(
        service,
        "get_stock",
        lambda stock_id: us if stock_id == "US:FOO" else None,
    )
    monkeypatch.setattr(service, "search", lambda _query: [us, tw])

    assert service.resolve_stock_id("FOO") == "US:FOO"
