"""Chip evidence formatting and first-pass chunk verification (#346/#347)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.services.tw_research.chip_evidence import (
    apply_chip_coverage_warnings,
    format_chip_evidence,
    summarize_chip_coverage,
)
from app.services.tw_research.export import content_digest
from app.services.tw_research.first_pass_chunks import (
    build_first_pass_chunk,
    build_first_pass_manifest,
    recompute_chunk_digest,
    verify_first_pass_chunks_consumed,
)
from app.services.tw_research.report_evidence import build_report_evidence


def _is_weekday_session(market, day, allow_holiday_fetch=False) -> bool:  # noqa: ARG001
    probe = day.date() if isinstance(day, datetime) else day
    return probe.weekday() < 5


def _patch_weekday_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.chip_evidence.last_completed_session_date",
        lambda market, now=None, allow_holiday_fetch=True: (
            now.date() if isinstance(now, datetime) else date.fromisoformat(str(now)[:10])
        ),
    )
    monkeypatch.setattr(
        "app.services.market_clock.is_trading_day",
        _is_weekday_session,
    )


def _record(
    stock_id: str,
    *,
    sessions: int = 5,
    margin_ok: bool = True,
    include_chip: bool = True,
    short_balance=None,
    source_as_of: str = "2024-06-27",
    tdcc_prior: str | None = "2024-06-21",
) -> dict:
    points = [
        {
            "date": f"2024-06-{20 + i:02d}",
            "foreignNetLots": 10 + i,
            "trustNetLots": 1,
            "dealerNetLots": -2,
            "totalNetLots": 9 + i,
        }
        for i in range(sessions)
    ]
    fields = {}
    if include_chip:
        fields = {
            "institutional_5d": {
                "status": "ok" if sessions >= 5 else "partial",
                "source": "fixture",
                "source_as_of": source_as_of if points else None,
                "captured_at": "2024-06-28T08:00:00+00:00",
                "value": points,
                "reason": None if sessions >= 5 else "fewer_than_5_completed_sessions_in_store",
            },
            "margin": {
                "status": "ok" if margin_ok else "unavailable",
                "source": "fixture",
                "source_as_of": source_as_of,
                "value": {
                    "balanceLots": 100.0,
                    "balanceDeltaLotsApprox5Sessions": 5.0,
                    "historyDates": [p["date"] for p in points],
                    **(
                        {"shortBalanceLots": short_balance}
                        if short_balance is not None
                        else {}
                    ),
                }
                if margin_ok
                else None,
                "reason": None if margin_ok else "margin_balance_missing",
            },
            "margin_utilization": {
                "status": "unavailable",
                "reason": "numerator_and_denominator_not_both_verified_in_margin_short_payload",
                "source": "fixture",
            },
            "tdcc_holders_ge_400_lots": {
                "status": "partial" if sessions < 2 else "ok",
                "source": "fixture",
                "source_as_of": source_as_of,
                "value": {
                    "percent": 41.0,
                    "weeklyChangePercentPoints": 1.0 if tdcc_prior else None,
                    "priorWeekDate": tdcc_prior,
                    "stale": False,
                    "note": "Weekly TDCC publication date; not a same-day daily change.",
                },
            },
        }
    return {
        "stockId": stock_id,
        "stockName": "台積電" if stock_id.endswith("2330") else stock_id,
        "nameStatus": "ok",
        "fields": fields,
    }


def test_chip_formatter_totals_and_units() -> None:
    summary = format_chip_evidence(
        _record("TW:2330", sessions=5, source_as_of="2024-06-24"),
        reference_day="2024-06-24",
    )
    assert summary["stockName"] == "台積電"
    assert summary["institutional"]["fiveSessionTotalsStatus"] == "ok"
    assert summary["institutional"]["fiveSessionTotals"]["foreignNetLots"] == sum(
        range(10, 15)
    )
    assert summary["institutional"]["unit"] == "lots"
    assert summary["margin"]["direction"] == "increase"
    assert summary["margin"]["utilization"]["status"] == "unavailable"
    assert summary["tdcc"]["changeUnit"] == "percentage_points"


def test_chip_formatter_partial_totals_omit_null_lots() -> None:
    record = _record("TW:2330", sessions=5, source_as_of="2024-06-24")
    points = list(record["fields"]["institutional_5d"]["value"])
    points[2]["foreignNetLots"] = None
    record["fields"]["institutional_5d"]["value"] = points
    summary = format_chip_evidence(record, reference_day="2024-06-24")
    inst = summary["institutional"]
    assert inst["fiveSessionTotalsStatus"] == "partial"
    assert inst["fiveSessionTotalsReason"] == "null_lots_in_session_window"
    totals = inst["fiveSessionTotals"]
    assert totals is not None
    assert totals["foreignNetLots"] == sum(v for v in range(10, 15) if v != 12)


def test_absent_key_is_not_fetched() -> None:
    summary = format_chip_evidence(
        {"stockId": "TW:2330", "fields": {}},
        reference_day="2024-06-27",
    )
    assert summary["institutional"]["status"] == "not_fetched"
    assert summary["institutional"]["reason"] == "field_absent"
    assert summary["margin"]["status"] == "not_fetched"
    assert summary["tdcc"]["status"] == "not_fetched"


def test_old_source_as_of_is_stale() -> None:
    summary = format_chip_evidence(
        _record("TW:2330", source_as_of="2024-06-01"),
        reference_day="2024-06-27",
    )
    assert summary["institutional"]["status"] == "stale"
    assert summary["margin"]["status"] == "stale"


def test_tdcc_prior_week_missing_is_partial_with_null_pp() -> None:
    summary = format_chip_evidence(
        _record("TW:2330", tdcc_prior=None),
        reference_day="2024-06-21",
    )
    assert summary["tdcc"]["status"] == "partial"
    assert summary["tdcc"]["weeklyChangePercentagePoints"] is None


def test_zero_short_balance_is_ok() -> None:
    summary = format_chip_evidence(
        _record("TW:2330", short_balance=0, source_as_of="2024-06-24"),
        reference_day="2024-06-24",
    )
    assert summary["margin"]["shortBalanceStatus"] == "ok"
    assert summary["margin"]["shortBalanceLots"] == 0.0


def test_summarize_chip_coverage_not_fetched_and_stale() -> None:
    coverage = summarize_chip_coverage(
        [
            {"stockId": "TW:1", "fields": {}},
            _record("TW:2", source_as_of="2024-01-01"),
        ]
    )
    assert coverage["fieldCoverage"]["institutional_5d"]["not_fetched"] >= 1
    assert coverage["fieldCoverage"]["institutional_5d"]["missing"] >= 1


def test_first_pass_chunk_digests_and_consumer_verification() -> None:
    records = [_record(f"TW:{2300 + i}") for i in range(40)]
    manifest = build_first_pass_manifest(
        snapshot_id="fps_test",
        content_digest="digest",
        universe_version="uv",
        first_pass_version="first-pass-v1",
        records=records,
        chunk_size=25,
        trading_day="2024-06-27",
    )
    assert manifest["chunkCount"] == 2
    assert manifest["tradingDay"] == "2024-06-27"
    assert set(manifest["chunkDigests"]) == {"0", "1"}

    chunks = [
        build_first_pass_chunk(
            snapshot_id="fps_test",
            content_digest="digest",
            records=records,
            chunk_index=index,
            chunk_size=25,
            trading_day="2024-06-27",
        )
        for index in range(2)
    ]
    assert "checksumEnvelope" in chunks[0]
    verified = verify_first_pass_chunks_consumed(manifest=manifest, chunks=chunks)
    assert verified["ok"] is True
    assert verified["uniqueStockIds"] == 40

    # Tamper one checksum → verification fails.
    bad = dict(chunks[0])
    bad["checksumSha256"] = "deadbeef"
    failed = verify_first_pass_chunks_consumed(manifest=manifest, chunks=[bad, chunks[1]])
    assert failed["ok"] is False
    assert any("digest_mismatch" in err for err in failed["errors"])


def test_digest_mismatch_when_record_altered_but_checksum_kept() -> None:
    records = [_record("TW:2330"), _record("TW:2317")]
    chunk = build_first_pass_chunk(
        snapshot_id="fps",
        content_digest="d",
        records=records,
        chunk_index=0,
        chunk_size=25,
        trading_day="2024-06-27",
    )
    manifest = build_first_pass_manifest(
        snapshot_id="fps",
        content_digest="d",
        universe_version="uv",
        first_pass_version="v1",
        records=records,
        trading_day="2024-06-27",
    )
    tampered = dict(chunk)
    tampered_records = [dict(chunk["records"][0]), dict(chunk["records"][1])]
    fields = dict(tampered_records[0]["fields"])
    margin = dict(fields["margin"])
    value = dict(margin["value"])
    value["balanceLots"] = 999.0
    margin["value"] = value
    fields["margin"] = margin
    tampered_records[0] = {**tampered_records[0], "fields": fields}
    tampered["records"] = tampered_records
    # Keep original checksumSha256 — verifier must recompute from rows.
    failed = verify_first_pass_chunks_consumed(manifest=manifest, chunks=[tampered])
    assert failed["ok"] is False
    assert any("digest_mismatch" in err for err in failed["errors"])


def test_duplicate_chunk_fails_verification() -> None:
    records = [_record("TW:2330")]
    chunk = build_first_pass_chunk(
        snapshot_id="fps",
        content_digest="d",
        records=records,
        chunk_index=0,
        trading_day="2024-06-27",
    )
    manifest = build_first_pass_manifest(
        snapshot_id="fps",
        content_digest="d",
        universe_version="uv",
        first_pass_version="v1",
        records=records,
        trading_day="2024-06-27",
    )
    failed = verify_first_pass_chunks_consumed(manifest=manifest, chunks=[chunk, chunk])
    assert failed["ok"] is False
    assert any("duplicate_chunk_index" in err for err in failed["errors"])


def test_envelope_recompute_matches_manifest() -> None:
    records = [_record(f"TW:{1000 + i}") for i in range(3)]
    manifest = build_first_pass_manifest(
        snapshot_id="fps",
        content_digest="d",
        universe_version="uv",
        first_pass_version="v1",
        records=records,
        trading_day="2024-06-27",
    )
    chunk = build_first_pass_chunk(
        snapshot_id="fps",
        content_digest="d",
        records=records,
        chunk_index=0,
        trading_day="2024-06-27",
    )
    recomputed = recompute_chunk_digest(
        envelope=chunk["checksumEnvelope"],
        rows=chunk["records"],
    )
    assert recomputed == manifest["chunkDigests"]["0"]
    assert recomputed == chunk["checksumSha256"]
    assert content_digest({**chunk["checksumEnvelope"], "rows": chunk["records"]}) == recomputed


def test_large_universe_chunk_verification_keeps_chip_fields() -> None:
    records = [_record(f"TW:{1000 + i}") for i in range(300)]
    manifest = build_first_pass_manifest(
        snapshot_id="fps_300",
        content_digest="digest300",
        universe_version="uv",
        first_pass_version="first-pass-v1",
        records=records,
        chunk_size=25,
        trading_day="2024-06-27",
    )
    assert manifest["chunkCount"] == 12
    chunks = [
        build_first_pass_chunk(
            snapshot_id="fps_300",
            content_digest="digest300",
            records=records,
            chunk_index=index,
            chunk_size=25,
            trading_day="2024-06-27",
        )
        for index in range(manifest["chunkCount"])
    ]
    verified = verify_first_pass_chunks_consumed(manifest=manifest, chunks=chunks)
    assert verified["ok"] is True
    assert verified["uniqueStockIds"] == 300


def test_empty_snapshot_chunk_count_is_zero() -> None:
    manifest = build_first_pass_manifest(
        snapshot_id="fps_empty",
        content_digest="d",
        universe_version="uv",
        first_pass_version="v1",
        records=[],
    )
    assert manifest["chunkCount"] == 0
    assert manifest["chunkDigests"] == {}
    assert manifest["tradingDay"] == "first_pass_snapshot"


def test_report_evidence_production_shaped_dict_without_quality_status() -> None:
    records = [_record("TW:2330"), _record("TW:2317")]
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_prod",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "payload": {"records": records, "tradingDay": "2024-06-27"},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_prod",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "completed_processing",
            "stockIds": ["TW:2330"],
            "calculationVersion": "monthly-first-v1",
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert "first_pass_quality_missing" not in pack["summary"]["qualityWarnings"]
    assert "first_pass_chip_fields_missing" not in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["publishability"] == "evidence_ready_for_analyst_decision"


def test_report_evidence_empty_fields_not_ready() -> None:
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_empty_fields",
            "content_digest": "abc",
            "stock_ids": ["TW:2330"],
            "payload": {"records": [_record("TW:2330", include_chip=False)]},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_empty_fields",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "completed_processing",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert pack["summary"]["publishability"] != "evidence_ready_for_analyst_decision"
    assert "first_pass_chip_fields_missing" in pack["summary"]["qualityWarnings"]


def test_margin_utilization_unavailable_alone_does_not_block() -> None:
    records = [_record("TW:2330")]
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_util",
            "content_digest": "abc",
            "stock_ids": ["TW:2330"],
            "quality_status": "verified_ready",
            "payload": {"records": records},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_util",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert "first_pass_chip_fields_missing" not in pack["summary"]["qualityWarnings"]
    assert "first_pass_chip_fields_partial" not in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["publishability"] == "evidence_ready_for_analyst_decision"


def test_reasoned_margin_unavailable_does_not_block_universe() -> None:
    records = []
    for index in range(300):
        stock_id = f"TW:{1000 + index}"
        record = _record(stock_id)
        if index < 20:
            record["fields"]["margin"] = {
                "status": "unavailable",
                "reason": "not_margin_eligible",
                "source": "fixture",
                "source_as_of": "2024-06-27",
                "value": None,
            }
        records.append(record)
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_gaps",
            "content_digest": "abc",
            "stock_ids": [row["stockId"] for row in records],
            "quality_status": "verified_ready",
            "payload": {"records": records},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:1020"],
            "firstPassSnapshotId": "fps_gaps",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:1020"],
        },
        ledger_rows=[
            {
                "stockId": "TW:1020",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert pack["summary"]["publishability"] == "evidence_ready_for_analyst_decision"
    assert "first_pass_chip_fields_partial" not in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["chipGaps"]["counts"]["margin"]["unavailable"] == 20


def test_candidate_stale_institutional_blocks_full_two_pass(monkeypatch) -> None:
    """Production-shaped raw fields (status ok, no injected stale) still gate."""
    _patch_weekday_calendar(monkeypatch)
    candidate = _record("TW:2330", source_as_of="2026-10-01")
    # Keep explicit status=ok — collector never writes stale.
    candidate["fields"]["institutional_5d"]["status"] = "ok"
    candidate["fields"]["institutional_5d"].pop("stale", None)
    candidate["fields"]["margin"]["status"] = "ok"
    candidate["fields"]["margin"].pop("stale", None)
    other = _record("TW:2317", source_as_of="2026-11-12")
    pack = build_report_evidence(
        trading_day="2026-11-12",
        first_pass_snapshot={
            "snapshot_id": "fps_stale_cand",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "verified_ready",
            "payload": {"records": [candidate, other], "tradingDay": "2026-11-12"},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_stale_cand",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert "candidate_chip_fields_stale_or_missing" in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["publishability"] != "evidence_ready_for_analyst_decision"
    assert pack["symbols"][0]["chipEvidence"]["institutional"]["status"] == "stale"


def test_non_candidate_old_source_as_of_is_chip_gap_only(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    candidate = _record("TW:2330", source_as_of="2026-11-12")
    non_candidate = _record("TW:2317", source_as_of="2026-10-01")
    non_candidate["fields"]["institutional_5d"]["status"] = "ok"
    non_candidate["fields"]["institutional_5d"].pop("stale", None)
    non_candidate["fields"]["margin"]["status"] = "ok"
    non_candidate["fields"]["margin"].pop("stale", None)
    pack = build_report_evidence(
        trading_day="2026-11-12",
        first_pass_snapshot={
            "snapshot_id": "fps_gap_stale",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "verified_ready",
            "payload": {
                "records": [candidate, non_candidate],
                "tradingDay": "2026-11-12",
            },
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_gap_stale",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            },
            {
                "stockId": "TW:2317",
                "monthly": {"passStatus": "fail"},
                "daily": {"passStatus": "not_run_by_design"},
            },
        ],
        mode="full_two_pass",
    ).as_dict()
    assert pack["summary"]["publishability"] == "evidence_ready_for_analyst_decision"
    assert "candidate_chip_fields_stale_or_missing" not in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["chipGaps"]["counts"]["institutional_5d"]["stale"] == 1
    assert any(
        item["stockId"] == "TW:2317" and item["field"] == "institutional_5d"
        for item in pack["summary"]["chipGaps"]["items"]
    )


def test_candidate_tdcc_older_than_10_days_blocks(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    candidate = _record("TW:2330", source_as_of="2026-11-12")
    candidate["fields"]["tdcc_holders_ge_400_lots"]["status"] = "ok"
    candidate["fields"]["tdcc_holders_ge_400_lots"].pop("stale", None)
    candidate["fields"]["tdcc_holders_ge_400_lots"]["source_as_of"] = "2026-10-01"
    pack = build_report_evidence(
        trading_day="2026-11-12",
        first_pass_snapshot={
            "snapshot_id": "fps_tdcc_stale",
            "content_digest": "abc",
            "stock_ids": ["TW:2330"],
            "quality_status": "verified_ready",
            "payload": {"records": [candidate], "tradingDay": "2026-11-12"},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_tdcc_stale",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert "candidate_chip_fields_stale_or_missing" in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["publishability"] != "evidence_ready_for_analyst_decision"
    assert pack["symbols"][0]["chipEvidence"]["tdcc"]["status"] == "stale"


def test_stale_chip_evidence_consistent_with_gaps_or_warnings(monkeypatch) -> None:
    _patch_weekday_calendar(monkeypatch)
    candidate = _record("TW:2330", source_as_of="2026-11-12")
    non_candidate = _record("TW:2317", source_as_of="2026-10-01")
    pack = build_report_evidence(
        trading_day="2026-11-12",
        first_pass_snapshot={
            "snapshot_id": "fps_consistent",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "verified_ready",
            "payload": {
                "records": [candidate, non_candidate],
                "tradingDay": "2026-11-12",
            },
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_consistent",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            },
            {
                "stockId": "TW:2317",
                "monthly": {"passStatus": "fail"},
                "daily": {"passStatus": "not_run_by_design"},
            },
        ],
        mode="full_two_pass",
    ).as_dict()
    warnings = pack["summary"]["qualityWarnings"]
    gap_stock_ids = {
        str(item["stockId"]) for item in (pack["summary"].get("chipGaps") or {}).get("items") or []
    }
    field_map = {
        "institutional": "institutional_5d",
        "margin": "margin",
        "tdcc": "tdcc_holders_ge_400_lots",
    }
    for symbol in pack["symbols"]:
        chip = symbol.get("chipEvidence") or {}
        stock_id = str(symbol.get("stockId") or "")
        for section_key in field_map:
            section = chip.get(section_key) or {}
            if section.get("status") != "stale":
                continue
            assert stock_id in gap_stock_ids or bool(warnings)


def test_unavailable_without_reason_blocks() -> None:
    record = _record("TW:2330")
    record["fields"]["margin"] = {
        "status": "unavailable",
        "reason": None,
        "source": "fixture",
        "value": None,
    }
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_noreason",
            "content_digest": "abc",
            "stock_ids": ["TW:2330"],
            "quality_status": "verified_ready",
            "payload": {"records": [record]},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_noreason",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert "first_pass_chip_fields_missing" in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["publishability"] != "evidence_ready_for_analyst_decision"


def test_absent_key_on_non_candidate_blocks() -> None:
    candidate = _record("TW:2330")
    non_candidate = {"stockId": "TW:2317", "stockName": "x", "nameStatus": "ok", "fields": {}}
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_absent",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "verified_ready",
            "payload": {"records": [candidate, non_candidate]},
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_absent",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
        mode="full_two_pass",
    ).as_dict()
    assert "first_pass_chip_fields_missing" in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["publishability"] != "evidence_ready_for_analyst_decision"


def test_candidate_chip_comes_from_frozen_snapshot_not_enrichment() -> None:
    frozen = _record("TW:2330")
    frozen["fields"]["margin"]["value"]["balanceLots"] = 42.0
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_chip",
            "content_digest": "abc",
            "stock_ids": ["TW:2330"],
            "quality_status": "verified_ready",
            "payload": {"records": [frozen], "tradingDay": "2024-06-24"},
        },
        mode="first_pass_only",
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "enrichment": {
                    "fields": {
                        "margin": {
                            "status": "ok",
                            "value": {"balanceLots": 999.0, "balanceDeltaLotsApprox5Sessions": 1},
                        }
                    },
                    "asOf": "2024-06-28",
                },
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
    ).as_dict()
    row = pack["symbols"][0]
    assert row["chipEvidence"]["margin"]["balanceLots"] == 42.0
    assert row["phase2Enrichment"]["fields"]["margin"]["value"]["balanceLots"] == 999.0
    assert row["enrichment"] is row["phase2Enrichment"]


def test_report_evidence_includes_chip_handoff_contract() -> None:
    records = [_record("TW:2330"), _record("TW:2317", sessions=2)]
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_1",
            "content_digest": "abc",
            "universe_size": 2,
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "complete_attempted",
            "payload": {"records": records},
        },
        mode="first_pass_only",
    ).as_dict()
    assert pack["chipHandoff"]["requiredFields"] == [
        "institutional_5d",
        "margin",
        "margin_utilization",
        "tdcc_holders_ge_400_lots",
    ]
    assert pack["firstPass"]["chunkCount"] >= 1
    assert pack["firstPass"]["chunkDigests"]
    coverage = summarize_chip_coverage(records)
    assert coverage["recordCount"] == 2
    # Reasoned partial chips are non-blocking chipGaps (first_pass_only mode).
    assert "first_pass_chip_fields_partial" not in pack["summary"]["qualityWarnings"]
    assert pack["summary"]["chipGaps"]["counts"]["institutional_5d"].get("partial", 0) >= 1


def test_calendar_known_holiday_does_not_mark_fresh_source_stale(monkeypatch) -> None:
    """Day after a mocked holiday: source_as_of = last real session → not stale."""
    holiday = date(2024, 10, 10)  # Double Ten

    def _is_trading(market, day, allow_holiday_fetch=False):  # noqa: ARG001
        probe = day.date() if isinstance(day, datetime) else day
        if probe == holiday:
            return False
        return probe.weekday() < 5

    def _last_completed(market, now=None, allow_holiday_fetch=True):  # noqa: ARG001
        # Match chip_evidence's midnight combine: session on reference_day not yet closed.
        current = now.date() if isinstance(now, datetime) else date.fromisoformat(str(now)[:10])
        day = date.fromordinal(current.toordinal() - 1)
        for _ in range(12):
            if _is_trading("TW", day):
                return day
            day = date.fromordinal(day.toordinal() - 1)
        return None

    monkeypatch.setattr(
        "app.services.tw_research.chip_evidence.last_completed_session_date",
        _last_completed,
    )
    monkeypatch.setattr("app.services.market_clock.is_trading_day", _is_trading)

    # reference_day Friday 2024-10-11; last session Wed 2024-10-09.
    summary = format_chip_evidence(
        _record("TW:2330", source_as_of="2024-10-09"),
        reference_day="2024-10-11",
        calendar_known=True,
    )
    assert summary["institutional"]["status"] == "ok"
    assert summary["institutional"]["staleCheck"] == "verified"
    assert summary["margin"]["status"] == "ok"
    assert summary["margin"]["staleCheck"] == "verified"
    assert summary["tdcc"]["staleCheck"] == "verified"


def test_calendar_unknown_does_not_claim_stale_and_blocks_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.tw_research.report_evidence.ensure_tw_calendars_bounded",
        lambda years: False,
    )
    candidate = _record("TW:2330", source_as_of="2024-10-09")
    candidate["fields"]["institutional_5d"]["status"] = "ok"
    candidate["fields"]["institutional_5d"].pop("stale", None)
    non_candidate = _record("TW:2317", source_as_of="2024-10-09")
    non_candidate["fields"]["institutional_5d"]["status"] = "ok"
    non_candidate["fields"]["institutional_5d"].pop("stale", None)

    direct = format_chip_evidence(
        candidate, reference_day="2024-10-11", calendar_known=False
    )
    assert direct["institutional"]["status"] == "ok"
    assert direct["institutional"]["staleCheck"] == "calendar_unavailable"
    assert direct["institutional"]["staleBasis"] == "unverified"
    assert direct["margin"]["staleCheck"] == "calendar_unavailable"
    assert direct["tdcc"]["staleCheck"] == "verified"

    pack = build_report_evidence(
        trading_day="2024-10-11",
        first_pass_snapshot={
            "snapshot_id": "fps_cal_unk",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "verified_ready",
            "payload": {
                "records": [candidate, non_candidate],
                "tradingDay": "2024-10-11",
            },
        },
        candidate_set={
            "candidateSetId": "cs",
            "version": 1,
            "stockIds": ["TW:2330"],
            "firstPassSnapshotId": "fps_cal_unk",
            "firstPassDigest": "abc",
        },
        technical_scan={
            "scanId": "scan",
            "status": "verified_ready",
            "stockIds": ["TW:2330"],
        },
        ledger_rows=[
            {
                "stockId": "TW:2330",
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            },
            {
                "stockId": "TW:2317",
                "monthly": {"passStatus": "fail"},
                "daily": {"passStatus": "not_run_by_design"},
            },
        ],
        mode="full_two_pass",
    ).as_dict()
    assert (
        "chip_staleness_unverifiable_calendar_unavailable"
        in pack["summary"]["qualityWarnings"]
    )
    assert pack["summary"]["publishability"] != "evidence_ready_for_analyst_decision"
    assert pack["symbols"][0]["chipEvidence"]["institutional"]["staleCheck"] == (
        "calendar_unavailable"
    )
    gaps = pack["summary"]["chipGaps"]["counts"]["institutional_5d"]
    assert gaps.get("stale_check_unavailable", 0) >= 1
    assert any(
        item["stockId"] == "TW:2317"
        and item["status"] == "stale_check_unavailable"
        for item in pack["summary"]["chipGaps"]["items"]
    )


def test_calendar_unknown_non_candidate_only_is_chip_gap_not_block() -> None:
    """Non-candidate calendar_unavailable → chipGaps; pack readiness not blocked."""
    non_candidate = _record("TW:2317", source_as_of="2024-10-09")
    warnings: list[str] = []
    coverage = apply_chip_coverage_warnings(
        [non_candidate],
        warnings,
        candidate_stock_ids=["TW:2330"],
        full_two_pass=True,
        reference_day="2024-10-11",
        calendar_known=False,
    )
    assert warnings == []
    assert coverage["chipGaps"]["counts"]["institutional_5d"]["stale_check_unavailable"] == 1
    assert coverage["chipGaps"]["items"][0]["status"] == "stale_check_unavailable"


def test_calendar_unknown_then_preload_success_sets_verified() -> None:
    summary = format_chip_evidence(
        _record("TW:2330", source_as_of="2024-10-11"),
        reference_day="2024-10-11",
        calendar_known=True,
    )
    assert summary["institutional"]["staleCheck"] == "verified"
    assert summary["institutional"]["status"] == "ok"


def test_ensure_tw_calendars_bounded_called_once_per_report_build(monkeypatch) -> None:
    calls: list[tuple[int, ...]] = []

    def _bounded(years):
        calls.append(tuple(sorted(int(y) for y in years)))
        return True

    monkeypatch.setattr(
        "app.services.tw_research.report_evidence.ensure_tw_calendars_bounded",
        _bounded,
    )
    records = [_record(f"TW:{1000 + i}", source_as_of="2024-06-27") for i in range(300)]
    build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_once",
            "content_digest": "abc",
            "stock_ids": [r["stockId"] for r in records],
            "quality_status": "verified_ready",
            "payload": {"records": records, "tradingDay": "2024-06-27"},
        },
        mode="first_pass_only",
        ledger_rows=[
            {
                "stockId": records[0]["stockId"],
                "monthly": {"passStatus": "pass"},
                "daily": {"passStatus": "pass"},
            }
        ],
    )
    assert len(calls) == 1
    assert calls[0] == (2023, 2024)


def test_chip_evidence_handler_preloads_calendar_once(monkeypatch) -> None:
    calls: list[tuple[int, ...]] = []

    def _bounded(years):
        calls.append(tuple(sorted(int(y) for y in years)))
        return True

    monkeypatch.setattr(
        "app.services.tw_calendar_preload.ensure_tw_calendars_bounded",
        _bounded,
    )
    records = [_record(f"TW:{1000 + i}", source_as_of="2024-06-27") for i in range(300)]

    def _snap(snapshot_id, user_id=None):  # noqa: ANN001
        del user_id
        return {
            "snapshot_id": snapshot_id,
            "content_digest": "digest",
            "created_at": "2024-06-27T08:00:00+08:00",
            "payload": {
                "tradingDay": "2024-06-27",
                "records": records,
            },
        }

    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.is_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.repositories.tw_research_repository.tw_research_repository.get_first_pass_snapshot",
        _snap,
    )
    from app.api.routes import tw_research as tw_routes

    result = tw_routes.get_first_pass_chip_evidence(
        "fps_once",
        user_id="u1",
        offset=0,
        limit=50,
        q=None,
        stockIds=None,
    )
    assert len(calls) == 1
    assert calls[0] == (2023, 2024)
    assert result["total"] == 300
    assert result["rows"][0]["institutional"]["staleCheck"] == "verified"
