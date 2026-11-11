"""Fundamental-first audit, first-pass, and relative price basis (#335–#337)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.quote import CandleRecord
from app.services.tw_research.contract import build_research_contract, load_feasibility_matrix
from app.services.tw_research.constants import (
    CONTRACT_VERSION,
    FIRST_PASS_VERSION,
    RELATIVE_PRICE_BASIS_VERSION,
)
from app.services.tw_research.first_pass import collect_first_pass_batch
from app.services.tw_research.fundamentals_watchlist import (
    ResearchMembershipConflict,
    research_membership_store,
)
from app.services.tw_research.price_basis import evaluate_relative_price_basis
from tests.support import stock_record

TZ = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class _InstRow:
    date: str
    foreign_net_shares: int
    investment_trust_net_shares: int
    dealer_net_shares: int
    total_net_shares: int


@dataclass(frozen=True)
class _InstResult:
    history: tuple
    latest_date: str | None
    source: str


@dataclass(frozen=True)
class _MarginRow:
    date: str
    margin_balance_lots: float | None


@dataclass(frozen=True)
class _MarginResult:
    history: tuple
    summary: _MarginRow | None
    latest_date: str | None
    source: str
    empty_reason: str | None = None


@dataclass(frozen=True)
class _OwnPoint:
    date: str
    holders_400_lots_or_more_percent: float | None


@dataclass(frozen=True)
class _OwnResult:
    points: tuple
    as_of: str | None
    source: str
    stale: bool = False


def _candles(*, days: int, start_close: float = 100.0, low_bias: bool = False) -> list[CandleRecord]:
    start = datetime(2024, 1, 2, 13, 30, tzinfo=TZ)
    out: list[CandleRecord] = []
    for index in range(days):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        # Drift down for low relative basis, or mild up for mid/high.
        close = start_close - (0.4 * index if low_bias else -0.05 * index)
        high = close + 2
        low = close - 2
        out.append(
            CandleRecord(
                time=day.isoformat(),
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1_000,
            )
        )
    return out


def test_contract_includes_fundamental_audit_fields() -> None:
    from app.services.tw_research import contract as contract_mod

    contract_mod._field_rows.cache_clear()
    contract_mod.load_feasibility_matrix.cache_clear()

    contract = build_research_contract()
    assert contract.contractVersion == CONTRACT_VERSION
    assert contract.firstPassVersion == FIRST_PASS_VERSION
    assert contract.relativePriceBasisVersion == RELATIVE_PRICE_BASIS_VERSION
    fields = {row.field: row for row in contract.fieldMap}
    assert "stock_name" in fields
    assert fields["stock_name"].status == "implemented"
    assert fields["monthly_revenue_yoy_mom"].missing is True
    assert fields["monthly_revenue_yoy_mom"].status == "not_implemented"
    assert fields["relative_price_basis"].status == "implemented"
    assert fields["ttm_eps_pe_peg"].missing is True
    assert any("synthetic" in note.lower() for note in contract.notes)
    matrix = load_feasibility_matrix()
    assert len(matrix) >= 10
    by_field = {row["field"]: row for row in matrix}
    assert by_field["monthly_revenue_yoy_mom"]["implementation"] == "not_implemented"
    assert "SYNTHETIC" in by_field["monthly_revenue_yoy_mom"]["provider"]


def test_relative_price_basis_low_mid_high_and_insufficient() -> None:
    low = evaluate_relative_price_basis(_candles(days=280, start_close=200.0, low_bias=True))
    assert low.category == "low_relative_price_basis"
    assert low.week52_position is not None
    assert low.week52_position <= 0.30
    assert low.drawdown_from_week52_high is not None
    assert low.valuation_context_status == "unavailable"
    assert low.multi_year_price_location_status == "not_calculated"
    assert "not cheap valuation" in low.rationale

    # Missing PE on a low basis raises potential value-trap flag (evidence only).
    trap = evaluate_relative_price_basis(
        _candles(days=280, start_close=200.0, low_bias=True),
        official_pe=None,
    )
    assert trap.potential_value_trap_flag is True

    high = evaluate_relative_price_basis(_candles(days=280, start_close=50.0, low_bias=False))
    assert high.category in {"high_relative_price_basis", "mid_relative_price_basis"}

    empty = evaluate_relative_price_basis([])
    assert empty.category == "insufficient_data"


def test_relative_price_basis_is_deterministic() -> None:
    candles = _candles(days=120, start_close=80.0, low_bias=True)
    first = evaluate_relative_price_basis(candles, official_pe=12.0)
    second = evaluate_relative_price_basis(candles, official_pe=12.0)
    assert first.as_dict() == second.as_dict()


def test_first_pass_dedupes_before_collection_and_keeps_names() -> None:
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="台積電",
        name_zh_tw="台積電",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    calls = {"key_stats": 0}

    def stock_loader(stock_id: str):
        return stock if stock_id == "TW:2330" else None

    def key_stats_loader(_stock_id: str):
        calls["key_stats"] += 1
        return {
            "peRatio": 18.5,
            "peRatioType": "official_daily",
            "peRatioSource": "TWSE official daily P/E",
            "valuationAsOf": "2024-06-27",
            "epsTtm": None,
        }

    def institutional_loader(_stock_id: str, _days: int):
        rows = tuple(
            _InstRow(
                date=f"2024-06-{day:02d}",
                foreign_net_shares=1000,
                investment_trust_net_shares=0,
                dealer_net_shares=0,
                total_net_shares=1000,
            )
            for day in range(20, 25)
        )
        return _InstResult(history=rows, latest_date="2024-06-24", source="fixture")

    def margin_loader(_stock_id: str, _days: int):
        rows = tuple(_MarginRow(date=f"2024-06-{day:02d}", margin_balance_lots=10.0) for day in range(20, 25))
        return _MarginResult(
            history=rows,
            summary=_MarginRow(date="2024-06-24", margin_balance_lots=10.0),
            latest_date="2024-06-24",
            source="fixture",
        )

    def ownership_loader(_stock_id: str):
        return _OwnResult(
            points=(_OwnPoint(date="2024-06-14", holders_400_lots_or_more_percent=40.0),
                    _OwnPoint(date="2024-06-21", holders_400_lots_or_more_percent=41.0)),
            as_of="2024-06-21",
            source="fixture",
        )

    result = collect_first_pass_batch(
        ["TW:2330", "2330", "TW:2330"],
        stock_loader=stock_loader,
        candles_loader=lambda _stock: _candles(days=60, start_close=100.0, low_bias=True),
        institutional_loader=institutional_loader,
        margin_loader=margin_loader,
        ownership_loader=ownership_loader,
        key_stats_loader=key_stats_loader,
        theme_keys_loader=lambda _sid: ["semiconductor"],
        include_nonofficial_margins=False,
    )
    assert result.requested_count == 3
    assert result.deduped_count == 1
    assert calls["key_stats"] == 1
    assert len(result.records) == 1
    record = result.records[0]
    assert record.stock_name == "台積電"
    assert record.name_status == "ok"
    assert record.fields["official_pe"].status == "ok"
    assert record.fields["monthly_revenue_yoy_mom"].status == "unavailable"
    assert "synthetic" in (record.fields["monthly_revenue_yoy_mom"].reason or "").lower()
    assert record.fields["peg"].status == "unavailable"
    assert record.fields["gross_operating_net_margins"].reason is not None
    assert "not_fetched" in (record.fields["gross_operating_net_margins"].reason or "")
    assert record.fields["second_pass_macd_kd"].reason is not None
    assert record.relative_price_basis is not None
    assert record.completeness["fundamentalComplete"] is False
    assert result.coverage_summary["symbolsWithOfficialPe"] == 1


def test_first_pass_never_fabricates_missing_name() -> None:
    stock = stock_record(
        "TW:9991",
        market="TW",
        symbol="9991",
        name="",
        name_zh_tw=None,
        exchange="TPEx",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    result = collect_first_pass_batch(
        ["TW:9991"],
        stock_loader=lambda sid: stock if sid == "TW:9991" else None,
        candles_loader=lambda _stock: [],
        institutional_loader=lambda *_a, **_k: _InstResult((), None, "fixture"),
        margin_loader=lambda *_a, **_k: _MarginResult((), None, None, "fixture", "empty"),
        ownership_loader=lambda *_a, **_k: _OwnResult((), None, "fixture"),
        key_stats_loader=lambda *_a, **_k: {},
        theme_keys_loader=lambda *_a, **_k: [],
    )
    record = result.records[0]
    assert record.stock_name is None
    assert record.name_status == "unavailable"
    assert record.fields["latest_completed_close"].status == "unavailable"
    assert record.relative_price_basis["category"] == "insufficient_data"


def test_membership_save_is_version_checked_and_dedupes_subset() -> None:
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="TSMC",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="Hon Hai",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    store = research_membership_store.__class__(
        persist=False,
        stock_resolver=catalog.get,
    )
    store.clear_for_tests()
    user = "user-fundamentals-test"
    initial = store.get(user)
    assert initial.version == 0

    saved = store.save(
        user,
        expected_version=0,
        theme_keys=[],
        extra_stock_ids=["TW:2330", "2330", "TW:2317"],
        compute_subset_ids=["TW:2330", "TW:2330", "TW:9999"],
    )
    assert saved.version == 1
    assert saved.extra_stock_ids == ("TW:2330", "TW:2317")
    assert set(saved.resolved_stock_ids) == {"TW:2330", "TW:2317"}
    # Orphan 9999 dropped; 2330 deduped.
    assert "TW:9999" not in saved.compute_subset_ids
    assert saved.compute_subset_ids.count("TW:2330") == 1

    with pytest.raises(ResearchMembershipConflict):
        store.save(
            user,
            expected_version=0,
            extra_stock_ids=["TW:2454"],
        )

    reset = store.reset(user, expected_version=1)
    assert reset.version == 2
    assert reset.extra_stock_ids == ()


def test_durable_membership_and_candidate_set_round_trip(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-fp-durable")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import (
        FirstPassBatchResult,
        FirstPassSymbolRecord,
    )
    from app.services.tw_research.first_pass_durable import (
        CandidateSetValidationError,
        persist_first_pass_batch,
        submit_candidate_set,
        latest_candidate_set,
    )
    from app.services.tw_research.fundamentals_watchlist import (
        ResearchMembershipConflict,
        ResearchMembershipStore,
    )

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="台積電",
            name_zh_tw="台積電",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        "TW:2317": stock_record(
            "TW:2317",
            market="TW",
            symbol="2317",
            name="鴻海",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }
    store = ResearchMembershipStore(
        persist=True,
        stock_resolver=catalog.get,
        repository=tw_research_repository,
    )
    user = "user-fp-durable"
    saved = store.save(
        user,
        expected_version=0,
        extra_stock_ids=["TW:2330", "TW:2317"],
        compute_subset_ids=["TW:2330"],
    )
    assert saved.durable is True
    assert saved.version == 1
    assert saved.extra_stock_ids == ("TW:2330", "TW:2317")

    with pytest.raises(ResearchMembershipConflict):
        store.save(user, expected_version=0, extra_stock_ids=["TW:2330"])

    reloaded = store.get(user)
    assert reloaded.version == 1
    assert reloaded.compute_subset_ids == ("TW:2330",)

    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version="test-u",
        requested_count=2,
        collected_count=2,
        deduped_count=2,
        records=[
            FirstPassSymbolRecord(
                stock_id="TW:2330",
                stock_name="台積電",
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=["semiconductor"],
            ),
            FirstPassSymbolRecord(
                stock_id="TW:2317",
                stock_name="鴻海",
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=[],
            ),
        ],
        coverage_summary={"symbolsWithOfficialPe": 0},
        notes=[],
    )
    snap = persist_first_pass_batch(batch, user_id=user, repository=tw_research_repository)
    assert snap.snapshot_id.startswith("fp_")
    again = persist_first_pass_batch(batch, user_id=user, repository=tw_research_repository)
    assert again.snapshot_id == snap.snapshot_id  # idempotent by digest

    with pytest.raises(CandidateSetValidationError):
        submit_candidate_set(
            user_id=user,
            first_pass_snapshot_id=snap.snapshot_id,
            first_pass_digest=snap.content_digest,
            stock_ids=["TW:9999"],
            origin="analyst",
            expected_version=0,
            repository=tw_research_repository,
        )

    candidates = submit_candidate_set(
        user_id=user,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2330", "TW:2330"],
        origin="analyst",
        expected_version=0,
        repository=tw_research_repository,
    )
    assert candidates["version"] == 1
    assert candidates["stockIds"] == ["TW:2330"]
    assert candidates["notNotionReportList"] is True
    latest = latest_candidate_set(user_id=user, repository=tw_research_repository)
    assert latest is not None
    assert latest["candidateSetId"] == candidates["candidateSetId"]

    tw_research_repository.clear_session_factory_override()


def test_relative_price_basis_marks_sparse_and_stale_history() -> None:
    sparse = evaluate_relative_price_basis(_candles(days=20, start_close=100.0, low_bias=True))
    assert sparse.history_coverage_status in {"sparse", "partial", "insufficient"}
    assert sparse.corporate_action_status == "unknown_unadjusted"

    # Force stale by anchoring now far in the future.
    stale = evaluate_relative_price_basis(
        _candles(days=120, start_close=100.0, low_bias=True),
        now=datetime(2030, 1, 1, tzinfo=TZ),
    )
    assert stale.stale_history_flag is True


def test_editable_sector_membership_add_remove_and_preview() -> None:
    catalog = {
        "TW:2330": stock_record(
            "TW:2330", market="TW", symbol="2330", name="台積電",
            name_zh_tw="台積電", exchange="TWSE", currency="TWD", timezone="Asia/Taipei",
        ),
        "TW:2454": stock_record(
            "TW:2454", market="TW", symbol="2454", name="聯發科",
            name_zh_tw="聯發科", exchange="TWSE", currency="TWD", timezone="Asia/Taipei",
        ),
        "TW:2727": stock_record(
            "TW:2727", market="TW", symbol="2727", name="王品",
            name_zh_tw="王品", exchange="TWSE", currency="TWD", timezone="Asia/Taipei",
        ),
    }
    store = research_membership_store.__class__(persist=False, stock_resolver=catalog.get)
    store.clear_for_tests()
    user = "user-sector-edit"
    preview = store.preview(
        user,
        theme_keys=["tw-ic-design"],
        sector_additions={"tw-ic-design": ["TW:2727"]},
        sector_removals={"tw-ic-design": ["TW:2454"]},
        extra_stock_ids=["TW:2330"],
    )
    assert preview.preview is True
    assert "TW:2727" in preview.sector_membership.get("tw-ic-design", ())
    assert "TW:2454" not in preview.sector_membership.get("tw-ic-design", ())
    assert "TW:2330" in preview.resolved_stock_ids

    saved = store.save(
        user,
        expected_version=0,
        theme_keys=["tw-ic-design"],
        sector_additions={"tw-ic-design": ["TW:2727"]},
        sector_removals={"tw-ic-design": ["TW:2454"]},
        extra_stock_ids=["TW:2330"],
    )
    assert saved.version == 1
    assert saved.preview is False
    reset = store.reset(user, expected_version=1)
    assert reset.version == 2
    assert reset.sector_additions == {}


def test_resumable_first_pass_job_beyond_batch_limit(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-fp-job")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import FirstPassSymbolRecord
    from app.services.tw_research.first_pass_job import FirstPassJobService

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    # Build a universe larger than 80 symbols.
    stock_ids = [f"TW:{1000 + i}" for i in range(90)]

    def fake_collect(ids, **kwargs):
        from app.services.tw_research.first_pass import FirstPassBatchResult

        records = [
            FirstPassSymbolRecord(
                stock_id=sid,
                stock_name=sid,
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=[],
                completeness={"fieldsOk": 1, "fieldsUnavailable": 0},
            )
            for sid in ids
        ]
        return FirstPassBatchResult(
            first_pass_version="first-pass-v1",
            universe_version="explicit:90",
            requested_count=len(ids),
            collected_count=len(ids),
            deduped_count=len(ids),
            records=records,
            coverage_summary={},
            notes=[],
        )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        fake_collect,
    )
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(user_id="user-fp-job", stock_ids=stock_ids, use_membership=False)
    assert job["universe_size"] == 90
    assert job["status"] == "pending"

    total_processed = 0
    for _ in range(20):
        result = service.run_batch(job["job_id"], user_id="user-fp-job", batch_size=10)
        total_processed += int(result["processed"])
        if result["job"]["status"] in {"completed", "partial", "deferred"} and result[
            "alreadyComplete"
        ] or result["job"].get("status") == "completed":
            if result["job"]["cursor"] >= 90 or result["alreadyComplete"]:
                break
        if result["job"]["cursor"] >= 90:
            break

    progress = service.progress(job["job_id"], user_id="user-fp-job")
    assert progress["processedCount"] == 90
    assert progress["pendingCount"] == 0
    assert progress["universeSize"] == 90
    final = service.get_job(job["job_id"], user_id="user-fp-job")
    assert final["status"] in {"completed", "partial", "deferred"}
    assert final["active_snapshot_id"]

    # Ownership isolation for snapshot retrieval.
    snap = tw_research_repository.get_first_pass_snapshot(
        final["active_snapshot_id"], user_id="user-fp-job"
    )
    assert snap is not None
    denied = tw_research_repository.get_first_pass_snapshot(
        final["active_snapshot_id"], user_id="other-user"
    )
    assert denied is None

    tw_research_repository.clear_session_factory_override()


def test_first_pass_snapshot_ids_are_unique_across_users(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-a", "user-b")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import (
        FirstPassBatchResult,
        FirstPassSymbolRecord,
    )
    from app.services.tw_research.first_pass_durable import persist_first_pass_batch

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version="shared-u",
        requested_count=1,
        collected_count=1,
        deduped_count=1,
        records=[
            FirstPassSymbolRecord(
                stock_id="TW:2330",
                stock_name="台積電",
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=["semiconductor"],
            )
        ],
        coverage_summary={},
        notes=[],
    )
    snap_a = persist_first_pass_batch(batch, user_id="user-a", repository=tw_research_repository)
    snap_b = persist_first_pass_batch(batch, user_id="user-b", repository=tw_research_repository)
    assert snap_a.content_digest == snap_b.content_digest
    assert snap_a.snapshot_id != snap_b.snapshot_id
    assert tw_research_repository.get_first_pass_snapshot(
        snap_a.snapshot_id, user_id="user-b"
    ) is None
    again_a = persist_first_pass_batch(batch, user_id="user-a", repository=tw_research_repository)
    assert again_a.snapshot_id == snap_a.snapshot_id
    tw_research_repository.clear_session_factory_override()


def test_first_pass_retries_transient_failures_with_backoff(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-retry")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
    from app.services.tw_research.first_pass_job import FirstPassJobService

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    calls = {"count": 0}

    def fake_collect(ids, **kwargs):
        calls["count"] += 1
        records = []
        for sid in ids:
            if calls["count"] == 1:
                records.append(
                    FirstPassSymbolRecord(
                        stock_id=sid,
                        stock_name=sid,
                        name_status="ok",
                        name_source="catalog",
                        exchange="TWSE",
                        market="TW",
                        sector_theme_keys=[],
                        error="timeout: upstream timed out",
                        completeness={"fieldsOk": 0, "fieldsUnavailable": 1},
                    )
                )
            else:
                records.append(
                    FirstPassSymbolRecord(
                        stock_id=sid,
                        stock_name=sid,
                        name_status="ok",
                        name_source="catalog",
                        exchange="TWSE",
                        market="TW",
                        sector_theme_keys=[],
                        completeness={"fieldsOk": 1, "fieldsUnavailable": 0},
                    )
                )
        return FirstPassBatchResult(
            first_pass_version="first-pass-v1",
            universe_version="explicit:1",
            requested_count=len(ids),
            collected_count=len(ids),
            deduped_count=len(ids),
            records=records,
            coverage_summary={},
            notes=[],
        )

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        fake_collect,
    )
    # Force immediate retry eligibility (no wall-clock wait in unit test).
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job._FIRST_PASS_RETRY_BASE_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job._FIRST_PASS_RETRY_CAP_SECONDS",
        0,
    )

    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(
        user_id="user-retry",
        stock_ids=["TW:2330"],
        use_membership=False,
    )
    first = service.run_batch(job["job_id"], user_id="user-retry", batch_size=1)
    assert first["processed"] == 1
    row = tw_research_repository.get_first_pass_symbol(job["job_id"], "TW:2330")
    assert row is not None
    assert row["status"] == "failed"
    assert row["payload"]["attemptCount"] == 1

    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.db.models import TwResearchFirstPassJob, TwResearchFirstPassSymbolResult

    session = session_local()
    try:
        fp_job = session.get(TwResearchFirstPassJob, job["job_id"])
        assert fp_job is not None
        fp_job.reserve_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        sym = session.execute(
            select(TwResearchFirstPassSymbolResult).where(
                TwResearchFirstPassSymbolResult.job_id == job["job_id"],
                TwResearchFirstPassSymbolResult.stock_id == "TW:2330",
            )
        ).scalars().first()
        assert sym is not None
        payload = dict(sym.payload_json or {})
        payload["nextRetryAt"] = "1970-01-01T00:00:00+00:00"
        sym.payload_json = payload
        session.commit()
    finally:
        session.close()

    second = service.run_batch(job["job_id"], user_id="user-retry", batch_size=1)
    assert second["processed"] == 1
    recovered = tw_research_repository.get_first_pass_symbol(job["job_id"], "TW:2330")
    assert recovered is not None
    assert recovered["status"] == "ok"
    assert calls["count"] == 2
    tw_research_repository.clear_session_factory_override()


def test_membership_occ_uses_version_predicate(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-occ")
    from app.repositories.tw_research_repository import tw_research_repository

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    first = tw_research_repository.save_membership(
        "user-occ",
        expected_version=0,
        theme_keys=["tw-pcb"],
        extra_stock_ids=[],
        compute_subset_ids=[],
    )
    assert first["version"] == 1
    with pytest.raises(ValueError):
        tw_research_repository.save_membership(
            "user-occ",
            expected_version=0,
            theme_keys=["tw-thermal"],
            extra_stock_ids=[],
            compute_subset_ids=[],
        )
    second = tw_research_repository.save_membership(
        "user-occ",
        expected_version=1,
        theme_keys=["tw-thermal"],
        extra_stock_ids=["TW:2330"],
        compute_subset_ids=[],
        sector_additions={"tw-thermal": ["TW:2330"]},
        sector_removals={"tw-thermal": []},
    )
    assert second["version"] == 2
    assert second["sector_additions"]["tw-thermal"] == ["TW:2330"]
    tw_research_repository.clear_session_factory_override()
