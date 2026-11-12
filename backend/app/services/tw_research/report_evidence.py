"""Analyst-ready evidence pack for external ChatGPT (#340).

Stock Ticker exports provenance-backed evidence only. It does **not**:
- rank final Notion picks
- author entry/target/stop commentary
- write Notion
- pad to 10/20

ChatGPT makes two decisions: (1) compute-subset selection, (2) final report
content. An external Agent publishes only after explicit approval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.tw_research.chip_evidence import CHIP_FIELD_KEYS, summarize_chip_coverage
from app.services.tw_research.constants import (
    DAILY_NOT_RUN_BY_DESIGN,
    EXPORT_CHUNK_SIZE,
    REPORT_EVIDENCE_VERSION,
)
from app.services.tw_research.evaluation import derive_stage_progress
from app.services.tw_research.export import content_digest
from app.services.tw_research.first_pass_chunks import build_first_pass_manifest

_FIXTURE_PATH = Path(__file__).with_name("report_evidence_fixture_zh_tw.json")

_PARTIAL_STATUSES = frozenset(
    {
        "partial",
        "deferred",
        "budget_exhausted",
        "waiting_for_next_window",
    }
)


@dataclass(frozen=True)
class ReportEvidencePack:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def normalize_first_pass_snapshot(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept snake_case repository rows or camelCase API shapes."""
    if not raw:
        return None
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    snapshot_id = (
        raw.get("snapshotId")
        or raw.get("snapshot_id")
        or raw.get("firstPassSnapshotId")
        or raw.get("first_pass_snapshot_id")
    )
    digest = (
        raw.get("contentDigest")
        or raw.get("content_digest")
        or raw.get("firstPassDigest")
        or raw.get("first_pass_digest")
    )
    stock_ids = list(
        raw.get("stockIds")
        or raw.get("stock_ids")
        or payload.get("stockIds")
        or payload.get("stock_ids")
        or []
    )
    quality = (
        raw.get("qualityStatus")
        or raw.get("quality_status")
        or payload.get("qualityStatus")
        or payload.get("quality_status")
    )
    trading_day = (
        raw.get("tradingDay")
        or raw.get("trading_day")
        or payload.get("tradingDay")
        or payload.get("trading_day")
    )
    return {
        "snapshotId": snapshot_id,
        "contentDigest": digest,
        "stockIds": stock_ids,
        "universeSize": int(
            raw.get("universeSize")
            or raw.get("universe_size")
            or len(stock_ids)
            or 0
        ),
        "qualityStatus": quality,
        "tradingDay": trading_day,
        "payload": dict(payload) if payload else {},
        "raw": dict(raw),
    }


def build_report_evidence(
    *,
    trading_day: str | None = None,
    first_pass_snapshot: dict[str, Any] | None = None,
    candidate_set: dict[str, Any] | None = None,
    technical_scan: dict[str, Any] | None = None,
    ledger_rows: list[dict[str, Any]] | None = None,
    mode: str = "auto",
) -> ReportEvidencePack:
    """Compose a compact analyst-ready evidence document from durable inputs."""
    now = datetime.now(UTC).isoformat()
    first_pass = normalize_first_pass_snapshot(first_pass_snapshot)
    rows = list(ledger_rows or [])
    resolved_mode = mode
    if resolved_mode == "auto":
        resolved_mode = "full_two_pass" if technical_scan is not None else "first_pass_only"
    stage = {}
    if technical_scan is not None:
        stage = derive_stage_progress(
            stock_ids=list(technical_scan.get("stockIds") or []),
            records=_records_from_rows(rows),
            calculation_version=str(
                technical_scan.get("calculationVersion") or "unknown"
            ),
        )

    quality_warnings: list[str] = []
    first_pass_quality = (first_pass or {}).get("qualityStatus")
    if first_pass is not None and first_pass_quality in {None, ""}:
        quality_warnings.append("first_pass_quality_missing")
    elif first_pass is not None and first_pass_quality in _PARTIAL_STATUSES:
        quality_warnings.append("first_pass_incomplete_or_partial")
    elif first_pass is not None and first_pass_quality not in {
        "verified_ready",
        "complete",
        "completed",
        # complete_attempted = durable collection finished with known gaps;
        # still not verified_ready — consumers must read qualityWarnings/digests.
        "complete_attempted",
        "ok",
    }:
        quality_warnings.append("first_pass_quality_not_verified_ready")

    technical_status = (technical_scan or {}).get("status")
    if technical_status == "budget_exhausted":
        quality_warnings.append("technical_hard_cap_budget_exhausted")
    if technical_status in _PARTIAL_STATUSES:
        quality_warnings.append("technical_waiting_or_partial")
    if resolved_mode == "full_two_pass" and not candidate_set:
        quality_warnings.append("candidate_set_missing")
    if resolved_mode == "full_two_pass" and not first_pass:
        quality_warnings.append("first_pass_missing")

    publishability = "draft_only"
    if (
        resolved_mode == "full_two_pass"
        and first_pass
        and candidate_set
        and technical_scan
        and technical_status in {"verified_ready", "completed_processing"}
        and not quality_warnings
    ):
        publishability = "evidence_ready_for_analyst_decision"
    elif quality_warnings:
        publishability = "incomplete_or_partial_evidence"

    symbols = [_symbol_evidence_row(row) for row in rows]
    insufficient = [
        row
        for row in symbols
        if row.get("bucket") in {"insufficient_evidence", "source_incomplete"}
    ]
    watchlist = [
        row for row in symbols if row.get("bucket") == "fundamental_watch_monthly_fail"
    ]
    trading_candidates = [
        row for row in symbols if row.get("bucket") == "technical_confirmed"
    ]

    first_pass_ref = None
    chip_handoff = None
    if first_pass and first_pass.get("snapshotId"):
        snapshot_id = str(first_pass["snapshotId"])
        payload_records = []
        raw_payload = first_pass.get("payload") if isinstance(first_pass.get("payload"), dict) else {}
        if isinstance(raw_payload.get("records"), list):
            payload_records = list(raw_payload["records"])
        elif isinstance((first_pass.get("raw") or {}).get("payload"), dict):
            nested = (first_pass.get("raw") or {}).get("payload") or {}
            if isinstance(nested.get("records"), list):
                payload_records = list(nested["records"])
        manifest = build_first_pass_manifest(
            snapshot_id=snapshot_id,
            content_digest=str(first_pass.get("contentDigest") or ""),
            universe_version=str(
                (first_pass.get("raw") or {}).get("universe_version")
                or raw_payload.get("universeVersion")
                or ""
            ),
            first_pass_version=str(
                (first_pass.get("raw") or {}).get("first_pass_version")
                or raw_payload.get("firstPassVersion")
                or ""
            ),
            records=payload_records,
            chunk_size=EXPORT_CHUNK_SIZE,
        )
        first_pass_ref = {
            "snapshotId": snapshot_id,
            "contentDigest": first_pass.get("contentDigest"),
            "stockCount": int(
                first_pass.get("universeSize") or len(first_pass.get("stockIds") or [])
            ),
            "manifestPath": f"/api/tw-research/first-pass/snapshots/{snapshot_id}/manifest",
            "chunkPathTemplate": manifest["chunkPathTemplate"],
            "chunkCount": manifest["chunkCount"],
            "chunkDigests": manifest["chunkDigests"],
            "chipCoverage": manifest["chipCoverage"],
            "requiredChipFields": list(CHIP_FIELD_KEYS),
        }
        chip_handoff = {
            "requiredFields": list(CHIP_FIELD_KEYS),
            "units": {
                "institutional_5d": "lots",
                "margin": "lots",
                "tdcc_holders_ge_400_lots": "percent_and_percentage_points",
            },
            "verification": {
                "steps": [
                    f"GET {first_pass_ref['manifestPath']}",
                    "GET every chunkIndex in chunkDigests via chunkPathTemplate",
                    "Match each chunk.checksumSha256 to manifest.chunkDigests[i]",
                    "Confirm requiredChipFields exist on every record (status may be unavailable)",
                ],
                "statusAfterVerify": "stock_ticker_first_pass_export_verified",
            },
            "decisionGuidance": {
                "decision1_candidateSelection": (
                    "May use chip + fundamental evidence when choosing technical compute subset."
                ),
                "decision2_finalReport": (
                    "May use chip + fundamental + valuation + price basis + "
                    "monthly-first/daily MACD/KD; zero selections is valid."
                ),
            },
            "coverage": summarize_chip_coverage(payload_records)
            if payload_records
            else first_pass_ref["chipCoverage"],
            "notBuySignal": True,
        }

    payload = {
        "reportEvidenceVersion": REPORT_EVIDENCE_VERSION,
        "generatedAt": now,
        "mode": resolved_mode,
        "tradingDay": trading_day
        or (technical_scan or {}).get("tradingDay")
        or (first_pass or {}).get("tradingDay"),
        "ownership": {
            "stockTickerRole": "data_producer_evidence_only",
            "chatgptRole": "two_decision_analyst",
            "notionWriterRole": "external_agent_after_explicit_approval",
            "stockTickerWritesNotion": False,
            "stockTickerRanksFinalPicks": False,
        },
        "summary": {
            "universeSize": int((first_pass or {}).get("universeSize") or 0),
            "candidateCount": len((candidate_set or {}).get("stockIds") or []),
            "tradingCandidateCount": len(trading_candidates),
            "watchlistCount": len(watchlist),
            "insufficientEvidenceCount": len(insufficient),
            "qualityWarnings": quality_warnings,
            "publishability": publishability,
            "zeroQualifyingIsValid": True,
            "noTopNPadding": True,
        },
        "pins": {
            "firstPassSnapshotId": (first_pass or {}).get("snapshotId"),
            "firstPassDigest": (first_pass or {}).get("contentDigest")
            or (candidate_set or {}).get("firstPassDigest"),
            "candidateSetId": (candidate_set or {}).get("candidateSetId"),
            "candidateSetVersion": (candidate_set or {}).get("version"),
            "technicalScanId": (technical_scan or {}).get("scanId"),
            "technicalContentDigest": (technical_scan or {}).get("contentDigest"),
        },
        "firstPass": first_pass_ref,
        "chipHandoff": chip_handoff,
        "stageProgress": stage,
        "symbols": symbols,
        "analystDecisionSlots": {
            "entryZone": "not_established",
            "target": "not_established",
            "stop": "not_established",
            "riskReward": "not_established",
            "whyNow": "not_established",
            "whyWait": "not_established",
            "note": (
                "Only ChatGPT may author these fields when defensible; "
                "Stock Ticker leaves them not_established."
            ),
        },
        "notes": [
            "Example Traditional Chinese narrative fixtures are labeled examples, "
            "not live recommendations.",
            "Partial/stale/unknown required fields must not be represented as a "
            "fully verified daily Notion report.",
            "Unchanged contentDigest ⇒ external Agent must not rewrite Notion.",
            "Retrieve and verify all first-pass chunks (chip fields) before Decision 1/2 (#347).",
        ],
    }
    digest_payload = {key: value for key, value in payload.items() if key != "generatedAt"}
    payload["contentDigest"] = content_digest(digest_payload)
    return ReportEvidencePack(payload=payload)


def load_zh_tw_example_fixture() -> dict[str, Any]:
    """Deterministic Traditional Chinese example — clearly marked as fixture."""
    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert raw.get("exampleOnly") is True
    return raw


def _records_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    class _Rec:
        def __init__(self, row: dict[str, Any]) -> None:
            self.daily = row.get("daily")
            self.monthly = row.get("monthly")
            self.coverage = row.get("coverage")
            self.readiness = row.get("readiness")
            self.rejection_reason = row.get("rejectionReason")

    return {str(row.get("stockId")): _Rec(row) for row in rows if row.get("stockId")}


def _symbol_evidence_row(row: dict[str, Any]) -> dict[str, Any]:
    monthly = row.get("monthly") if isinstance(row.get("monthly"), dict) else {}
    daily = row.get("daily") if isinstance(row.get("daily"), dict) else {}
    monthly_pass = monthly.get("passStatus")
    daily_pass = daily.get("passStatus")
    daily_exec = daily.get("executionStatus")
    bucket = "insufficient_evidence"
    if row.get("readiness") == "source_incomplete":
        bucket = "source_incomplete"
    elif monthly_pass == "pass" and daily_pass == "pass":
        bucket = "technical_confirmed"
    elif monthly_pass == "pass" and (
        daily_pass == "fail" or daily_exec == DAILY_NOT_RUN_BY_DESIGN
    ):
        bucket = "waiting_daily_turn"
    elif monthly_pass == "fail" or daily_exec == DAILY_NOT_RUN_BY_DESIGN:
        bucket = "fundamental_watch_monthly_fail"

    return {
        "stockId": row.get("stockId"),
        "stockName": row.get("stockName"),
        "nameStatus": row.get("nameStatus"),
        "technicalStage": row.get("technicalStage") or row.get("technical_stage"),
        "bucket": bucket,
        "monthlyPassStatus": monthly_pass,
        "dailyPassStatus": daily_pass,
        "dailyExecutionStatus": daily_exec,
        "officialPe": _field_value(row, "official_pe"),
        "relativePriceBasis": row.get("relativePriceBasis")
        or _field_value(row, "relative_price_basis"),
        "week52Position": row.get("week52Position"),
        "enrichment": row.get("enrichment"),
        "provenanceNote": "Values carry source/as-of when present; never fabricate.",
        "analystNarrative": "not_established",
    }


def _field_value(row: dict[str, Any], field_name: str) -> Any:
    fields = row.get("fields")
    if isinstance(fields, dict) and field_name in fields:
        payload = fields[field_name]
        if isinstance(payload, dict):
            return payload.get("value")
    return None
