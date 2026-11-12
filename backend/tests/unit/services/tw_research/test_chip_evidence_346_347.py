"""Chip evidence formatting and first-pass chunk verification (#346/#347)."""

from __future__ import annotations

from app.services.tw_research.chip_evidence import format_chip_evidence, summarize_chip_coverage
from app.services.tw_research.export import content_digest
from app.services.tw_research.first_pass_chunks import (
    build_first_pass_chunk,
    build_first_pass_manifest,
    recompute_chunk_digest,
    verify_first_pass_chunks_consumed,
)
from app.services.tw_research.report_evidence import build_report_evidence


def _record(
    stock_id: str,
    *,
    sessions: int = 5,
    margin_ok: bool = True,
    include_chip: bool = True,
    short_balance=None,
    source_as_of: str = "2024-06-24",
    tdcc_prior: str | None = "2024-06-14",
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
                "source_as_of": "2024-06-21",
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
    summary = format_chip_evidence(_record("TW:2330", sessions=5), reference_day="2024-06-24")
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
    record = _record("TW:2330", sessions=5)
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
        _record("TW:2330", short_balance=0),
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
                "source_as_of": "2024-06-24",
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


def test_candidate_stale_institutional_blocks_full_two_pass() -> None:
    candidate = _record("TW:2330")
    candidate["fields"]["institutional_5d"]["status"] = "stale"
    candidate["fields"]["institutional_5d"]["stale"] = True
    candidate["fields"]["institutional_5d"]["reason"] = "source_as_of_before_last_completed_session"
    other = _record("TW:2317")
    pack = build_report_evidence(
        trading_day="2024-06-27",
        first_pass_snapshot={
            "snapshot_id": "fps_stale_cand",
            "content_digest": "abc",
            "stock_ids": ["TW:2330", "TW:2317"],
            "quality_status": "verified_ready",
            "payload": {"records": [candidate, other]},
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
