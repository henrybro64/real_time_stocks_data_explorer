from __future__ import annotations


def test_health_includes_quote_cache_section(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "quote_cache" in payload
    quote_cache = payload["quote_cache"]
    assert "configured" in quote_cache
    assert "state" in quote_cache
    assert "connected" in quote_cache
    assert "backend" in quote_cache
    assert "last_success_ping_at" in quote_cache
    assert "last_success_read_at" in quote_cache
    assert "last_success_write_at" in quote_cache
    assert "last_error_category" in quote_cache
    assert "redis://" not in response.text
