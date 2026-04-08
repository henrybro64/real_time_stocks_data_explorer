import pytest

from app.api.routes import markets as markets_routes


pytestmark = pytest.mark.integration


def test_assets_api_adds_and_removes_assets(monkeypatch, client) -> None:
    monkeypatch.setattr(
        markets_routes,
        "get_usd_twd_fx_snapshot",
        lambda: {
            "pair": "USD/TWD",
            "rate": 32.15,
            "timestamp": "2026-04-01T01:00:00Z",
        },
    )

    add = client.post(
        "/api/portfolio/assets",
        json={
            "assetType": "cash",
            "name": "Emergency fund",
            "currency": "USD",
            "value": 1500,
            "notes": None,
        },
    )
    list_response = client.get("/api/portfolio/assets")
    fx_response = client.get("/api/markets/fx/usd-twd")
    delete = client.delete(f"/api/portfolio/assets/{add.json()['id']}")

    assert add.status_code == 200
    assert add.json()["name"] == "Emergency fund"
    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "id": add.json()["id"],
            "assetType": "cash",
            "name": "Emergency fund",
            "currency": "USD",
            "value": 1500.0,
            "notes": None,
        }
    ]
    assert fx_response.status_code == 200
    assert fx_response.json()["rate"] == 32.15
    assert delete.status_code == 204
