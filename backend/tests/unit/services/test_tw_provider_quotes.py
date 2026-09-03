"""TWSE MIS quote resilience (#183)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.models.quote import QuoteRecord
from app.models.stock import StockRecord
from app.services.http_client import HttpFetchError
from app.services.providers import tw_provider


def _tw_stock() -> StockRecord:
    return StockRecord(
        id="TW:2330",
        market="TW",
        symbol="2330",
        name="TSMC",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def test_get_quote_falls_back_to_yahoo_when_mis_disconnects(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    stock = _tw_stock()
    yahoo_quote = QuoteRecord(
        id=stock.id,
        last_price=900.0,
        change=1.0,
        change_percent=0.1,
        previous_close=899.0,
        open=899.5,
        high=901.0,
        low=898.0,
        volume=1000,
        timestamp=datetime(2026, 8, 27, 13, 30, tzinfo=ZoneInfo("Asia/Taipei")).isoformat(),
        is_market_open=True,
    )
    mis_calls = {"count": 0}

    def fail_mis(*_args, **_kwargs):
        mis_calls["count"] += 1
        raise HttpFetchError(
            "Server disconnected without sending a response.",
            classification="network_failure",
        )

    monkeypatch.setattr(tw_provider, "_load_json", fail_mis)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_yahoo_tw_quote",
        lambda self, _stock: yahoo_quote,
    )
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()

    quote = tw_provider.TWProvider().get_quote(stock.id)
    assert quote == yahoo_quote
    assert mis_calls["count"] == 1


def test_get_quote_uses_cache_when_mis_and_yahoo_fail(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    stock = _tw_stock()
    cached = QuoteRecord(
        id=stock.id,
        last_price=880.0,
        change=-1.0,
        change_percent=-0.1,
        previous_close=881.0,
        open=881.0,
        high=882.0,
        low=879.0,
        volume=500,
        timestamp=datetime(2026, 8, 27, 13, 0, tzinfo=ZoneInfo("Asia/Taipei")).isoformat(),
        is_market_open=True,
    )
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HttpFetchError("disconnect", classification="network_failure")
        ),
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_yahoo_tw_quote",
        lambda self, _stock: None,
    )
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache[stock.id] = cached
    tw_provider.TWProvider._quote_cached_at[stock.id] = datetime.now(ZoneInfo(stock.timezone))

    quote = tw_provider.TWProvider().get_quote(stock.id)
    assert quote == cached


def test_mis_circuit_skips_mis_after_repeated_disconnects(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    stock = _tw_stock()
    yahoo_quote = QuoteRecord(
        id=stock.id,
        last_price=901.0,
        change=2.0,
        change_percent=0.2,
        previous_close=899.0,
        open=899.5,
        high=902.0,
        low=898.0,
        volume=2000,
        timestamp=datetime(2026, 8, 27, 13, 30, tzinfo=ZoneInfo("Asia/Taipei")).isoformat(),
        is_market_open=True,
    )
    mis_calls = {"count": 0}

    def fail_mis(*_args, **_kwargs):
        mis_calls["count"] += 1
        raise HttpFetchError(
            "Server disconnected without sending a response.",
            classification="network_failure",
        )

    monkeypatch.setattr(tw_provider, "_load_json", fail_mis)
    monkeypatch.setattr(tw_provider, "_mis_cookie_header", lambda: None)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_yahoo_tw_quote",
        lambda self, _stock: yahoo_quote,
    )
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()

    provider = tw_provider.TWProvider()
    for _ in range(tw_provider._MIS_FAILURE_THRESHOLD):
        assert provider.get_quote(stock.id) == yahoo_quote

    before = mis_calls["count"]
    assert before == tw_provider._MIS_FAILURE_THRESHOLD
    assert tw_provider._mis_circuit_is_open()

    assert provider.get_quote(stock.id) == yahoo_quote
    assert mis_calls["count"] == before  # circuit skipped further MIS calls
    tw_provider.reset_mis_circuit_for_tests()


def test_mis_cookie_warmup_caches_cookie_less_failure(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    calls = {"count": 0}

    def fail_warmup(*_args, **_kwargs):
        calls["count"] += 1
        raise httpx.ConnectTimeout("warmup timed out")

    monkeypatch.setattr(tw_provider.httpx, "get", fail_warmup)
    assert tw_provider._mis_cookie_header() is None
    assert tw_provider._mis_cookie_header() is None
    assert calls["count"] == 1
    tw_provider.reset_mis_circuit_for_tests()


def _tpex_stock() -> StockRecord:
    return StockRecord(
        id="TW:6562",
        market="TW",
        symbol="6562",
        name="聯亞",
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
        name_zh_tw="聯亞",
    )


def test_tpex_yahoo_candidates_prefer_two_then_tw() -> None:
    assert tw_provider._yahoo_tw_symbol_candidates(_tpex_stock()) == (
        "6562.TWO",
        "6562.TW",
    )


def test_tpex_get_quote_falls_back_to_yahoo_when_mis_empty(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    stock = _tpex_stock()
    yahoo_quote = QuoteRecord(
        id=stock.id,
        last_price=120.5,
        change=1.5,
        change_percent=1.26,
        previous_close=119.0,
        open=119.5,
        high=121.0,
        low=118.5,
        volume=1000,
        timestamp=datetime(2026, 9, 3, 13, 30, tzinfo=ZoneInfo("Asia/Taipei")).isoformat(),
        is_market_open=True,
    )
    mis_calls: list[dict[str, str]] = []

    def empty_mis(_url, params, **_kwargs):
        mis_calls.append(dict(params))
        return {"msgArray": []}

    monkeypatch.setattr(tw_provider, "_load_json", empty_mis)
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_yahoo_tw_quote",
        lambda self, _stock: yahoo_quote,
    )
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()

    quote = tw_provider.TWProvider().get_quote(stock.id)
    assert quote == yahoo_quote
    assert mis_calls[0]["ex_ch"] == "otc_6562.tw"


def test_tpex_get_quote_returns_none_when_mis_and_yahoo_fail(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    stock = _tpex_stock()
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HttpFetchError("timeout", classification="network_failure")
        ),
    )
    monkeypatch.setattr(
        tw_provider.TWProvider,
        "_fetch_yahoo_tw_quote",
        lambda self, _stock: None,
    )
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()

    assert tw_provider.TWProvider().get_quote(stock.id) is None


def test_tpex_yahoo_quote_tries_two_before_tw(monkeypatch) -> None:
    tw_provider.reset_mis_circuit_for_tests()
    stock = _tpex_stock()
    requested: list[str] = []

    def fake_fetch_json(url: str, **_kwargs) -> dict[str, object]:
        requested.append(url)
        if "/6562.TWO?" in url:
            return {"chart": {"result": None, "error": {"code": "Not Found"}}}
        return {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 121.0,
                            "chartPreviousClose": 119.0,
                            "regularMarketTime": 1756897200,
                        },
                        "indicators": {"quote": [{"close": [118.0, 119.0]}]},
                    }
                ]
            }
        }

    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: {"msgArray": []})
    monkeypatch.setattr(tw_provider, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(tw_provider.TWProvider, "_stock_catalog", lambda self: {stock.id: stock})
    tw_provider.TWProvider._quote_cache.clear()
    tw_provider.TWProvider._quote_cached_at.clear()

    quote = tw_provider.TWProvider().get_quote(stock.id)
    assert quote is not None
    assert quote.last_price == pytest.approx(121.0)
    assert any(url.endswith("6562.TWO?interval=1d&range=5d") or "/6562.TWO?" in url for url in requested)
    assert any("/6562.TW?" in url for url in requested)
    two_idx = next(i for i, url in enumerate(requested) if "/6562.TWO?" in url)
    tw_idx = next(i for i, url in enumerate(requested) if "/6562.TW?" in url)
    assert two_idx < tw_idx
