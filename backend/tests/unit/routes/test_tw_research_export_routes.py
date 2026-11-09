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


def test_latest_verified_manifest_and_snapshot_chunk(monkeypatch) -> None:
    from datetime import datetime, timezone

    import app.api.routes.tw_research as tw_routes
    from app.repositories.tw_research_repository import TwResearchRepository
    from app.services.tw_research.persistence import finalize_verified_snapshot, persist_scan
    from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
    from app.services.tw_research.universe import UniverseSelector
    from tests.support import install_in_memory_sessionlocal, stock_record

    factory = install_in_memory_sessionlocal(monkeypatch)
    repo = TwResearchRepository()
    repo.set_session_factory_override(factory)
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository",
        repo,
    )
    monkeypatch.setattr(
        "app.services.tw_research.persistence.tw_research_repository",
        repo,
    )

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
        persist=True,
    )
    monkeypatch.setattr(tw_routes, "tw_research_scan_service", service)

    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2727",)),
        trading_day="2024-06-28",
        user_id="user-1",
    )
    state.records["TW:2727"] = SymbolScanRecord(
        stock_id="TW:2727",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    state.cursor = 1
    state.status = "completed_processing"
    state.quality_status = "technically_qualified"
    assert persist_scan(state, repository=repo, user_id="user-1") is True

    # Empty DB → explicit no-snapshot response (before finalize).
    assert (
        tw_routes._latest_verified_snapshot(
            universe_version=state.universe_version,
            trading_day="2024-06-28",
            user_id="user-1",
        )
        is None
    )

    finalized = finalize_verified_snapshot(
        state,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=timezone.utc),
    )
    assert finalized is not None

    assert (
        tw_routes._latest_verified_snapshot(
            universe_version="explicit:99:deadbeef",
            trading_day="2024-06-28",
            user_id="user-1",
        )
        is None
    )

    latest = tw_routes._latest_verified_snapshot(
        universe_version=state.universe_version,
        trading_day="2024-06-28",
        user_id="user-1",
    )
    assert latest is not None
    assert latest["content_digest"] == finalized["contentDigest"]
    assert latest["snapshot_id"] == finalized["snapshotId"]

    chunk = tw_routes.get_verified_snapshot_chunk(
        finalized["snapshotId"], chunkIndex=0, kind="ledger", user_id="user-1"
    )
    assert chunk.chunk["checksumSha256"]

    latest_manifest = tw_routes.get_latest_verified_manifest(
        universeVersion=state.universe_version,
        tradingDay="2024-06-28",
        user_id="user-1",
    )
    assert latest_manifest.manifest["snapshotId"] == finalized["snapshotId"]
    assert latest_manifest.manifest["snapshotId"] is not None

    # Other users must not read this snapshot or fall back to a global latest.
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as denied:
        tw_routes.get_verified_snapshot_chunk(
            finalized["snapshotId"], chunkIndex=0, kind="ledger", user_id="user-2"
        )
    assert denied.value.status_code == 404
    assert (
        tw_routes._latest_verified_snapshot(
            universe_version=state.universe_version,
            trading_day="2024-06-28",
            user_id="user-2",
        )
        is None
    )

    # Null daily/monthly payloads must not 500 on kind=qualified.
    snap = repo.get_export_snapshot(finalized["snapshotId"])
    assert snap is not None
    original_ledger = list(snap["ledger_json"])
    with factory() as session:
        from app.db.models import TwResearchExportSnapshot

        row = session.get(TwResearchExportSnapshot, finalized["snapshotId"])
        assert row is not None
        row.ledger_json = original_ledger + [
            {"stockId": "TW:ERR", "stage": "A", "readiness": "processed", "daily": None}
        ]
        session.commit()
    qualified = tw_routes.get_verified_snapshot_chunk(
        finalized["snapshotId"], chunkIndex=0, kind="qualified", user_id="user-1"
    )
    assert all(row.get("stockId") != "TW:ERR" for row in qualified.chunk["rows"])
    with factory() as session:
        from app.db.models import TwResearchExportSnapshot

        row = session.get(TwResearchExportSnapshot, finalized["snapshotId"])
        assert row is not None
        row.ledger_json = original_ledger
        session.commit()

    # After scan retention nulls the FK, chunk digests must still match via manifest scanId.
    assert repo.delete_scans_older_than(datetime(2099, 1, 1, tzinfo=timezone.utc)) >= 1
    retained = repo.get_export_snapshot(finalized["snapshotId"])
    assert retained is not None
    assert retained["scan_id"] is None
    still = tw_routes.get_verified_snapshot_chunk(
        finalized["snapshotId"], chunkIndex=0, kind="ledger", user_id="user-1"
    )
    assert still.chunk["checksumSha256"] == chunk.chunk["checksumSha256"]
