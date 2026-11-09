"""Agent JSON handoff contract tests (#322) + provisional pilot markers (#321)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.tw_research.agent_handoff import (
    build_agent_transfer_example,
    classify_session_error,
    plan_chunk_transfer,
    verify_ledger_chunk,
)
from app.services.tw_research.export import content_digest
from tests.integration.local_auth_helpers import enable_local_auth


FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "tw_research"
    / "agent_handoff_example.json"
)


def test_fixture_matches_deterministic_example_builder() -> None:
    example = build_agent_transfer_example()
    on_disk = json.loads(FIXTURE.read_text())
    assert on_disk["manifest"]["contentDigest"] == example["manifest"]["contentDigest"]
    assert on_disk["chunks"]["0"]["checksumSha256"] == example["chunks"]["0"]["checksumSha256"]
    assert on_disk["notionPublishedVerified"] is False
    assert on_disk["notionPublishNotPerformed"] is True
    assert on_disk["stockTickerExportVerified"] is True


def test_unchanged_second_read_skips_all_chunks() -> None:
    example = build_agent_transfer_example()
    manifest = example["manifest"]
    plan = plan_chunk_transfer(
        previous_manifest=manifest,
        current_manifest=dict(manifest),
    )
    assert plan.action == "skip_all"
    assert plan.verification == "unchanged_skip"
    assert plan.chunk_indexes == ()


def test_changed_chunk_digest_fetches_only_that_index() -> None:
    example = build_agent_transfer_example()
    previous = dict(example["manifest"])
    current = dict(example["manifest"])
    current["chunkDigests"] = dict(previous["chunkDigests"])
    current["chunkDigests"]["0"] = "0" * 64
    current["contentDigest"] = "1" * 64
    plan = plan_chunk_transfer(previous_manifest=previous, current_manifest=current)
    assert plan.action == "fetch_changed"
    assert plan.chunk_indexes == (0,)


def test_incomplete_quality_aborts() -> None:
    plan = plan_chunk_transfer(
        previous_manifest=None,
        current_manifest={
            "snapshotId": "snap",
            "contentDigest": "abc",
            "qualityStatus": "completed_processing",
        },
    )
    assert plan.action == "abort"
    assert plan.verification == "incomplete_snapshot"


def test_verify_ledger_chunk_accepts_fixture_and_rejects_tamper() -> None:
    example = build_agent_transfer_example()
    manifest = example["manifest"]
    chunk = dict(example["chunks"]["0"])
    assert verify_ledger_chunk(manifest=manifest, chunk=chunk) == "stock_ticker_export_verified"

    tampered = dict(chunk)
    tampered["rows"] = list(chunk["rows"]) + [{"stockId": "TW:FAKE"}]
    assert verify_ledger_chunk(manifest=manifest, chunk=tampered) == "digest_mismatch"

    missing_map = dict(manifest)
    missing_map["chunkDigests"] = {}
    assert verify_ledger_chunk(manifest=missing_map, chunk=chunk) == "digest_mismatch"


def test_classify_session_errors() -> None:
    assert classify_session_error(401) == "auth_required"
    assert classify_session_error(403) == "auth_required"
    assert classify_session_error(404) == "incomplete_snapshot"
    assert classify_session_error(409) == "digest_mismatch"


def test_latest_manifest_requires_auth_when_enabled(monkeypatch) -> None:
    enable_local_auth(monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr("app.main.seed_default_user", lambda: None)
    monkeypatch.setattr("app.main.ensure_bootstrap_local_user", lambda *_a, **_k: None)
    monkeypatch.setattr("app.main.start_enabled_background_jobs", lambda: None)
    monkeypatch.setattr("app.main.stop_background_jobs", lambda: None)

    with TestClient(app) as client:
        response = client.get("/api/tw-research/exports/latest/manifest")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert classify_session_error(response.status_code) == "auth_required"


def test_verified_snapshot_get_paths_do_not_call_history_loader(monkeypatch) -> None:
    """GET latest/chunk must not trigger upstream history (simulated via loader counter)."""
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

    fetch_count = {"n": 0}

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        fetch_count["n"] += 1
        return [], 0

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
        history_loader=loader,
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
    finalized = finalize_verified_snapshot(
        state,
        repository=repo,
        user_id="user-1",
        finalized_at=datetime(2024, 6, 28, 17, 0, tzinfo=timezone.utc),
    )
    assert finalized is not None
    before = fetch_count["n"]

    first = tw_routes.get_latest_verified_manifest(
        universeVersion=state.universe_version,
        tradingDay="2024-06-28",
        user_id="user-1",
    )
    second = tw_routes.get_latest_verified_manifest(
        universeVersion=state.universe_version,
        tradingDay="2024-06-28",
        user_id="user-1",
    )
    assert first.manifest["contentDigest"] == second.manifest["contentDigest"]
    plan = plan_chunk_transfer(
        previous_manifest=first.manifest,
        current_manifest=second.manifest,
    )
    assert plan.action == "skip_all"

    chunk = tw_routes.get_verified_snapshot_chunk(
        finalized["snapshotId"], chunkIndex=0, kind="ledger", user_id="user-1"
    )
    assert (
        verify_ledger_chunk(manifest=first.manifest, chunk=chunk.chunk)
        == "stock_ticker_export_verified"
    )
    assert fetch_count["n"] == before  # no upstream history on GET paths


def test_provisional_unit_benchmark_is_explicitly_not_ubuntu() -> None:
    """#321: provisional marker only; live node metrics remain ubuntu_measurement_pending."""
    example = build_agent_transfer_example()
    digest = content_digest(example["manifest"])
    assert len(digest) == 64
    pilot_status = {
        "provisional_unit_benchmark": True,
        "ubuntu_measurement_pending": True,
        "safe_for_daily_snapshots": None,
    }
    assert pilot_status["ubuntu_measurement_pending"] is True
    assert pilot_status["safe_for_daily_snapshots"] is None
