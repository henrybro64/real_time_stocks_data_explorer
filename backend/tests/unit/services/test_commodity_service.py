from __future__ import annotations

import threading
import time

import pytest

from app.services import commodity_service
from app.services.commodity_service import CommodityUnavailableError
from app.services.http_client import HttpFetchError


@pytest.fixture(autouse=True)
def _reset_wti_cache():
    commodity_service.clear_wti_cache_for_tests()
    yield
    commodity_service.clear_wti_cache_for_tests()


def _yahoo_payload(
    *,
    price: float = 68.45,
    previous_close: float | None = 69.19,
    market_time: int | None = 1_754_012_700,
    market_state: str | None = "CLOSED",
) -> dict:
    meta: dict = {
        "regularMarketPrice": price,
        "marketState": market_state,
    }
    if previous_close is not None:
        meta["chartPreviousClose"] = previous_close
    if market_time is not None:
        meta["regularMarketTime"] = market_time
    return {"chart": {"result": [{"meta": meta}]}}


def test_normalize_valid_wti_payload_negative_move() -> None:
    quote = commodity_service._normalize_yahoo_wti_payload(
        _yahoo_payload(price=68.45, previous_close=69.19),
    )
    assert quote is not None
    assert quote.symbol == "CL=F"
    assert quote.name == "WTI Crude Oil"
    assert quote.currency == "USD"
    assert quote.unit == "barrel"
    assert quote.price == pytest.approx(68.45)
    assert quote.change == pytest.approx(-0.74)
    assert quote.change_percent == pytest.approx(-1.0692, abs=1e-3)
    assert quote.source == "Yahoo Finance"
    assert quote.is_market_open is False
    assert quote.timestamp.endswith("-04:00") or quote.timestamp.endswith("-05:00")


def test_normalize_positive_and_unchanged_moves() -> None:
    up = commodity_service._normalize_yahoo_wti_payload(
        _yahoo_payload(price=70.0, previous_close=69.0),
    )
    flat = commodity_service._normalize_yahoo_wti_payload(
        _yahoo_payload(price=70.0, previous_close=70.0),
    )
    assert up is not None and up.change == pytest.approx(1.0)
    assert up.change_percent == pytest.approx(1.4493, abs=1e-3)
    assert flat is not None and flat.change == pytest.approx(0.0)
    assert flat.change_percent == pytest.approx(0.0)


def test_missing_previous_close_yields_null_change() -> None:
    quote = commodity_service._normalize_yahoo_wti_payload(
        _yahoo_payload(price=68.45, previous_close=None),
    )
    assert quote is not None
    assert quote.change is None
    assert quote.change_percent is None


@pytest.mark.parametrize("price", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_price_is_rejected(price: float) -> None:
    payload = _yahoo_payload(price=1.0)
    payload["chart"]["result"][0]["meta"]["regularMarketPrice"] = price
    assert commodity_service._normalize_yahoo_wti_payload(payload) is None


@pytest.mark.parametrize("price", [0.0, -37.63])
def test_zero_and_negative_futures_prices_are_accepted(price: float) -> None:
    quote = commodity_service._normalize_yahoo_wti_payload(
        _yahoo_payload(price=price, previous_close=10.0),
    )
    assert quote is not None
    assert quote.price == pytest.approx(price)
    assert quote.change == pytest.approx(price - 10.0)


def test_provider_failure_with_no_cache_raises(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise HttpFetchError("down")

    monkeypatch.setattr(commodity_service, "fetch_json", boom)
    with pytest.raises(CommodityUnavailableError):
        commodity_service.get_wti_crude_oil_quote()


def test_cache_serves_within_ttl(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fetch(*_a, **_k):
        calls["count"] += 1
        return _yahoo_payload()

    monkeypatch.setattr(commodity_service, "fetch_json", fake_fetch)
    first = commodity_service.get_wti_crude_oil_quote()
    second = commodity_service.get_wti_crude_oil_quote()
    assert first.price == second.price
    assert first.status == "ok"
    assert calls["count"] == 1


def test_stale_retained_quote_served_after_soft_ttl_provider_failure(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fetch(*_a, **_k):
        calls["count"] += 1
        if calls["count"] == 1:
            return _yahoo_payload(price=68.45)
        raise HttpFetchError("down")

    monkeypatch.setattr(commodity_service, "fetch_json", fake_fetch)
    first = commodity_service.get_wti_crude_oil_quote()
    assert first.status == "ok"

    monkeypatch.setattr(
        commodity_service,
        "payload_age_seconds",
        lambda _payload: commodity_service._SOFT_TTL_SECONDS + 1,
    )
    second = commodity_service.get_wti_crude_oil_quote()
    assert second.status == "stale"
    assert second.price == pytest.approx(68.45)
    assert calls["count"] == 2


def test_expired_refresh_is_singleflight_across_threads(monkeypatch) -> None:
    calls = {"count": 0}
    started = threading.Event()
    release = threading.Event()

    def fake_fetch(*_a, **_k):
        calls["count"] += 1
        if calls["count"] == 1:
            return _yahoo_payload(price=68.45)
        started.set()
        assert release.wait(timeout=2.0)
        return _yahoo_payload(price=70.25)

    monkeypatch.setattr(commodity_service, "fetch_json", fake_fetch)
    warm = commodity_service.get_wti_crude_oil_quote()
    assert warm.status == "ok"
    assert calls["count"] == 1

    monkeypatch.setattr(
        commodity_service,
        "payload_age_seconds",
        lambda _payload: commodity_service._SOFT_TTL_SECONDS + 1,
    )

    results: list[object] = []

    def worker() -> None:
        results.append(commodity_service.get_wti_crude_oil_quote())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=2.0)
    # Give waiters a moment to join the singleflight gate.
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=2.0)

    assert calls["count"] == 2
    assert len(results) == 4
    assert all(getattr(item, "price", None) == pytest.approx(70.25) for item in results)


def test_commodity_quote_to_api_uses_camel_case() -> None:
    quote = commodity_service._normalize_yahoo_wti_payload(_yahoo_payload())
    assert quote is not None
    payload = commodity_service.commodity_quote_to_api(quote)
    assert payload["changePercent"] == quote.change_percent
    assert payload["isMarketOpen"] is False
    assert payload["status"] == "ok"
