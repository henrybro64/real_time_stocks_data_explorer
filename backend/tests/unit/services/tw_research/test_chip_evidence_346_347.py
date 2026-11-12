"""Chip evidence formatting and first-pass chunk verification (#346/#347)."""

from __future__ import annotations

from app.services.tw_research.chip_evidence import format_chip_evidence, summarize_chip_coverage
from app.services.tw_research.first_pass_chunks import (
    build_first_pass_chunk,
    build_first_pass_manifest,
    verify_first_pass_chunks_consumed,
)
from app.services.tw_research.report_evidence import build_report_evidence


def _record(stock_id: str, *, sessions: int = 5, margin_ok: bool = True) -> dict:
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
    return {
        "stockId": stock_id,
        "stockName": "台積電" if stock_id.endswith("2330") else stock_id,
        "nameStatus": "ok",
        "fields": {
            "institutional_5d": {
                "status": "ok" if sessions >= 5 else "partial",
                "source": "fixture",
                "source_as_of": points[-1]["date"] if points else None,
                "captured_at": "2024-06-28T08:00:00+00:00",
                "value": points,
                "reason": None if sessions >= 5 else "fewer_than_5_completed_sessions_in_store",
            },
            "margin": {
                "status": "ok" if margin_ok else "unavailable",
                "source": "fixture",
                "source_as_of": "2024-06-24",
                "value": {
                    "balanceLots": 100.0,
                    "balanceDeltaLotsApprox5Sessions": 5.0,
                    "historyDates": [p["date"] for p in points],
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
                    "weeklyChangePercentPoints": 1.0 if sessions >= 2 else None,
                    "priorWeekDate": "2024-06-14" if sessions >= 2 else None,
                    "stale": False,
                    "note": "Weekly TDCC publication date; not a same-day daily change.",
                },
            },
        },
    }


def test_chip_formatter_totals_and_units() -> None:
    summary = format_chip_evidence(_record("TW:2330", sessions=5))
    assert summary["stockName"] == "台積電"
    assert summary["institutional"]["fiveSessionTotalsStatus"] == "ok"
    assert summary["institutional"]["fiveSessionTotals"]["foreignNetLots"] == sum(
        range(10, 15)
    )
    assert summary["institutional"]["unit"] == "lots"
    assert summary["margin"]["direction"] == "increase"
    assert summary["margin"]["utilization"]["status"] == "unavailable"
    assert summary["tdcc"]["changeUnit"] == "percentage_points"
    assert "percentage points" in (summary["tdcc"]["note"] or "").lower() or (
        "percentage points" in (summary["tdcc"].get("limitation") or "").lower()
        or summary["tdcc"]["weeklyChangePercentagePoints"] == 1.0
    )

    partial = format_chip_evidence(_record("TW:2317", sessions=3))
    assert partial["institutional"]["fiveSessionTotalsStatus"] == "partial"
    assert "only_3_sessions" in (partial["institutional"]["fiveSessionTotalsReason"] or "")
    assert partial["institutional"]["fiveSessionTotals"] is None


def test_chip_formatter_partial_totals_omit_null_lots() -> None:
    """5-session window with a null lot → partial totals via sum-present (#346 P0)."""
    record = _record("TW:2330", sessions=5)
    points = list(record["fields"]["institutional_5d"]["value"])
    points[2]["foreignNetLots"] = None
    record["fields"]["institutional_5d"]["value"] = points
    summary = format_chip_evidence(record)
    inst = summary["institutional"]
    assert inst["fiveSessionTotalsStatus"] == "partial"
    assert inst["fiveSessionTotalsReason"] == "null_lots_in_session_window"
    totals = inst["fiveSessionTotals"]
    assert totals is not None
    # Omit the null session (index 2 would have been 12); do not invent zero.
    assert totals["foreignNetLots"] == sum(v for v in range(10, 15) if v != 12)
    assert "omit null" in (totals.get("note") or "").lower()


def test_first_pass_chunk_digests_and_consumer_verification() -> None:
    records = [_record(f"TW:{2300 + i}") for i in range(40)]
    manifest = build_first_pass_manifest(
        snapshot_id="fps_test",
        content_digest="digest",
        universe_version="uv",
        first_pass_version="first-pass-v1",
        records=records,
        chunk_size=25,
    )
    assert manifest["chunkCount"] == 2
    assert set(manifest["chunkDigests"]) == {"0", "1"}
    assert "institutional_5d" in manifest["requiredChipFields"]

    chunks = [
        build_first_pass_chunk(
            snapshot_id="fps_test",
            content_digest="digest",
            records=records,
            chunk_index=index,
            chunk_size=25,
        )
        for index in range(2)
    ]
    verified = verify_first_pass_chunks_consumed(manifest=manifest, chunks=chunks)
    assert verified["ok"] is True
    assert verified["uniqueStockIds"] == 40
    assert verified["status"] == "stock_ticker_first_pass_export_verified"

    # Tamper one checksum → verification fails.
    bad = dict(chunks[0])
    bad["checksumSha256"] = "deadbeef"
    failed = verify_first_pass_chunks_consumed(manifest=manifest, chunks=[bad, chunks[1]])
    assert failed["ok"] is False
    assert any("digest_mismatch" in err for err in failed["errors"])


def test_large_universe_chunk_verification_keeps_chip_fields() -> None:
    """#347 acceptance: ~300 frozen records, every chunk + required chip keys."""
    records = [_record(f"TW:{1000 + i}") for i in range(300)]
    manifest = build_first_pass_manifest(
        snapshot_id="fps_300",
        content_digest="digest300",
        universe_version="uv",
        first_pass_version="first-pass-v1",
        records=records,
        chunk_size=25,
    )
    assert manifest["chunkCount"] == 12
    chunks = [
        build_first_pass_chunk(
            snapshot_id="fps_300",
            content_digest="digest300",
            records=records,
            chunk_index=index,
            chunk_size=25,
        )
        for index in range(manifest["chunkCount"])
    ]
    verified = verify_first_pass_chunks_consumed(manifest=manifest, chunks=chunks)
    assert verified["ok"] is True
    assert verified["uniqueStockIds"] == 300
    for chunk in chunks:
        for record in chunk["records"]:
            for key in (
                "institutional_5d",
                "margin",
                "margin_utilization",
                "tdcc_holders_ge_400_lots",
            ):
                assert key in record["fields"]


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
    assert "Retrieve and verify all first-pass chunks" in " ".join(pack["notes"])
    coverage = summarize_chip_coverage(records)
    assert coverage["recordCount"] == 2
