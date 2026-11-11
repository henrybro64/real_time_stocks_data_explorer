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

from app.services.tw_research.constants import (
    DAILY_NOT_RUN_BY_DESIGN,
    REPORT_EVIDENCE_VERSION,
)
from app.services.tw_research.evaluation import derive_stage_progress

_FIXTURE_PATH = Path(__file__).with_name("report_evidence_fixture_zh_tw.json")


@dataclass(frozen=True)
class ReportEvidencePack:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def build_report_evidence(
    *,
    trading_day: str | None = None,
    first_pass_snapshot: dict[str, Any] | None = None,
    candidate_set: dict[str, Any] | None = None,
    technical_scan: dict[str, Any] | None = None,
    ledger_rows: list[dict[str, Any]] | None = None,
) -> ReportEvidencePack:
    """Compose a compact analyst-ready evidence document from durable inputs."""
    now = datetime.now(UTC).isoformat()
    rows = list(ledger_rows or [])
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
    if first_pass_snapshot and first_pass_snapshot.get("qualityStatus") not in {
        None,
        "verified_ready",
        "complete",
        "partial",
    }:
        quality_warnings.append("first_pass_quality_not_verified_ready")
    if technical_scan and technical_scan.get("status") == "budget_exhausted":
        quality_warnings.append("technical_hard_cap_budget_exhausted")
    if technical_scan and technical_scan.get("status") in {
        "partial",
        "deferred",
        "waiting_for_next_window",
    }:
        quality_warnings.append("technical_waiting_or_partial")

    publishability = "draft_only"
    if (
        first_pass_snapshot
        and candidate_set
        and technical_scan
        and technical_scan.get("status") in {"verified_ready", "completed_processing"}
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

    payload = {
        "reportEvidenceVersion": REPORT_EVIDENCE_VERSION,
        "generatedAt": now,
        "tradingDay": trading_day
        or (technical_scan or {}).get("tradingDay")
        or (first_pass_snapshot or {}).get("tradingDay"),
        "ownership": {
            "stockTickerRole": "data_producer_evidence_only",
            "chatgptRole": "two_decision_analyst",
            "notionWriterRole": "external_agent_after_explicit_approval",
            "stockTickerWritesNotion": False,
            "stockTickerRanksFinalPicks": False,
        },
        "summary": {
            "universeSize": int(
                (first_pass_snapshot or {}).get("universeSize")
                or len((first_pass_snapshot or {}).get("stockIds") or [])
                or 0
            ),
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
            "firstPassSnapshotId": (first_pass_snapshot or {}).get("snapshotId")
            or (first_pass_snapshot or {}).get("firstPassSnapshotId"),
            "firstPassDigest": (first_pass_snapshot or {}).get("contentDigest")
            or (candidate_set or {}).get("firstPassDigest"),
            "candidateSetId": (candidate_set or {}).get("candidateSetId"),
            "candidateSetVersion": (candidate_set or {}).get("version"),
            "technicalScanId": (technical_scan or {}).get("scanId"),
            "technicalContentDigest": (technical_scan or {}).get("contentDigest"),
        },
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
        ],
    }
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
