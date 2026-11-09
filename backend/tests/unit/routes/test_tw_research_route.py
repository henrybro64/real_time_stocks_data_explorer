"""HTTP surface for Taiwan research routes (#310–#312)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_contract_endpoint_is_industry_agnostic() -> None:
    response = client.get("/api/tw-research/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["industryAgnostic"] is True
    assert payload["starterPresetIsOptional"] is True
    assert payload["noHardUniverseCap"] is True
    assert payload["fieldMap"]


def test_get_scan_does_not_create_state() -> None:
    response = client.get("/api/tw-research/scans/does-not-exist")
    assert response.status_code == 404


def test_create_and_get_scan_round_trip_with_explicit_ids(monkeypatch) -> None:
    from app.services.tw_research import scan as scan_module
    from tests.support import stock_record

    catalog = {
        "TW:2727": stock_record(
            "TW:2727",
            market="TW",
            symbol="2727",
            name="王品",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }

    service = scan_module.TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
        persist=False,
    )
    monkeypatch.setattr(
        "app.api.routes.tw_research.tw_research_scan_service",
        service,
    )

    created = client.post(
        "/api/tw-research/scans",
        json={
            "universe": {"mode": "stock_ids", "stockIds": ["TW:2727"]},
            "targetCount": 10,
            "tradingDay": "2024-06-28",
        },
    )
    assert created.status_code == 200
    scan_id = created.json()["scan"]["scanId"]

    fetched = client.get(f"/api/tw-research/scans/{scan_id}")
    assert fetched.status_code == 200
    assert fetched.json()["scan"]["scanId"] == scan_id
    assert fetched.json()["scan"]["checkpointsEphemeral"] is False
    assert fetched.json()["scan"]["checkpointsDurable"] is True
    assert fetched.json()["scan"]["status"] == "queued"


def test_resolve_universe_endpoint_accepts_out_of_preset_ids(monkeypatch) -> None:
    from tests.support import stock_record

    catalog = {
        "TW:2727": stock_record(
            "TW:2727",
            market="TW",
            symbol="2727",
            name="王品",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2618": stock_record(
            "TW:2618",
            market="TW",
            symbol="2618",
            name="長榮航",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    monkeypatch.setattr(
        "app.services.tw_research.universe.quote_service.get_catalog_stock",
        lambda stock_id: catalog.get(stock_id),
    )
    response = client.post(
        "/api/tw-research/universe/resolve",
        json={"mode": "stock_ids", "stockIds": ["TW:2727", "TW:2618"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["uniqueCount"] == 2
    assert payload["stockIds"] == ["TW:2727", "TW:2618"]
