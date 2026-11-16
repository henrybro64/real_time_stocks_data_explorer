"""Read-only technical results projection for Stage-4 (#361).

Maps persisted scan symbol records into a stable API shape. Never recomputes
indicators or calls providers.
"""

from __future__ import annotations

from typing import Any

from app.services.tw_research.constants import (
    DAILY_NOT_RUN_BY_DESIGN,
    HISTORY_BUDGET_EXHAUSTED_REASON,
    MIN_DAILY_BARS_FOR_MACD_KD,
    MIN_MONTHLY_BARS_FOR_MACD_KD,
    PRICE_BASIS,
    TECHNICAL_STAGE_DAILY_FAIL,
    TECHNICAL_STAGE_DAILY_NOT_RUN,
    TECHNICAL_STAGE_DAILY_PASS,
    TECHNICAL_STAGE_DAILY_UNKNOWN,
    TECHNICAL_STAGE_DEFERRED,
    TECHNICAL_STAGE_MONTHLY_FAIL,
    TECHNICAL_STAGE_MONTHLY_PASS,
    TECHNICAL_STAGE_MONTHLY_UNKNOWN,
    TECHNICAL_STAGE_PENDING,
)
from app.services.tw_research.scan import ScanState, SymbolScanRecord


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_field(record: SymbolScanRecord | dict[str, Any], *names: str) -> Any:
    if isinstance(record, dict):
        for name in names:
            if name in record:
                return record.get(name)
        return None
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return None


def _indicator_payload(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "asOf": raw.get("asOf"),
        "dif": _as_float(raw.get("dif")),
        "signal": _as_float(raw.get("signal")),
        "osc": _as_float(raw.get("osc")),
        "k": _as_float(raw.get("k")),
        "d": _as_float(raw.get("d")),
        "barCount": _as_int(raw.get("barCount")),
        "quality": raw.get("quality"),
        "passStatus": raw.get("passStatus"),
        "executionStatus": raw.get("executionStatus"),
        "priceBasis": raw.get("priceBasis"),
        "calculationVersion": raw.get("calculationVersion"),
        "timeframe": raw.get("timeframe"),
        "reason": raw.get("reason"),
    }


def _insufficient_history(
    *,
    timeframe: str,
    payload: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    rejection_reason: str | None,
) -> dict[str, Any] | None:
    required = (
        MIN_MONTHLY_BARS_FOR_MACD_KD
        if timeframe == "monthly"
        else MIN_DAILY_BARS_FOR_MACD_KD
    )
    available: int | None = None
    reason: str | None = None

    if isinstance(payload, dict):
        if str(payload.get("quality") or "") == "insufficient_history":
            reason = "insufficient_history"
            available = _as_int(payload.get("barCount"))
        elif str(payload.get("executionStatus") or "") == "insufficient_history":
            reason = "insufficient_history"
            available = _as_int(payload.get("barCount"))

    if isinstance(coverage, dict):
        frame = coverage.get(timeframe)
        if isinstance(frame, dict):
            cov_reason = str(frame.get("reason") or "")
            if cov_reason in {"insufficient_history", "insufficient_monthly_history"}:
                reason = reason or cov_reason
                if available is None:
                    available = _as_int(frame.get("barCount"))

    if rejection_reason == HISTORY_BUDGET_EXHAUSTED_REASON and timeframe == "monthly":
        reason = reason or HISTORY_BUDGET_EXHAUSTED_REASON

    if reason is None:
        return None
    return {
        "reason": reason,
        "requiredBars": required,
        "availableBars": available if available is not None else 0,
        "timeframe": timeframe,
    }


def _daily_not_run_reason(daily: dict[str, Any] | None) -> str | None:
    if not isinstance(daily, dict):
        return None
    execution = str(daily.get("executionStatus") or "")
    if execution == DAILY_NOT_RUN_BY_DESIGN:
        return str(daily.get("reason") or "monthly_gate_failed")
    return None


def _overall_stage_result(
    *,
    technical_stage: str | None,
    monthly: dict[str, Any] | None,
    daily: dict[str, Any] | None,
    rejection_reason: str | None,
    insufficient: dict[str, Any] | None,
) -> str:
    if insufficient is not None:
        return "insufficient_history"
    if rejection_reason == HISTORY_BUDGET_EXHAUSTED_REASON:
        return "insufficient_history"
    stage = str(technical_stage or "") or None
    if stage == TECHNICAL_STAGE_DAILY_PASS:
        return "pass"
    if stage == TECHNICAL_STAGE_DAILY_FAIL:
        return "fail"
    if stage == TECHNICAL_STAGE_DAILY_NOT_RUN:
        return "daily_not_run"
    if stage == TECHNICAL_STAGE_DAILY_UNKNOWN:
        return "daily_unknown"
    if stage == TECHNICAL_STAGE_MONTHLY_PASS:
        return "monthly_pass"
    if stage == TECHNICAL_STAGE_MONTHLY_FAIL:
        return "monthly_fail"
    if stage == TECHNICAL_STAGE_MONTHLY_UNKNOWN:
        return "monthly_unknown"
    if stage == TECHNICAL_STAGE_DEFERRED:
        return "deferred"
    if stage in {TECHNICAL_STAGE_PENDING, None, ""}:
        # Monthly quality may already mark insufficient without a stage.
        if isinstance(monthly, dict) and str(monthly.get("quality") or "") == (
            "insufficient_history"
        ):
            return "insufficient_history"
        if _daily_not_run_reason(daily):
            return "daily_not_run"
        return "pending"
    return stage


def map_symbol_technical_result(
    record: SymbolScanRecord | dict[str, Any],
) -> dict[str, Any]:
    """Map one persisted symbol record to the Stage-4 results row shape."""
    stock_id = str(_record_field(record, "stock_id", "stockId") or "")
    name = _record_field(record, "stock_name", "stockName")
    technical_stage = _record_field(record, "technical_stage", "technicalStage")
    rejection_reason = _record_field(record, "rejection_reason", "rejectionReason")
    if rejection_reason is not None:
        rejection_reason = str(rejection_reason) or None
    if technical_stage is not None:
        technical_stage = str(technical_stage) or None

    monthly_raw = _record_field(record, "monthly")
    daily_raw = _record_field(record, "daily")
    coverage_raw = _record_field(record, "coverage")
    coverage = coverage_raw if isinstance(coverage_raw, dict) else None

    monthly = _indicator_payload(monthly_raw)
    daily_full = _indicator_payload(daily_raw)
    not_run_reason = _daily_not_run_reason(daily_full)

    insufficient = _insufficient_history(
        timeframe="monthly",
        payload=monthly,
        coverage=coverage,
        rejection_reason=rejection_reason,
    )
    if insufficient is None:
        insufficient = _insufficient_history(
            timeframe="daily",
            payload=daily_full,
            coverage=coverage,
            rejection_reason=rejection_reason,
        )

    # Daily not-run-by-design: null indicators + notRunReason (never a fail).
    if not_run_reason is not None:
        daily_out: dict[str, Any] | None = None
    else:
        daily_out = daily_full

    overall = _overall_stage_result(
        technical_stage=technical_stage,
        monthly=monthly,
        daily=daily_full,
        rejection_reason=rejection_reason,
        insufficient=insufficient,
    )

    row: dict[str, Any] = {
        "stockId": stock_id,
        "name": str(name) if name else None,
        "monthly": monthly,
        "daily": daily_out,
        "overallStageResult": overall,
        "technicalStage": technical_stage,
        "insufficientHistory": insufficient,
        "rejectionReason": rejection_reason,
        "dailyNotRunReason": not_run_reason,
    }
    return row


def build_technical_results_payload(
    *,
    run: Any,
    state: ScanState,
    phase2_execution: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the GET technical-results response body from a owned scan."""
    snap = state.snapshot()
    rows: list[dict[str, Any]] = []
    for stock_id in state.stock_ids:
        record = state.records.get(stock_id)
        if record is None:
            rows.append(
                {
                    "stockId": stock_id,
                    "name": None,
                    "monthly": None,
                    "daily": None,
                    "overallStageResult": "pending",
                    "technicalStage": TECHNICAL_STAGE_PENDING,
                    "insufficientHistory": None,
                    "rejectionReason": None,
                }
            )
            continue
        rows.append(map_symbol_technical_result(record))

    analyzed = 0
    insufficient_count = 0
    for row in rows:
        if row.get("overallStageResult") == "pending":
            continue
        analyzed += 1
        if row.get("insufficientHistory") is not None or row.get(
            "overallStageResult"
        ) == "insufficient_history":
            insufficient_count += 1

    # Prefer honesty-block counters when present.
    if isinstance(phase2_execution, dict):
        if phase2_execution.get("analyzedCount") is not None:
            try:
                analyzed = int(phase2_execution["analyzedCount"])
            except (TypeError, ValueError):
                pass
        if phase2_execution.get("insufficientHistoryCount") is not None:
            try:
                insufficient_count = int(phase2_execution["insufficientHistoryCount"])
            except (TypeError, ValueError):
                pass

    price_basis = snap.get("priceBasis")
    if not price_basis and rows:
        monthly0 = rows[0].get("monthly")
        if isinstance(monthly0, dict) and monthly0.get("priceBasis"):
            price_basis = monthly0.get("priceBasis")
    if not price_basis:
        price_basis = PRICE_BASIS

    return {
        "researchRunId": getattr(run, "research_run_id", None),
        "technicalScanId": state.scan_id,
        "scanStatus": str(snap.get("status") or state.status),
        "tradingDay": state.trading_day,
        "calculationVersion": state.calculation_version,
        "priceBasis": price_basis,
        "universeSize": len(state.stock_ids),
        "processedCount": int(snap.get("processedCount") or 0),
        "remainingCount": int(snap.get("remainingCount") or 0),
        "analyzedCount": analyzed,
        "insufficientHistoryCount": insufficient_count,
        "phase2Execution": phase2_execution,
        "candidates": rows,
        "requiredBars": {
            "monthly": MIN_MONTHLY_BARS_FOR_MACD_KD,
            "daily": MIN_DAILY_BARS_FOR_MACD_KD,
        },
    }
