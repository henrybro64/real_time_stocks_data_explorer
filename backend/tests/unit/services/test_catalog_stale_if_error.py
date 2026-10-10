"""Catalog and holiday calendar stale-if-error behavior (#264)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import market_clock
from app.services.http_client import HttpFetchError
from app.services.providers import tw_provider, us_provider
from app.services.providers.tw_provider import TWProvider
from app.services.providers.us_provider import USProvider
from tests.support import stock_record


def _tw(stock_id: str, *, symbol: str, name: str):
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=name,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def _us(stock_id: str, *, symbol: str, name: str):
    return stock_record(
        stock_id,
        market="US",
        symbol=symbol,
        name=name,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
    )


@pytest.fixture(autouse=True)
def _reset_catalogs(monkeypatch):
    monkeypatch.setattr(TWProvider, "_catalog_cache", None)
    monkeypatch.setattr(TWProvider, "_catalog_cached_at", None)
    monkeypatch.setattr(TWProvider, "_catalog_degraded", False)
    monkeypatch.setattr(USProvider, "_catalog_cache", None)
    monkeypatch.setattr(USProvider, "_catalog_cached_at", None)
    monkeypatch.setattr(USProvider, "_catalog_degraded", False)
    market_clock._clear_tw_market_holiday_cache()
    yield
    market_clock._clear_tw_market_holiday_cache()


def test_tw_catalog_keeps_previous_on_total_failure(monkeypatch) -> None:
    good = {
        "TW:2330": _tw("TW:2330", symbol="2330", name="TSMC"),
        "TW:2317": _tw("TW:2317", symbol="2317", name="Hon Hai"),
    }
    TWProvider._catalog_cache = dict(good)
    TWProvider._catalog_cached_at = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(hours=13)
    TWProvider._catalog_degraded = False

    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HttpFetchError("down", classification="network_failure")
        ),
    )
    monkeypatch.setattr(
        tw_provider,
        "_load_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HttpFetchError("down", classification="network_failure")
        ),
    )

    catalog = TWProvider().refresh_catalog()
    assert set(catalog) >= set(good)
    assert TWProvider._catalog_degraded is True


def test_tw_partial_load_does_not_drop_known_symbols(monkeypatch) -> None:
    previous = {
        "TW:2330": _tw("TW:2330", symbol="2330", name="TSMC"),
        "TW:2881": _tw("TW:2881", symbol="2881", name="Fubon"),
    }
    TWProvider._catalog_cache = dict(previous)
    TWProvider._catalog_cached_at = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(hours=13)

    monkeypatch.setattr(tw_provider, "_load_json", lambda *_a, **_k: None)
    monkeypatch.setattr(
        tw_provider,
        "_load_text",
        lambda url, *_a, **_k: (
            "<table><tr><td>1234 台积电</td><td>TWSE</td></tr></table>"
            if "strMode=2" in url
            else (_ for _ in ()).throw(HttpFetchError("otc down", classification="http_error"))
        ),
    )
    monkeypatch.setattr(
        tw_provider,
        "_stocks_from_isin_html",
        lambda html, detailed: [
            _tw("TW:1234", symbol="1234", name="NewCo"),
        ],
    )

    catalog = TWProvider().refresh_catalog()
    assert "TW:2330" in catalog
    assert "TW:2881" in catalog
    assert "TW:1234" in catalog


def test_us_catalog_keeps_previous_on_sec_failure(monkeypatch) -> None:
    previous = {
        "US:AAPL": _us("US:AAPL", symbol="AAPL", name="Apple"),
    }
    USProvider._catalog_cache = dict(previous)
    USProvider._catalog_cached_at = datetime.now(ZoneInfo("UTC")) - timedelta(hours=13)

    monkeypatch.setattr(
        us_provider,
        "_load_catalog_payload",
        lambda: (_ for _ in ()).throw(
            HttpFetchError("sec down", classification="network_failure")
        ),
    )

    catalog = USProvider().refresh_catalog()
    assert "US:AAPL" in catalog
    assert USProvider._catalog_degraded is True


def _expire_holiday_success(year: int = 2026) -> None:
    """Place the success timestamp outside the TTL even on a freshly booted runner."""
    market_clock._tw_holiday_success_at[year] = (
        market_clock.monotonic() - market_clock._TW_HOLIDAY_SUCCESS_TTL_SECONDS - 1
    )


def test_holiday_failure_keeps_previous_and_retries(monkeypatch) -> None:
    # A runner booted less than four hours ago has a small monotonic clock.
    monkeypatch.setattr(market_clock, "monotonic", lambda: 1_000.0)
    market_clock._tw_holiday_success[2026] = frozenset(
        {datetime(2026, 1, 1).date()}
    )
    _expire_holiday_success()

    calls = {"n": 0}

    def boom(_year: int):
        calls["n"] += 1
        raise HttpFetchError("holiday down", classification="network_failure")

    monkeypatch.setattr(market_clock, "_load_tw_market_holidays", boom)
    first = market_clock.refresh_tw_market_holidays(2026)
    second = market_clock.refresh_tw_market_holidays(2026)
    assert first == frozenset({datetime(2026, 1, 1).date()})
    assert second == first
    assert calls["n"] == 1


def test_holiday_success_expires_and_refetches(monkeypatch) -> None:
    monkeypatch.setattr(market_clock, "monotonic", lambda: 1_000.0)
    market_clock._tw_holiday_success[2026] = frozenset()
    _expire_holiday_success()

    monkeypatch.setattr(
        market_clock,
        "_load_tw_market_holidays",
        lambda year: frozenset({datetime(year, 10, 10).date()}),
    )
    holidays = market_clock.refresh_tw_market_holidays(2026)
    assert datetime(2026, 10, 10).date() in holidays


def test_stale_catalog_read_does_not_fetch(monkeypatch) -> None:
    good = {"TW:2330": _tw("TW:2330", symbol="2330", name="TSMC")}
    TWProvider._catalog_cache = dict(good)
    TWProvider._catalog_cached_at = datetime.now(ZoneInfo("Asia/Taipei")) - timedelta(hours=13)

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("catalog read must not call upstream")

    monkeypatch.setattr(tw_provider, "_load_json", _should_not_fetch)
    monkeypatch.setattr(tw_provider, "_load_text", _should_not_fetch)

    catalog = TWProvider()._stock_catalog()
    assert "TW:2330" in catalog


def test_empty_catalog_hydrates_from_store_without_fetch(monkeypatch) -> None:
    stored = {"TW:9999": _tw("TW:9999", symbol="9999", name="Stored")}

    monkeypatch.setattr(
        "app.services.reference_data_store.load_catalog_snapshot",
        lambda _key: (stored, False, datetime.now(ZoneInfo("Asia/Taipei"))),
    )
    monkeypatch.setattr(
        tw_provider,
        "_load_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("hydrate must not fetch")),
    )

    catalog = TWProvider()._stock_catalog()
    assert "TW:9999" in catalog


def test_expired_holiday_read_does_not_fetch(monkeypatch) -> None:
    market_clock._tw_holiday_success[2026] = frozenset({datetime(2026, 1, 1).date()})
    _expire_holiday_success()

    def _should_not_fetch(_year: int):
        raise AssertionError("holiday read must not call upstream")

    monkeypatch.setattr(market_clock, "_load_tw_market_holidays", _should_not_fetch)
    assert market_clock._tw_market_holidays(2026) == frozenset({datetime(2026, 1, 1).date()})
