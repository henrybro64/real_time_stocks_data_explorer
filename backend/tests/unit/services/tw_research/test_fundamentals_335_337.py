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

    # Missing PE on a low basis is incomplete valuation — not a value trap.
    missing_pe = evaluate_relative_price_basis(
        _candles(days=280, start_close=200.0, low_bias=True),
        official_pe=None,
    )
    assert missing_pe.potential_value_trap_flag is False
    assert missing_pe.value_trap_status == "not_evaluated_missing_pe"
    assert missing_pe.official_pe_missing is True
    assert "official_pe_missing" in missing_pe.rationale

    high_pe_trap = evaluate_relative_price_basis(
        _candles(days=280, start_close=200.0, low_bias=True),
        official_pe=45.0,
    )
    assert high_pe_trap.category == "low_relative_price_basis"
    assert high_pe_trap.potential_value_trap_flag is True
    assert high_pe_trap.value_trap_status == "potential_trap"

    low_pe_ok = evaluate_relative_price_basis(
        _candles(days=280, start_close=200.0, low_bias=True),
        official_pe=12.0,
    )
    assert low_pe_ok.potential_value_trap_flag is False
    assert low_pe_ok.value_trap_status == "not_indicated"

    high = evaluate_relative_price_basis(_candles(days=280, start_close=50.0, low_bias=False))
    assert high.category in {"high_relative_price_basis", "mid_relative_price_basis"}

    empty = evaluate_relative_price_basis([])
    assert empty.category == "insufficient_data"
    assert empty.potential_value_trap_flag is False
    assert empty.value_trap_status == "not_applicable"


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


def test_field_level_timeout_marks_failed_and_preserves_ok_fields() -> None:
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
        return _MarginResult(
            history=(),
            summary=None,
            latest_date=None,
            source="fixture",
            empty_reason="empty",
        )

    def ownership_loader(_stock_id: str):
        return _OwnResult(points=(), as_of=None, source="fixture")

    def key_stats_loader(_stock_id: str):
        raise TimeoutError("valuation upstream timed out")

    result = collect_first_pass_batch(
        ["TW:2330"],
        stock_loader=lambda sid: stock if sid == "TW:2330" else None,
        candles_loader=lambda _stock: _candles(days=60, start_close=100.0, low_bias=True),
        institutional_loader=institutional_loader,
        margin_loader=margin_loader,
        ownership_loader=ownership_loader,
        key_stats_loader=key_stats_loader,
        theme_keys_loader=lambda _sid: ["semiconductor"],
    )
    record = result.records[0]
    assert record.fields["institutional_5d"].status == "ok"
    assert record.fields["official_pe"].status == "unavailable"
    assert "timeout" in (record.fields["official_pe"].reason or "").lower()
    assert record.error is not None
    assert "timeout" in record.error.lower()
    # Permanent gaps remain non-retryable markers.
    assert "adapter" in (record.fields["operating_cash_flow"].reason or "") or (
        "not_implemented" in (record.fields["operating_cash_flow"].reason or "")
    )


def test_http_429_field_failure_encodes_retry_after() -> None:
    class RateLimited(Exception):
        def __init__(self) -> None:
            super().__init__("Too Many Requests")
            self.status_code = 429
            self.retry_after = 42

    stock = stock_record(
        "TW:2454",
        market="TW",
        symbol="2454",
        name="聯發科",
        name_zh_tw="聯發科",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )

    def ok_inst(_stock_id: str, _days: int):
        return _InstResult(history=(), latest_date=None, source="fixture")

    def ok_margin(_stock_id: str, _days: int):
        return _MarginResult(
            history=(), summary=None, latest_date=None, source="fixture", empty_reason="empty"
        )

    def ok_own(_stock_id: str):
        return _OwnResult(points=(), as_of=None, source="fixture")

    result = collect_first_pass_batch(
        ["TW:2454"],
        stock_loader=lambda sid: stock if sid == "TW:2454" else None,
        candles_loader=lambda _stock: [],
        institutional_loader=ok_inst,
        margin_loader=ok_margin,
        ownership_loader=ok_own,
        key_stats_loader=lambda _sid: (_ for _ in ()).throw(RateLimited()),
        theme_keys_loader=lambda _sid: [],
    )
    reason = result.records[0].fields["official_pe"].reason or ""
    assert "rate_limited" in reason
    assert "retry_after=42" in reason


def test_per_symbol_isolation_in_ten_symbol_batch() -> None:
    stocks = {
        f"TW:{1000 + index}": stock_record(
            f"TW:{1000 + index}",
            market="TW",
            symbol=str(1000 + index),
            name=f"股票{index}",
            name_zh_tw=f"股票{index}",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
        for index in range(10)
    }
    fail_id = "TW:1003"
    partial_id = "TW:1007"

    def stock_loader(stock_id: str):
        return stocks.get(stock_id)

    def candles_loader(stock):
        if stock.id == fail_id:
            raise RuntimeError("candle store unavailable")
        return _candles(days=40, start_close=80.0, low_bias=True)

    def key_stats_loader(stock_id: str):
        if stock_id == partial_id:
            raise TimeoutError("pe timeout")
        return {
            "peRatio": 12.0,
            "peRatioType": "official_daily",
            "peRatioSource": "fixture",
            "valuationAsOf": "2024-06-27",
            "epsTtm": None,
        }

    def institutional_loader(_stock_id: str, _days: int):
        return _InstResult(history=(), latest_date=None, source="fixture")

    def margin_loader(_stock_id: str, _days: int):
        return _MarginResult(
            history=(), summary=None, latest_date=None, source="fixture", empty_reason="empty"
        )

    def ownership_loader(_stock_id: str):
        return _OwnResult(points=(), as_of=None, source="fixture")

    result = collect_first_pass_batch(
        list(stocks.keys()),
        stock_loader=stock_loader,
        candles_loader=candles_loader,
        institutional_loader=institutional_loader,
        margin_loader=margin_loader,
        ownership_loader=ownership_loader,
        key_stats_loader=key_stats_loader,
        theme_keys_loader=lambda _sid: ["tw-pcb"],
    )
    assert len(result.records) == 10
    by_id = {record.stock_id: record for record in result.records}
    assert by_id[fail_id].fields["latest_completed_close"].status == "unavailable"
    assert "symbol_error" in (by_id[fail_id].fields["latest_completed_close"].reason or "") or (
        "RuntimeError" in (by_id[fail_id].fields["latest_completed_close"].reason or "")
    )
    assert by_id[partial_id].error is not None
    assert by_id[partial_id].fields["official_pe"].status == "unavailable"
    successes = [
        record
        for sid, record in by_id.items()
        if sid not in {fail_id, partial_id} and record.fields["official_pe"].status == "ok"
    ]
    assert len(successes) == 8


def test_first_pass_job_retries_field_level_timeout_and_reuses_ok_fields(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-field-retry")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
    from app.services.tw_research.first_pass_job import FirstPassJobService
    from app.services.tw_research.enrichment import ProvenanceField

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    calls = {"count": 0}

    def fake_collect(ids, **kwargs):
        calls["count"] += 1
        previous = (kwargs.get("previous_fields_by_stock") or {}).get("TW:2330") or {}
        records = []
        for sid in ids:
            if calls["count"] == 1:
                records.append(
                    FirstPassSymbolRecord(
                        stock_id=sid,
                        stock_name="台積電",
                        name_status="ok",
                        name_source="catalog",
                        exchange="TWSE",
                        market="TW",
                        sector_theme_keys=["semiconductor"],
                        fields={
                            "institutional_5d": ProvenanceField(
                                field="institutional_5d",
                                status="ok",
                                source="fixture",
                                captured_at="2024-06-28T00:00:00+00:00",
                                freshness_class="quote_or_eod_day",
                                value={"ok": True},
                            ),
                            "official_pe": ProvenanceField(
                                field="official_pe",
                                status="unavailable",
                                source="key_stats_service",
                                captured_at="2024-06-28T00:00:00+00:00",
                                freshness_class="quote_or_eod_day",
                                reason="transient_timeout:TimeoutError",
                            ),
                            "peg": ProvenanceField(
                                field="peg",
                                status="unavailable",
                                source="none",
                                captured_at="2024-06-28T00:00:00+00:00",
                                freshness_class="earnings_quarterly",
                                reason="verified_eps_growth_denominator_unavailable",
                            ),
                        },
                        error="transient_timeout:TimeoutError",
                        completeness={"fieldsOk": 1, "fieldsUnavailable": 2},
                    )
                )
            else:
                assert previous.get("institutional_5d", {}).get("status") == "ok"
                records.append(
                    FirstPassSymbolRecord(
                        stock_id=sid,
                        stock_name="台積電",
                        name_status="ok",
                        name_source="catalog",
                        exchange="TWSE",
                        market="TW",
                        sector_theme_keys=["semiconductor"],
                        fields={
                            "institutional_5d": ProvenanceField(
                                field="institutional_5d",
                                status="ok",
                                source="reused_prior_ok",
                                captured_at="2024-06-28T00:00:00+00:00",
                                freshness_class="reused",
                                value={"ok": True},
                            ),
                            "official_pe": ProvenanceField(
                                field="official_pe",
                                status="ok",
                                source="key_stats_service",
                                captured_at="2024-06-28T00:00:00+00:00",
                                freshness_class="quote_or_eod_day",
                                value={"peRatio": 18.0},
                            ),
                            "peg": ProvenanceField(
                                field="peg",
                                status="unavailable",
                                source="none",
                                captured_at="2024-06-28T00:00:00+00:00",
                                freshness_class="earnings_quarterly",
                                reason="verified_eps_growth_denominator_unavailable",
                            ),
                        },
                        completeness={"fieldsOk": 2, "fieldsUnavailable": 1},
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
        user_id="user-field-retry",
        stock_ids=["TW:2330"],
        use_membership=False,
    )
    first = service.run_batch(job["job_id"], user_id="user-field-retry", batch_size=1)
    assert first["processed"] == 1
    row = tw_research_repository.get_first_pass_symbol(job["job_id"], "TW:2330")
    assert row is not None
    assert row["status"] == "failed"
    assert row["payload"]["fields"]["institutional_5d"]["status"] == "ok"

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

    second = service.run_batch(job["job_id"], user_id="user-field-retry", batch_size=1)
    assert second["processed"] == 1
    recovered = tw_research_repository.get_first_pass_symbol(job["job_id"], "TW:2330")
    assert recovered is not None
    assert recovered["status"] in {"ok", "partial"}
    assert recovered["payload"]["fields"]["institutional_5d"]["status"] == "ok"
    assert recovered["payload"]["fields"]["official_pe"]["status"] == "ok"
    assert recovered["payload"]["fields"]["peg"]["status"] == "unavailable"
    assert calls["count"] == 2
    tw_research_repository.clear_session_factory_override()


def test_candidate_set_feeds_technical_scan_universe_only(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-tech")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
    from app.services.tw_research.first_pass_durable import (
        create_technical_scan_from_candidate_set,
        persist_first_pass_batch,
        submit_candidate_set,
    )
    from app.services.tw_research.scan import TwResearchScanService

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version="u1",
        requested_count=3,
        collected_count=3,
        deduped_count=3,
        records=[
            FirstPassSymbolRecord(
                stock_id=sid,
                stock_name=sid,
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=[],
            )
            for sid in ("TW:2330", "TW:2454", "TW:2303")
        ],
        coverage_summary={},
        notes=[],
    )
    snap = persist_first_pass_batch(batch, user_id="user-tech", repository=tw_research_repository)
    candidates = submit_candidate_set(
        user_id="user-tech",
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=["TW:2330", "TW:2454"],
        origin="analyst",
        expected_version=0,
        repository=tw_research_repository,
    )

    service = TwResearchScanService(
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            name_zh_tw=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    )
    result = create_technical_scan_from_candidate_set(
        user_id="user-tech",
        expected_version=int(candidates["version"]),
        repository=tw_research_repository,
        scan_service=service,
    )
    scan = result["scan"]
    assert list(scan.stock_ids) == ["TW:2330", "TW:2454"]
    assert "TW:2303" not in scan.stock_ids
    assert any("candidate set" in note.lower() for note in scan.notes)
    assert any("not a notion" in note.lower() for note in scan.notes)
    assert scan.candidate_provenance is not None
    assert scan.candidate_provenance["candidateSetId"] == candidates["candidateSetId"]
    assert scan.candidate_provenance["candidateSetVersion"] == candidates["version"]
    assert scan.candidate_provenance["approvedStockIds"] == ["TW:2330", "TW:2454"]
    assert "candidateProvenance" in scan.snapshot()
    tw_research_repository.clear_session_factory_override()


def test_evidence_search_matches_name_and_sector(monkeypatch) -> None:
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-search")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass_job import FirstPassJobService

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(
        user_id="user-search",
        stock_ids=["TW:2330", "TW:2454"],
        use_membership=False,
    )
    tw_research_repository.upsert_first_pass_symbol(
        job_id=job["job_id"],
        stock_id="TW:2330",
        status="ok",
        payload={
            "stockId": "TW:2330",
            "stockName": "台積電",
            "nameStatus": "ok",
            "sectorThemeKeys": ["semiconductor"],
            "completeness": {"fieldsOk": 3, "fieldsUnavailable": 1},
        },
        lease_generation=0,
    )
    tw_research_repository.upsert_first_pass_symbol(
        job_id=job["job_id"],
        stock_id="TW:2454",
        status="partial",
        payload={
            "stockId": "TW:2454",
            "stockName": "聯發科",
            "nameStatus": "ok",
            "sectorThemeKeys": ["ic-design"],
            "completeness": {"fieldsOk": 1, "fieldsUnavailable": 4},
        },
        lease_generation=0,
    )
    found = service.list_symbols(
        job["job_id"], user_id="user-search", q="台積", offset=0, limit=10
    )
    assert found["total"] == 1
    assert found["records"][0]["stockId"] == "TW:2330"
    assert found["records"][0]["displayLabel"] == "台積電 (2330)"
    sector = service.list_symbols(
        job["job_id"], user_id="user-search", q="ic-design", offset=0, limit=10
    )
    assert sector["total"] == 1
    assert sector["records"][0]["stockId"] == "TW:2454"
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


def _seed_candidate_universe(monkeypatch, user_id: str, stock_ids: list[str]):
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, user_id)
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
    from app.services.tw_research.first_pass_durable import (
        persist_first_pass_batch,
        submit_candidate_set,
    )

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)
    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version="u1",
        requested_count=len(stock_ids),
        collected_count=len(stock_ids),
        deduped_count=len(stock_ids),
        records=[
            FirstPassSymbolRecord(
                stock_id=sid,
                stock_name=sid,
                name_status="ok",
                name_source="catalog",
                exchange="TWSE",
                market="TW",
                sector_theme_keys=[],
            )
            for sid in stock_ids
        ],
        coverage_summary={},
        notes=[],
    )
    snap = persist_first_pass_batch(batch, user_id=user_id, repository=tw_research_repository)
    candidates = submit_candidate_set(
        user_id=user_id,
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=stock_ids[:2] if len(stock_ids) >= 2 else stock_ids,
        origin="analyst",
        expected_version=0,
        repository=tw_research_repository,
    )
    return tw_research_repository, snap, candidates


def test_candidate_scan_rejects_missing_catalog_id(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import (
        CandidateSetValidationError,
        create_technical_scan_from_candidate_set,
    )
    from app.services.tw_research.scan import TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-miss", ["TW:2330", "TW:2454", "TW:2303"]
    )

    def loader(stock_id: str):
        if stock_id == "TW:2454":
            return None
        return stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            name_zh_tw=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )

    service = TwResearchScanService(stock_loader=loader, persist=False)
    with pytest.raises(CandidateSetValidationError) as raised:
        create_technical_scan_from_candidate_set(
            user_id="user-miss",
            expected_version=int(candidates["version"]),
            repository=repo,
            scan_service=service,
        )
    assert raised.value.detail["error"] == "candidate_universe_mismatch"
    assert "TW:2454" in raised.value.detail["missingFromCatalog"]
    assert service.list_scans("user-miss", limit=5) == []
    repo.clear_session_factory_override()


def test_candidate_scan_rejects_empty_and_version_conflict(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import (
        CandidateSetConflict,
        CandidateSetValidationError,
        create_technical_scan_from_candidate_set,
    )
    from app.services.tw_research.scan import TwResearchScanService

    repo, snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-empty", ["TW:2330", "TW:2454"]
    )

    empty = repo.save_candidate_set(
        candidate_set_id="cs_empty",
        user_id="user-empty",
        expected_version=int(candidates["version"]),
        first_pass_snapshot_id=snap.snapshot_id,
        first_pass_digest=snap.content_digest,
        stock_ids=[],
        origin="analyst",
        selection_reasons={},
        decision_date=None,
        notes=[],
    )
    with pytest.raises(CandidateSetValidationError) as empty_exc:
        create_technical_scan_from_candidate_set(
            user_id="user-empty",
            expected_version=int(empty["version"]),
            repository=repo,
            scan_service=TwResearchScanService(persist=False),
        )
    assert empty_exc.value.detail["error"] == "empty_candidate_set"

    with pytest.raises(CandidateSetConflict):
        create_technical_scan_from_candidate_set(
            user_id="user-empty",
            expected_version=0,
            repository=repo,
            scan_service=TwResearchScanService(persist=False),
        )
    repo.clear_session_factory_override()


def test_candidate_scan_rejects_digest_mismatch(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import (
        CandidateSetValidationError,
        create_technical_scan_from_candidate_set,
    )
    from app.services.tw_research.scan import TwResearchScanService

    repo, snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-digest", ["TW:2330", "TW:2454"]
    )
    # Corrupt digest on the stored candidate set row.
    session = repo._session_factory()()
    try:
        from sqlalchemy import select

        from app.db.models import TwResearchCandidateSet

        row = session.execute(
            select(TwResearchCandidateSet).where(
                TwResearchCandidateSet.candidate_set_id == candidates["candidateSetId"]
            )
        ).scalars().first()
        assert row is not None
        row.first_pass_digest = "0" * 64
        session.commit()
    finally:
        session.close()

    with pytest.raises(CandidateSetValidationError) as raised:
        create_technical_scan_from_candidate_set(
            user_id="user-digest",
            expected_version=int(candidates["version"]),
            repository=repo,
            scan_service=TwResearchScanService(
                stock_loader=lambda sid: stock_record(
                    sid,
                    market="TW",
                    symbol=sid.split(":")[-1],
                    name=sid,
                    name_zh_tw=sid,
                    exchange="TWSE",
                    currency="TWD",
                    timezone="Asia/Taipei",
                ),
                persist=False,
            ),
        )
    assert raised.value.detail["error"] == "first_pass_digest_mismatch"
    del snap
    repo.clear_session_factory_override()


def test_infrastructure_failure_is_not_converted_to_unavailable_field() -> None:
    from sqlalchemy.exc import OperationalError

    from app.services.tw_research.first_pass import (
        collect_first_pass_batch,
        is_infrastructure_failure,
    )

    assert is_infrastructure_failure(OperationalError("stmt", {}, Exception("db down")))

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

    def boom(_sid: str):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    with pytest.raises(OperationalError):
        collect_first_pass_batch(
            ["TW:2330", "TW:2454"],
            stock_loader=lambda sid: stock if sid == "TW:2330" else stock_record(
                sid,
                market="TW",
                symbol=sid.split(":")[-1],
                name=sid,
                name_zh_tw=sid,
                exchange="TWSE",
                currency="TWD",
                timezone="Asia/Taipei",
            ),
            candles_loader=lambda _s: _candles(days=40, start_close=90.0, low_bias=True),
            institutional_loader=lambda _sid, _days: _InstResult(
                history=(), latest_date=None, source="fixture"
            ),
            margin_loader=lambda _sid, _days: _MarginResult(
                history=(), summary=None, latest_date=None, source="fixture", empty_reason="empty"
            ),
            ownership_loader=lambda _sid: _OwnResult(points=(), as_of=None, source="fixture"),
            key_stats_loader=boom,
            theme_keys_loader=lambda _sid: [],
        )


def test_provider_timeout_still_isolates_peers() -> None:
    stocks = {
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
        "TW:2454": stock_record(
            "TW:2454",
            market="TW",
            symbol="2454",
            name="聯發科",
            name_zh_tw="聯發科",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    }

    def key_stats(stock_id: str):
        if stock_id == "TW:2330":
            raise TimeoutError("upstream timeout")
        return {
            "peRatio": 15.0,
            "peRatioType": "official_daily",
            "peRatioSource": "fixture",
            "valuationAsOf": "2024-06-27",
            "epsTtm": None,
        }

    result = collect_first_pass_batch(
        ["TW:2330", "TW:2454"],
        stock_loader=lambda sid: stocks[sid],
        candles_loader=lambda _s: _candles(days=40, start_close=90.0, low_bias=True),
        institutional_loader=lambda _sid, _days: _InstResult(
            history=(), latest_date=None, source="fixture"
        ),
        margin_loader=lambda _sid, _days: _MarginResult(
            history=(), summary=None, latest_date=None, source="fixture", empty_reason="empty"
        ),
        ownership_loader=lambda _sid: _OwnResult(points=(), as_of=None, source="fixture"),
        key_stats_loader=key_stats,
        theme_keys_loader=lambda _sid: [],
    )
    by_id = {record.stock_id: record for record in result.records}
    assert by_id["TW:2330"].error is not None
    assert "timeout" in (by_id["TW:2330"].fields["official_pe"].reason or "").lower()
    assert by_id["TW:2454"].fields["official_pe"].status == "ok"


def test_first_pass_job_raises_on_infrastructure_during_collect(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-infra")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass_job import FirstPassJobError, FirstPassJobService

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)

    def boom(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("db down"))

    monkeypatch.setattr(
        "app.services.tw_research.first_pass_job.collect_first_pass_batch",
        boom,
    )
    service = FirstPassJobService(repository=tw_research_repository)
    job = service.create_job(
        user_id="user-infra",
        stock_ids=["TW:2330"],
        use_membership=False,
    )
    with pytest.raises(FirstPassJobError) as raised:
        service.run_batch(job["job_id"], user_id="user-infra", batch_size=1)
    assert "infrastructure" in str(raised.value)
    # Prior job row remains; no false symbol success.
    assert tw_research_repository.get_first_pass_symbol(job["job_id"], "TW:2330") is None
    tw_research_repository.clear_session_factory_override()


def test_candidate_scan_does_not_re_resolve_after_catalog_flip(monkeypatch) -> None:
    """Catalog mutations after the single validation resolve must not alter the scan."""
    from app.services.tw_research.first_pass_durable import create_technical_scan_from_candidate_set
    from app.services.tw_research.scan import TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-race", ["TW:2330", "TW:2454", "TW:2303"]
    )
    catalog = {
        sid: stock_record(
            sid,
            market="TW",
            symbol=sid.split(":")[-1],
            name=sid,
            name_zh_tw=sid,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
        for sid in ("TW:2330", "TW:2454", "TW:2303")
    }
    lookups: list[str] = []

    def flipping_loader(stock_id: str):
        lookups.append(stock_id)
        # After the first full validation pass (2 candidate lookups), disappear 2454.
        candidate_lookups = [sid for sid in lookups if sid in {"TW:2330", "TW:2454"}]
        if len(candidate_lookups) > 2 and stock_id == "TW:2454":
            return None
        return catalog.get(stock_id)

    service = TwResearchScanService(stock_loader=flipping_loader, persist=False)
    result = create_technical_scan_from_candidate_set(
        user_id="user-race",
        expected_version=int(candidates["version"]),
        repository=repo,
        scan_service=service,
    )
    assert list(result["scan"].stock_ids) == ["TW:2330", "TW:2454"]
    # Resolver must not be consulted again during create_scan for membership.
    assert lookups.count("TW:2330") == 1
    assert lookups.count("TW:2454") == 1
    repo.clear_session_factory_override()


def test_candidate_scan_rejects_cross_user_snapshot_access(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import (
        CandidateSetValidationError,
        create_technical_scan_from_candidate_set,
    )
    from app.services.tw_research.scan import TwResearchScanService
    from tests.support import install_in_memory_sessionlocal, seed_test_users

    session_local = install_in_memory_sessionlocal(monkeypatch)
    seed_test_users(session_local, "user-a", "user-b")
    from app.repositories.tw_research_repository import tw_research_repository
    from app.services.tw_research.first_pass import FirstPassBatchResult, FirstPassSymbolRecord
    from app.services.tw_research.first_pass_durable import (
        persist_first_pass_batch,
        submit_candidate_set,
    )

    tw_research_repository.clear_session_factory_override()
    tw_research_repository.set_session_factory_override(session_local)
    batch = FirstPassBatchResult(
        first_pass_version="first-pass-v1",
        universe_version="u1",
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
                sector_theme_keys=[],
            )
        ],
        coverage_summary={},
        notes=[],
    )
    snap_a = persist_first_pass_batch(batch, user_id="user-a", repository=tw_research_repository)
    # user-b cannot pin to user-a's snapshot via submit; create scan for user-b
    # with no candidate set of their own.
    with pytest.raises(KeyError):
        create_technical_scan_from_candidate_set(
            user_id="user-b",
            repository=tw_research_repository,
            scan_service=TwResearchScanService(persist=False),
        )
    # user-a can submit; snapshot ownership is enforced on get.
    submit_candidate_set(
        user_id="user-a",
        first_pass_snapshot_id=snap_a.snapshot_id,
        first_pass_digest=snap_a.content_digest,
        stock_ids=["TW:2330"],
        origin="analyst",
        expected_version=0,
        repository=tw_research_repository,
    )
    assert (
        tw_research_repository.get_first_pass_snapshot(snap_a.snapshot_id, user_id="user-b")
        is None
    )
    del CandidateSetValidationError
    tw_research_repository.clear_session_factory_override()


def test_candidate_scan_atomic_provenance_fails_closed(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import create_technical_scan_from_candidate_set
    from app.services.tw_research.scan import TwResearchDurableStoreError, TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-atom", ["TW:2330", "TW:2454"]
    )

    class FailingScanService(TwResearchScanService):
        def create_scan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise TwResearchDurableStoreError(
                "Durable scan write failed.",
                scan_id="scan_x",
                operation="persist_scan",
            )

    with pytest.raises(TwResearchDurableStoreError):
        create_technical_scan_from_candidate_set(
            user_id="user-atom",
            expected_version=int(candidates["version"]),
            repository=repo,
            scan_service=FailingScanService(
                stock_loader=lambda sid: stock_record(
                    sid,
                    market="TW",
                    symbol=sid.split(":")[-1],
                    name=sid,
                    name_zh_tw=sid,
                    exchange="TWSE",
                    currency="TWD",
                    timezone="Asia/Taipei",
                ),
                persist=True,
            ),
        )
    repo.clear_session_factory_override()


def _fixture_history(*, n: int, rising: bool) -> list[CandleRecord]:
    end = datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
    dates: list[datetime] = []
    offset = 0
    while len(dates) < n:
        day = end - timedelta(days=offset)
        offset += 1
        if day.weekday() >= 5:
            continue
        dates.append(day.replace(hour=13, minute=30, second=0, microsecond=0))
    dates.reverse()
    out: list[CandleRecord] = []
    for index, stamp in enumerate(dates):
        level = (100.0 + index) if rising else (300.0 - index * 0.4)
        out.append(
            CandleRecord(
                time=stamp.isoformat(),
                open=level - 0.5,
                high=level + 1.0,
                low=level - 1.0,
                close=level,
                volume=1000 + index,
            )
        )
    return out


def test_candidate_technical_scan_monthly_before_daily_on_fixture_history(monkeypatch) -> None:
    """Candidate-derived scans use versioned Stage A monthly → Stage B daily (#336).

    Daily runs only after monthly pass. Qualification is technical evidence only —
    not a Notion report recommendation. No live providers.
    """
    from app.services.tw_research.constants import (
        CALCULATION_VERSION_MONTHLY_FIRST,
        MONTHLY_FIRST_DAILY_CONTINUATION_POLICY,
        STAGE_ORDER_MONTHLY_FIRST,
    )
    from app.services.tw_research.first_pass_durable import create_technical_scan_from_candidate_set
    from app.services.tw_research.scan import TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-macd", ["TW:2330", "TW:2454", "TW:2303"]
    )
    # ≥35 completed months required for monthly MACD/KD quality=ok.
    history = _fixture_history(n=900, rising=True)

    def loader(stock, limit, *, max_month_fetches):  # noqa: ANN001
        del stock, limit, max_month_fetches
        return list(history), 0

    service = TwResearchScanService(
        history_loader=loader,
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            name_zh_tw=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        persist=False,
    )
    created = create_technical_scan_from_candidate_set(
        user_id="user-macd",
        expected_version=int(candidates["version"]),
        repository=repo,
        scan_service=service,
        trading_day="2024-06-28",
    )
    state = created["scan"]
    assert list(state.stock_ids) == ["TW:2330", "TW:2454"]
    assert "TW:2303" not in state.stock_ids
    assert state.calculation_version == CALCULATION_VERSION_MONTHLY_FIRST
    assert state.candidate_provenance is not None
    assert state.candidate_provenance["stageOrder"] == STAGE_ORDER_MONTHLY_FIRST
    assert (
        state.candidate_provenance["dailyContinuationPolicy"]
        == MONTHLY_FIRST_DAILY_CONTINUATION_POLICY
    )
    assert state.snapshot()["stageOrder"] == STAGE_ORDER_MONTHLY_FIRST
    assert any("monthly" in note.lower() and "daily" in note.lower() for note in state.notes)
    assert any("not a notion" in note.lower() for note in state.notes)

    state = service.run_batch(
        state.scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert set(state.records) == {"TW:2330", "TW:2454"}
    monthly_passed = 0
    snap = state.snapshot()
    assert snap["stageProgressSemanticsVersion"] == "stage-progress-v1"
    assert snap["stageOrder"] == STAGE_ORDER_MONTHLY_FIRST
    for stock_id, record in state.records.items():
        assert record.monthly is not None, stock_id
        assert record.monthly.get("quality") == "ok", stock_id
        if record.monthly.get("passStatus") == "pass":
            monthly_passed += 1
            assert record.stage == "B"
            assert record.daily is not None
            assert stock_id in snap["monthPassIds"]
            assert stock_id in snap["firstStagePassIds"]
            # dayPassIds means daily pass only — not the monthly gate.
            if record.daily.get("passStatus") == "pass":
                assert stock_id in snap["dayPassIds"]
                assert stock_id in state.day_pass_ids
            else:
                assert stock_id not in snap["dayPassIds"]
        else:
            assert record.stage == "A"
            assert record.daily is None
            assert (record.coverage or {}).get("daily", {}).get("deferred") is True
            assert stock_id not in snap["dayPassIds"]
            assert stock_id not in snap["monthPassIds"]
    assert monthly_passed >= 1
    assert snap["monthPassCount"] == monthly_passed
    assert snap["firstStagePassCount"] == monthly_passed
    assert all(sid in state.stock_ids for sid in state.qualified_ids)
    repo.clear_session_factory_override()


def test_candidate_monthly_fail_retains_monthly_and_defers_daily(monkeypatch) -> None:
    from dataclasses import replace

    from app.services.tw_research.first_pass_durable import create_technical_scan_from_candidate_set
    from app.services.tw_research.indicators import monthly_macd_kd_snapshot
    from app.services.tw_research.scan import TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-mfail", ["TW:2330", "TW:2454"]
    )
    history = _fixture_history(n=900, rising=True)

    def failing_monthly(candles, *, market="TW", now=None):  # noqa: ANN001
        snap = monthly_macd_kd_snapshot(candles, market=market, now=now)
        assert snap.quality == "ok"
        return replace(snap, pass_status="fail")

    monkeypatch.setattr(
        "app.services.tw_research.scan.monthly_macd_kd_snapshot",
        failing_monthly,
    )

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: (list(history), 0),
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            name_zh_tw=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        persist=False,
    )
    created = create_technical_scan_from_candidate_set(
        user_id="user-mfail",
        expected_version=int(candidates["version"]),
        repository=repo,
        scan_service=service,
        trading_day="2024-06-28",
    )
    state = service.run_batch(
        created["scan"].scan_id,
        batch_size=2,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert state.records
    for record in state.records.values():
        assert record.monthly is not None
        assert record.monthly.get("passStatus") == "fail"
        assert record.stage == "A"
        assert record.daily is None
        daily_cov = (record.coverage or {}).get("daily") or {}
        assert daily_cov.get("deferred") is True
        assert daily_cov.get("reason") == "monthly_screen_failed"
    assert state.qualified_ids == []
    repo.clear_session_factory_override()


def test_candidate_incomplete_monthly_is_not_screening_fail(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import create_technical_scan_from_candidate_set
    from app.services.tw_research.scan import TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-minc", ["TW:2330"]
    )
    short = _fixture_history(n=15, rising=True)

    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: (list(short), 0),
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            name_zh_tw=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        persist=False,
    )
    created = create_technical_scan_from_candidate_set(
        user_id="user-minc",
        expected_version=int(candidates["version"]),
        repository=repo,
        scan_service=service,
        trading_day="2024-06-28",
    )
    state = service.run_batch(
        created["scan"].scan_id,
        batch_size=1,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    record = state.records["TW:2330"]
    assert record.monthly is not None
    assert record.monthly.get("passStatus") == "unknown"
    assert record.monthly.get("quality") in {"insufficient_history", "unavailable"}
    assert record.daily is None
    assert record.readiness == "source_incomplete"
    assert (record.coverage or {}).get("daily", {}).get("reason") == (
        "monthly_indicators_incomplete"
    )
    repo.clear_session_factory_override()


def test_candidate_monthly_first_scan_resumes_after_interrupt(monkeypatch) -> None:
    from app.services.tw_research.first_pass_durable import create_technical_scan_from_candidate_set
    from app.services.tw_research.scan import TwResearchScanService

    repo, _snap, candidates = _seed_candidate_universe(
        monkeypatch, "user-resume", ["TW:2330", "TW:2454"]
    )
    history = _fixture_history(n=900, rising=True)
    service = TwResearchScanService(
        history_loader=lambda stock, limit, *, max_month_fetches: (list(history), 0),
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[-1],
            name=stock_id,
            name_zh_tw=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
        persist=False,
    )
    created = create_technical_scan_from_candidate_set(
        user_id="user-resume",
        expected_version=int(candidates["version"]),
        repository=repo,
        scan_service=service,
        trading_day="2024-06-28",
    )
    first = service.run_batch(
        created["scan"].scan_id,
        batch_size=1,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert first.cursor == 1
    assert len(first.records) == 1
    second = service.run_batch(
        first.scan_id,
        batch_size=1,
        now=datetime(2024, 6, 28, 16, 5, tzinfo=TZ),
    )
    assert second.cursor == 2
    assert set(second.records) == {"TW:2330", "TW:2454"}
    for record in second.records.values():
        assert record.monthly is not None
    repo.clear_session_factory_override()
