from app.services import fx_service
from app.services.http_client import HttpFetchError


def test_fetch_usd_twd_rate_parses_valid_payload(monkeypatch) -> None:
    def fake_fetch_json(*args, **kwargs):
        return {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 32.123456,
                        }
                    }
                ]
            }
        }

    monkeypatch.setattr(fx_service, "fetch_json", fake_fetch_json)

    assert fx_service._fetch_usd_twd_rate() == 32.1235


def test_fetch_usd_twd_rate_returns_none_for_malformed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        fx_service,
        "fetch_json",
        lambda *args, **kwargs: {"chart": {"result": [{"meta": {}}]}},
    )

    assert fx_service._fetch_usd_twd_rate() is None


def test_fetch_usd_twd_rate_returns_none_when_http_fails(monkeypatch) -> None:
    def fake_fetch_json(*args, **kwargs):
        raise HttpFetchError("boom")

    monkeypatch.setattr(fx_service, "fetch_json", fake_fetch_json)

    assert fx_service._fetch_usd_twd_rate() is None


def test_snapshot_uses_fallback_rate_when_live_fetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(fx_service, "_fetch_usd_twd_rate", lambda: None)

    snapshot = fx_service.get_usd_twd_fx_snapshot()

    assert snapshot["pair"] == "USD/TWD"
    assert snapshot["rate"] == 32.0
    assert isinstance(snapshot["timestamp"], str)
