"""Export/enrichment HTTP surface for #313–#314."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
from tests.support import stock_record


client = TestClient(app)


def test_export_and_enrichment_routes_are_read_or_bounded(monkeypatch) -> None:
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
    service = TwResearchScanService(
        history_loader=lambda stock, limit, max_month_fetches=0: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
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
            "maxEnrichmentsPerRun": 5,
        },
    )
    assert created.status_code == 200
    scan_id = created.json()["scan"]["scanId"]
    state = service.get_scan(scan_id)
    assert state is not None
    state.records["TW:2727"] = SymbolScanRecord(
        stock_id="TW:2727",
        stage="B",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
        theme_keys=(),
    )
    state.qualified_ids.append("TW:2727")

    from app.services.tw_research.enrichment import EnrichmentResult

    def stub_enrich(stock_id: str, **kwargs):  # noqa: ANN003
        del kwargs
        return EnrichmentResult(stock_id=stock_id, qualified=True, fields={})

    enriched_state = service.run_enrichment_batch(
        scan_id,
        batch_size=1,
        enricher=stub_enrich,
    )
    assert enriched_state.records["TW:2727"].enrichment is not None

    http_enrich = client.post(
        "/api/tw-research/scans/enrichment-batches",
        json={"scanId": scan_id, "batchSize": 1},
    )
    assert http_enrich.status_code == 200

    manifest = client.get(f"/api/tw-research/scans/{scan_id}/export/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["manifest"]["schemaVersion"]

    chunk = client.get(
        f"/api/tw-research/scans/{scan_id}/export/chunk",
        params={"chunkIndex": 0, "kind": "ledger"},
    )
    assert chunk.status_code == 200
    assert chunk.json()["chunk"]["totalRows"] == 1

    csv_response = client.get(f"/api/tw-research/scans/{scan_id}/export/csv")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]

    text = client.get(f"/api/tw-research/scans/{scan_id}/export/browser-text")
    assert text.status_code == 200
    assert b"frontendExportReady" in text.content
    assert b"continuation" in text.content
    assert b"chunkPathTemplate" in text.content

    chunked = client.get(
        f"/api/tw-research/scans/{scan_id}/export/browser-text",
        params={"chunkIndex": 0, "kind": "ledger"},
    )
    assert chunked.status_code == 200
    assert b'"chunkIndex": 0' in chunked.content
