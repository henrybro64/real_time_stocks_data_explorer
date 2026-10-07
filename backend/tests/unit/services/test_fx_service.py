from __future__ import annotations

import pytest

from app.services import fx_service
from app.services.http_client import HttpFetchError


@pytest.fixture(autouse=True)
def _reset_fx_cache() -> None:
    fx_service.clear_fx_cache_for_tests()
    yield
    fx_service.clear_fx_cache_for_tests()


def test_fetch_usd_twd_snapshot_parses_valid_payload(monkeypatch) -> None:
    def fake_fetch_json(*args, **kwargs):
        return {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 32.123456,
                            "chartPreviousClose": 32.0,
                            "regularMarketTime": 1_754_012_700,
                        }
                    }
                ]
            }
        }

    monkeypatch.setattr(fx_service, "fetch_json", fake_fetch_json)

    assert fx_service._fetch_usd_twd_snapshot()["rate"] == 32.1235


def test_snapshot_includes_change_when_previous_close_present(monkeypatch) -> None:
    monkeypatch.setattr(
        fx_service,
        "fetch_json",
        lambda *args, **kwargs: {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 32.5,
                            "previousClose": 32.0,
                        }
                    }
                ]
            }
        },
    )

    snapshot = fx_service.get_usd_twd_fx_snapshot()

    assert snapshot["pair"] == "USD/TWD"
    assert snapshot["rate"] == 32.5
    assert snapshot["change"] == 0.5
    assert snapshot["changePercent"] == 1.5625
    assert snapshot["source"] == "Yahoo Finance"
    assert snapshot["status"] == "ok"


def test_fetch_usd_twd_snapshot_returns_none_for_malformed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        fx_service,
        "fetch_json",
        lambda *args, **kwargs: {"chart": {"result": [{"meta": {}}]}},
    )

    assert fx_service._fetch_usd_twd_snapshot() is None


def test_fetch_usd_twd_snapshot_returns_none_when_http_fails(monkeypatch) -> None:
    def fake_fetch_json(*args, **kwargs):
        raise HttpFetchError("boom")

    monkeypatch.setattr(fx_service, "fetch_json", fake_fetch_json)

    assert fx_service._fetch_usd_twd_snapshot() is None


def test_snapshot_uses_fallback_status_when_live_fetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(fx_service, "_fetch_usd_twd_snapshot", lambda: None)

    snapshot = fx_service.get_usd_twd_fx_snapshot()

    assert snapshot["pair"] == "USD/TWD"
    assert snapshot["rate"] == 32.0
    assert snapshot["change"] is None
    assert snapshot["changePercent"] is None
    assert snapshot["status"] == "fallback"
    assert isinstance(snapshot["timestamp"], str)


def test_snapshot_serves_stale_retained_quote_after_provider_failure(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_fetch(*_a, **_k):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "regularMarketPrice": 31.5,
                                "previousClose": 31.0,
                            }
                        }
                    ]
                }
            }
        raise HttpFetchError("down")

    monkeypatch.setattr(fx_service, "fetch_json", fake_fetch)
    first = fx_service.get_usd_twd_fx_snapshot()
    assert first["status"] == "ok"
    assert first["rate"] == 31.5

    # Expire soft TTL but keep hard retention.
    monkeypatch.setattr(
        fx_service,
        "payload_age_seconds",
        lambda _payload: fx_service._SOFT_TTL_SECONDS + 1,
    )
    second = fx_service.get_usd_twd_fx_snapshot()
    assert second["status"] == "stale"
    assert second["rate"] == 31.5
    assert calls["count"] == 2
