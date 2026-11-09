"""Enrichment and export coverage for #313 / #314 / #315."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.tw_research.enrichment import (
    EnrichmentResult,
    ProvenanceField,
    enrich_qualified_symbol,
    is_technically_qualified,
)
from app.services.tw_research.export import (
    build_browser_text_view,
    build_export_chunk,
    build_export_csv,
    build_export_manifest,
)
from app.services.tw_research.scan import (
    SymbolScanRecord,
    TwResearchScanService,
)
from app.services.tw_research.universe import UniverseSelector
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


def _qualified_record(stock_id: str) -> SymbolScanRecord:
    return SymbolScanRecord(
        stock_id=stock_id,
        stage="B",
        daily={"passStatus": "pass", "asOf": "2024-06-27"},
        monthly={"passStatus": "pass", "asOf": "2024-05-31"},
        theme_keys=(),
    )


def test_is_technically_qualified_requires_day_and_month_pass() -> None:
    assert is_technically_qualified(_qualified_record("TW:2727"))
    assert not is_technically_qualified(
        SymbolScanRecord(
            stock_id="TW:2727",
            stage="B",
            daily={"passStatus": "pass"},
            monthly={"passStatus": "fail"},
        )
    )
    assert not is_technically_qualified(
        SymbolScanRecord(stock_id="TW:2727", stage="A", daily={"passStatus": "pass"})
    )


def test_enrichment_marks_unavailable_fields_without_inventing_peg() -> None:
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
    inst_rows = tuple(
        _InstRow(
            date=f"2024-06-{day:02d}",
            foreign_net_shares=1000 * day,
            investment_trust_net_shares=100 * day,
            dealer_net_shares=50 * day,
            total_net_shares=1150 * day,
        )
        for day in range(20, 25)
    )

    result = enrich_qualified_symbol(
        "TW:2727",
        stock_loader=lambda stock_id: catalog.get(stock_id),
        institutional_loader=lambda stock_id, days: _InstResult(
            history=inst_rows,
            latest_date="2024-06-24",
            source="store",
        ),
        margin_loader=lambda stock_id, days: _MarginResult(
            history=(_MarginRow("2024-06-20", 10.0), _MarginRow("2024-06-24", 12.0)),
            summary=_MarginRow("2024-06-24", 12.0),
            latest_date="2024-06-24",
            source="store",
        ),
        ownership_loader=lambda stock_id: _OwnResult(
            points=(
                _OwnPoint("2024-06-07", 40.0),
                _OwnPoint("2024-06-14", 41.5),
            ),
            as_of="2024-06-14",
            source="tdcc",
        ),
        key_stats_loader=lambda stock_id: {
            "peRatio": 18.5,
            "peRatioType": "official_daily",
            "peRatioSource": "TWSE official daily P/E",
            "valuationAsOf": "2024-06-24",
        },
        candles_loader=lambda stock: [],
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert result.fields["institutional_5d"].status == "ok"
    assert result.fields["margin"].status == "ok"
    assert result.fields["margin_utilization"].status == "unavailable"
    assert result.fields["tdcc_holders_ge_400_lots"].status == "ok"
    assert result.fields["tdcc_holders_ge_400_lots"].value["weeklyChangePercentPoints"] == pytest.approx(
        1.5
    )
    assert result.fields["official_pe"].status == "ok"
    assert result.fields["peg"].status == "unavailable"
    assert result.fields["peg"].reason == "verified_eps_growth_denominator_unavailable"
    assert result.fields["ttm_eps"].status == "unavailable"


def test_default_enrichment_uses_stored_only_candles_for_levels(monkeypatch) -> None:
    from app.models.quote import CandleRecord
    from app.services.tw_research import enrichment as enrichment_module

    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="TSMC",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    end = datetime(2024, 6, 28, 13, 30, tzinfo=TZ)
    bars: list[CandleRecord] = []
    cursor = end
    while len(bars) < 60:
        if cursor.weekday() < 5:
            close = 100.0 + len(bars)
            bars.append(
                CandleRecord(
                    time=cursor.isoformat(),
                    open=close - 0.5,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=1000 + len(bars),
                )
            )
        cursor -= timedelta(days=1)
    bars.reverse()

    def fake_peek(stock, range_name, *, limit=None):  # noqa: ANN001
        del stock, range_name, limit
        return list(bars), False

    monkeypatch.setattr(
        enrichment_module.daily_price_history_service,
        "peek_daily_history",
        fake_peek,
    )

    result = enrich_qualified_symbol(
        "TW:2330",
        stock_loader=lambda stock_id: catalog.get(stock_id),
        institutional_loader=lambda stock_id, days: _InstResult(
            history=(), latest_date=None, source="store"
        ),
        margin_loader=lambda stock_id, days: _MarginResult(
            history=(), summary=None, latest_date=None, source="store"
        ),
        ownership_loader=lambda stock_id: _OwnResult(points=(), as_of=None, source="tdcc"),
        key_stats_loader=lambda stock_id: {},
        # candles_loader intentionally omitted → stored-only default
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert result.fields["week52_position"].status == "ok"
    assert result.fields["reference_levels"].status in {"ok", "unavailable"}
    assert result.fields["reference_levels"].reason != "no_completed_candles_supplied"
    assert result.fields["price_basis"].status == "ok"


def test_enrichment_captured_at_is_wall_clock_not_calculation_day(monkeypatch) -> None:
    catalog = {
        "TW:2330": stock_record(
            "TW:2330",
            market="TW",
            symbol="2330",
            name="TSMC",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }
    fixed_now = datetime(2024, 7, 15, 9, 30, tzinfo=TZ)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(
        "app.services.tw_research.enrichment.datetime",
        _FixedDateTime,
    )

    result = enrich_qualified_symbol(
        "TW:2330",
        stock_loader=lambda stock_id: catalog.get(stock_id),
        institutional_loader=lambda stock_id, days: _InstResult(
            history=(), latest_date=None, source="store"
        ),
        margin_loader=lambda stock_id, days: _MarginResult(
            history=(), summary=None, latest_date=None, source="store"
        ),
        ownership_loader=lambda stock_id: _OwnResult(points=(), as_of=None, source="tdcc"),
        key_stats_loader=lambda stock_id: {},
        candles_loader=lambda stock: [],
        # Scan trading-day anchor for candle math — must not become captured_at.
        now=datetime(2024, 6, 28, 14, 0, tzinfo=TZ),
    )
    captured = result.fields["institutional_5d"].captured_at or ""
    assert captured.startswith("2024-07-15")
    assert "2024-06-28" not in captured


def test_prior_week52_from_stage_b_used_when_store_empty() -> None:
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
    result = enrich_qualified_symbol(
        "TW:2727",
        stock_loader=lambda stock_id: catalog.get(stock_id),
        institutional_loader=lambda stock_id, days: _InstResult(
            history=(), latest_date=None, source="store"
        ),
        margin_loader=lambda stock_id, days: _MarginResult(
            history=(), summary=None, latest_date=None, source="store"
        ),
        ownership_loader=lambda stock_id: _OwnResult(points=(), as_of=None, source="tdcc"),
        key_stats_loader=lambda stock_id: {},
        candles_loader=lambda stock: [],
        prior_week52_position=0.72,
        now=datetime(2024, 6, 28, 16, 0, tzinfo=TZ),
    )
    assert result.fields["week52_position"].status == "ok"
    assert result.fields["week52_position"].value == pytest.approx(0.72)
    assert result.fields["week52_position"].source == "scan_stage_b"
    assert result.fields["reference_levels"].reason == "stored_history_empty_or_incomplete"


def test_enrichment_isolates_symbol_errors_and_negative_eps_blocks_peg() -> None:
    catalog = {
        "TW:2618": stock_record(
            "TW:2618",
            market="TW",
            symbol="2618",
            name="長榮航",
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        )
    }

    def boom(stock_id: str, days: int = 0):  # noqa: ANN001
        del stock_id, days
        raise RuntimeError("upstream")

    result = enrich_qualified_symbol(
        "TW:2618",
        stock_loader=lambda stock_id: catalog.get(stock_id),
        institutional_loader=boom,
        margin_loader=boom,
        ownership_loader=lambda stock_id: (_ for _ in ()).throw(RuntimeError("tdcc")),
        key_stats_loader=lambda stock_id: {"epsTtm": -2.5},
        candles_loader=lambda stock: [],
    )
    assert result.fields["institutional_5d"].status == "unavailable"
    assert "symbol_error" in (result.fields["institutional_5d"].reason or "")
    assert result.fields["ttm_eps"].reason == "negative_eps"
    assert result.fields["peg"].reason == "peg_invalid_with_negative_eps"


def test_export_paginates_more_than_eighty_rows_and_supports_unclassified() -> None:
    service = TwResearchScanService(
        history_loader=lambda stock, limit, max_month_fetches=0: ([], 0),
        stock_loader=lambda stock_id: stock_record(
            stock_id,
            market="TW",
            symbol=stock_id.split(":")[1],
            name=stock_id,
            exchange="TWSE",
            currency="TWD",
            timezone="Asia/Taipei",
        ),
    )
    ids = tuple(f"TW:{1000 + index:04d}" for index in range(85))
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=ids),
        target_count=10,
        trading_day="2024-06-28",
    )
    for stock_id in ids:
        record = _qualified_record(stock_id)
        if stock_id == "TW:1084":
            record = SymbolScanRecord(
                stock_id=stock_id,
                stage="B",
                daily={"passStatus": "pass"},
                monthly={"passStatus": "pass"},
                theme_keys=("tw-pcb",),
                enrichment={"stockId": stock_id, "fields": {}},
            )
        state.records[stock_id] = record
        state.qualified_ids.append(stock_id)
    state.cursor = len(ids)

    manifest = build_export_manifest(state, generated_at="2024-06-28T16:00:00+08:00")
    assert manifest["counts"]["technicallyQualified"] == 85
    assert manifest["universe"]["size"] == 85
    assert manifest["themeSummary"]["unclassifiedCount"] == 84

    chunk0 = build_export_chunk(
        state,
        chunk_index=0,
        chunk_size=25,
        kind="ledger",
        generated_at="2024-06-28T16:00:00+08:00",
    )
    chunk3 = build_export_chunk(
        state,
        chunk_index=3,
        chunk_size=25,
        kind="ledger",
        generated_at="2024-06-28T16:00:00+08:00",
    )
    assert chunk0["totalRows"] == 85
    assert chunk0["totalChunks"] == 4
    assert len(chunk0["rows"]) == 25
    assert len(chunk3["rows"]) == 10
    assert "checksumSha256" in chunk0

    csv_body = build_export_csv(state)
    assert csv_body.splitlines()[0].startswith("stockId,")
    assert "TW:1000" in csv_body

    text = build_browser_text_view(state, generated_at="2024-06-28T16:00:00+08:00")
    assert "frontendExportReady" in text
    assert "not_performed_in_stock_ticker" in text
    assert '"chunkIndex": 0' in text
    assert '"totalChunks": 4' in text
    assert "nextChunkIndex" in text
    assert "browserTextPathTemplate" in text

    last = build_browser_text_view(
        state,
        generated_at="2024-06-28T16:00:00+08:00",
        chunk_index=3,
    )
    assert '"chunkIndex": 3' in last
    assert '"nextChunkIndex": null' in last


def test_enrichment_batch_budget_and_restaurant_airline_unclassified(monkeypatch) -> None:
    catalog = {
        "TW:2727": stock_record(
            "TW:2727", market="TW", symbol="2727", name="王品", exchange="TWSE",
            currency="TWD", timezone="Asia/Taipei",
        ),
        "TW:2618": stock_record(
            "TW:2618", market="TW", symbol="2618", name="長榮航", exchange="TWSE",
            currency="TWD", timezone="Asia/Taipei",
        ),
        "TW:2330": stock_record(
            "TW:2330", market="TW", symbol="2330", name="台積電", exchange="TWSE",
            currency="TWD", timezone="Asia/Taipei",
        ),
    }
    service = TwResearchScanService(
        history_loader=lambda stock, limit, max_month_fetches=0: ([], 0),
        stock_loader=lambda stock_id: catalog.get(stock_id),
    )
    state = service.create_scan(
        UniverseSelector(mode="stock_ids", stock_ids=("TW:2727", "TW:2618", "TW:2330")),
        target_count=10,
        trading_day="2024-06-28",
        max_enrichments_per_run=2,
    )
    for stock_id in ("TW:2727", "TW:2618", "TW:2330"):
        state.records[stock_id] = _qualified_record(stock_id)
        state.qualified_ids.append(stock_id)

    def fake_enrich(stock_id: str, **kwargs):  # noqa: ANN003
        del kwargs
        return EnrichmentResult(
            stock_id=stock_id,
            qualified=True,
            fields={
                "peg": ProvenanceField(
                    field="peg",
                    status="unavailable",
                    source="none",
                    reason="verified_eps_growth_denominator_unavailable",
                )
            },
        )

    first = service.run_enrichment_batch(state.scan_id, batch_size=10, enricher=fake_enrich)
    assert first.enrichments_run == 2
    assert first.records["TW:2727"].enrichment is not None
    assert first.records["TW:2618"].enrichment is not None
    assert first.records["TW:2330"].enrichment is None

    second = service.run_enrichment_batch(state.scan_id, batch_size=10, enricher=fake_enrich)
    assert second.enrichments_run == 2
    assert "max_enrichments_per_run" in " ".join(second.notes)
