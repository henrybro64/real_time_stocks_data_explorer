#!/usr/bin/env python3
"""Offline micro-benchmark for chip-evidence / first-pass chunk / report packs (#346).

Stdlib + in-repo app only. Does not touch the DB or network: patches the trading
calendar to weekday mode in-process.

Usage (from backend/ with venv, or inside the backend container)::

    python scripts/bench_chip_evidence.py --n 73 300 --iterations 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Allow `python scripts/bench_chip_evidence.py` from backend/.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_vals) - 1)
    frac = rank - low
    return sorted_vals[low] * (1 - frac) + sorted_vals[high] * frac


def _synth_record(stock_id: str, *, source_as_of: str = "2024-06-27") -> dict[str, Any]:
    points = [
        {
            "date": f"2024-06-{20 + i:02d}",
            "foreignNetLots": 10 + i,
            "trustNetLots": 1,
            "dealerNetLots": -2,
            "totalNetLots": 9 + i,
        }
        for i in range(5)
    ]
    return {
        "stockId": stock_id,
        "stockName": stock_id,
        "nameStatus": "ok",
        "fields": {
            "institutional_5d": {
                "status": "ok",
                "source": "bench",
                "source_as_of": source_as_of,
                "captured_at": "2024-06-28T08:00:00+00:00",
                "value": points,
            },
            "margin": {
                "status": "ok",
                "source": "bench",
                "source_as_of": source_as_of,
                "value": {
                    "balanceLots": 100.0,
                    "balanceDeltaLotsApprox5Sessions": 5.0,
                    "historyDates": [p["date"] for p in points],
                },
            },
            "margin_utilization": {
                "status": "unavailable",
                "reason": "numerator_and_denominator_not_both_verified_in_margin_short_payload",
                "source": "bench",
            },
            "tdcc_holders_ge_400_lots": {
                "status": "ok",
                "source": "bench",
                "source_as_of": source_as_of,
                "value": {
                    "percent": 41.0,
                    "weeklyChangePercentPoints": 1.0,
                    "priorWeekDate": "2024-06-21",
                    "stale": False,
                },
            },
        },
    }


def _patch_weekday_calendar() -> None:
    from app.services import market_clock
    from app.services.tw_research import chip_evidence

    def _is_weekday(market, day, allow_holiday_fetch=False):  # noqa: ARG001
        probe = day.date() if isinstance(day, datetime) else day
        return probe.weekday() < 5

    def _last_completed(market, now=None, allow_holiday_fetch=True):  # noqa: ARG001
        current = now.date() if isinstance(now, datetime) else date.fromisoformat(str(now)[:10])
        day = current
        for _ in range(12):
            if day.weekday() < 5:
                return day
            day = date.fromordinal(day.toordinal() - 1)
        return None

    market_clock.is_trading_day = _is_weekday  # type: ignore[assignment]
    chip_evidence.last_completed_session_date = _last_completed  # type: ignore[assignment]


def _time_ms(fn, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _json_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def _report(label: str, samples: list[float], *, size_bytes: int | None = None) -> None:
    ordered = sorted(samples)
    line = (
        f"  {label}: p50={_percentile(ordered, 50):.2f} ms  "
        f"p95={_percentile(ordered, 95):.2f} ms  "
        f"mean={statistics.fmean(ordered):.2f} ms"
    )
    if size_bytes is not None:
        line += f"  json={size_bytes} B ({size_bytes / 1024:.1f} KiB)"
    print(line)


def run_for_n(n: int, iterations: int) -> None:
    from app.services.tw_research.chip_evidence import format_chip_evidence
    from app.services.tw_research.first_pass_chunks import (
        build_first_pass_chunk,
        build_first_pass_manifest,
        verify_first_pass_chunks_consumed,
    )
    from app.services.tw_research.report_evidence import build_report_evidence

    records = [_synth_record(f"TW:{1000 + i}") for i in range(n)]
    reference_day = "2024-06-27"
    candidate_ids = [records[i]["stockId"] for i in range(min(40, n))]

    print(f"\n=== N={n}  iterations={iterations} ===")

    def _fmt_all():
        for row in records:
            format_chip_evidence(row, reference_day=reference_day, calendar_known=True)

    samples = _time_ms(_fmt_all, iterations)
    _report("format_chip_evidence (all records)", samples)

    def _manifest():
        return build_first_pass_manifest(
            snapshot_id="fps_bench",
            content_digest="digest_bench",
            universe_version="uv",
            first_pass_version="first-pass-v1",
            records=records,
            chunk_size=25,
            trading_day=reference_day,
        )

    manifest = _manifest()
    samples = _time_ms(_manifest, iterations)
    _report("build_first_pass_manifest", samples, size_bytes=_json_size(manifest))

    chunk0 = build_first_pass_chunk(
        snapshot_id="fps_bench",
        content_digest="digest_bench",
        records=records,
        chunk_index=0,
        chunk_size=25,
        trading_day=reference_day,
    )

    def _all_chunks_verify():
        chunks = [
            build_first_pass_chunk(
                snapshot_id="fps_bench",
                content_digest="digest_bench",
                records=records,
                chunk_index=index,
                chunk_size=25,
                trading_day=reference_day,
            )
            for index in range(int(manifest["chunkCount"]))
        ]
        verify_first_pass_chunks_consumed(manifest=manifest, chunks=chunks)

    samples = _time_ms(_all_chunks_verify, iterations)
    _report(
        "all build_first_pass_chunk + verify",
        samples,
        size_bytes=_json_size(chunk0),
    )

    def _report_first_pass_only():
        return build_report_evidence(
            trading_day=reference_day,
            first_pass_snapshot={
                "snapshot_id": "fps_bench",
                "content_digest": "digest_bench",
                "stock_ids": [r["stockId"] for r in records],
                "quality_status": "verified_ready",
                "payload": {"records": records, "tradingDay": reference_day},
            },
            mode="first_pass_only",
        ).as_dict()

    pack_fp = _report_first_pass_only()
    samples = _time_ms(_report_first_pass_only, iterations)
    _report("build_report_evidence first_pass_only", samples, size_bytes=_json_size(pack_fp))

    def _report_full():
        return build_report_evidence(
            trading_day=reference_day,
            first_pass_snapshot={
                "snapshot_id": "fps_bench",
                "content_digest": "digest_bench",
                "stock_ids": [r["stockId"] for r in records],
                "quality_status": "verified_ready",
                "payload": {"records": records, "tradingDay": reference_day},
            },
            candidate_set={
                "candidateSetId": "cs_bench",
                "version": 1,
                "stockIds": candidate_ids,
                "firstPassSnapshotId": "fps_bench",
                "firstPassDigest": "digest_bench",
            },
            technical_scan={
                "scanId": "scan_bench",
                "status": "verified_ready",
                "stockIds": candidate_ids,
                "calculationVersion": "monthly-first-v1",
            },
            ledger_rows=[
                {
                    "stockId": stock_id,
                    "monthly": {"passStatus": "pass"},
                    "daily": {"passStatus": "pass"},
                }
                for stock_id in candidate_ids
            ],
            mode="full_two_pass",
        ).as_dict()

    pack_full = _report_full()
    samples = _time_ms(_report_full, iterations)
    _report(
        "build_report_evidence full_two_pass (40 candidates)",
        samples,
        size_bytes=_json_size(pack_full),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--n",
        nargs="+",
        type=int,
        default=[73, 300, 1000],
        help="Record counts to benchmark (default: 73 300 1000)",
    )
    parser.add_argument(
        "--iterations",
        "-k",
        type=int,
        default=20,
        help="Timed iterations per workload (default: 20)",
    )
    args = parser.parse_args(argv)

    _patch_weekday_calendar()
    # Avoid network calendar preload during report builds.
    from app.services.tw_research import report_evidence as report_evidence_mod

    report_evidence_mod.ensure_tw_calendars_bounded = lambda years: True  # type: ignore[assignment]

    print("bench_chip_evidence.py — offline micro-benchmark (no DB / no network)")
    print(f"iterations={args.iterations}  n={args.n}")

    tracemalloc.start()
    peak_before = tracemalloc.get_traced_memory()[1]
    for n in args.n:
        run_for_n(n, args.iterations)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"\ntracemalloc peak={peak / 1024:.1f} KiB  "
        f"(delta from start={(peak - peak_before) / 1024:.1f} KiB; current={current / 1024:.1f} KiB)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
