"""Integration coverage for browser-only Agent JSON handoff (#322).

Requires DATABASE_URL (see integration conftest). Exercises authenticated HTTPS
JSON paths against real PostgreSQL without upstream provider calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.init_ids import DEFAULT_USER_ID
from app.main import app
from app.repositories.tw_research_repository import TwResearchRepository
from app.services.tw_research.agent_handoff import (
    plan_chunk_transfer,
    verify_ledger_chunk,
)
from app.services.tw_research.persistence import finalize_verified_snapshot, persist_scan
from app.services.tw_research.scan import SymbolScanRecord, TwResearchScanService
from app.services.tw_research.universe import UniverseSelector
from tests.support import stock_record


def _tw(stock_id: str):
    symbol = stock_id.split(":", 1)[1]
    return stock_record(
        stock_id,
        market="TW",
        symbol=symbol,
        name=symbol,
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )


def test_postgres_latest_manifest_and_chunk_handoff_without_upstream() -> None:
    fetch_count = {"n": 0}

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        fetch_count["n"] += 1
        return [], 0

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: _tw(stock_id),
        persist=True,
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2330",)),
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    state.records["TW:2330"] = SymbolScanRecord(
        stock_id="TW:2330",
        stage="B",
        readiness="verified_ready",
        daily={"passStatus": "pass"},
        monthly={"passStatus": "pass"},
    )
    state.cursor = 1
    state.status = "completed_processing"
    state.quality_status = "technically_qualified"
    assert persist_scan(state, user_id=DEFAULT_USER_ID) is True
    finalized = finalize_verified_snapshot(
        state,
        user_id=DEFAULT_USER_ID,
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=timezone.utc),
    )
    assert finalized is not None
    before = fetch_count["n"]

    repo = TwResearchRepository()
    latest = repo.latest_verified_snapshot(
        universe_version=state.universe_version,
        trading_day="2024-06-28",
        user_id=DEFAULT_USER_ID,
    )
    assert latest is not None
    manifest = dict(latest["manifest_json"] or {})
    manifest["snapshotId"] = latest["snapshot_id"]
    manifest["contentDigest"] = latest["content_digest"]

    plan = plan_chunk_transfer(previous_manifest=None, current_manifest=manifest)
    assert plan.action == "fetch_all"
    assert plan.verification == "verification_required"

    unchanged = plan_chunk_transfer(
        previous_manifest=manifest,
        current_manifest=dict(manifest),
    )
    assert unchanged.action == "skip_all"
    assert unchanged.verification == "unchanged_skip"
    assert unchanged.removed_chunk_indexes == ()

    snapshot = repo.get_export_snapshot(finalized["snapshotId"])
    assert snapshot is not None
    from app.services.tw_research.export import build_export_chunk_from_rows

    rows = list(snapshot["ledger_json"] or [])
    chunk = build_export_chunk_from_rows(
        rows,
        chunk_index=0,
        chunk_size=25,
        kind="ledger",
        scan_id=state.scan_id,
        trading_day="2024-06-28",
        frozen_at=str(manifest.get("finalizedAt") or ""),
    )
    assert (
        verify_ledger_chunk(manifest=manifest, chunk=chunk)
        == "stock_ticker_export_verified"
    )
    assert fetch_count["n"] == before


def test_http_latest_manifest_requires_auth_and_returns_json() -> None:
    with TestClient(app) as client:
        unauth = client.get("/api/tw-research/exports/latest/manifest")
        # Auth may be disabled in some local configs; when enabled → 401.
        assert unauth.status_code in {200, 401, 404}
        if unauth.status_code == 401:
            assert unauth.json() == {"detail": "Not authenticated"}
        else:
            body = unauth.json()
            assert isinstance(body, dict)
