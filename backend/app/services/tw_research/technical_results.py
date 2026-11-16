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

_PHASE2_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_BUDGET_RELATED_REASONS = frozenset(
    {
        HISTORY_BUDGET_EXHAUSTED_REASON,
        "budget_exhausted",
        "history_budget_exhausted",
    }
)


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


def _is_budget_related_reason(reason: str | None) -> bool:
    return bool(reason) and str(reason) in _BUDGET_RELATED_REASONS


def _phase2_treats_budget_as_insufficient(
    phase2_execution: dict[str, Any] | None,
) -> bool:
    """True when budget exhaustion should surface as insufficient_history.

    Waiting-for-budget (non-terminal, non-cap) stays ``waiting_for_history``.
    Completed / failed / cancelled / daily-cap-reached keep insufficient_history.
    """
    if not isinstance(phase2_execution, dict):
        return False
    state = str(phase2_execution.get("state") or "")
    if state in _PHASE2_TERMINAL_STATES:
        return True
    blocker = str(phase2_execution.get("blocker") or "")
    if blocker == "phase2_budget_cap_reached":
        return True
    if state == "waiting_for_budget_window" and blocker in {
        "phase2_budget_cap_reached",
        "budget_cap_reached",
    }:
        return True
    return False


def _coverage_budget_reason(coverage: dict[str, Any] | None) -> str | None:
    if not isinstance(coverage, dict):
        return None
    for timeframe in ("monthly", "daily"):
        frame = coverage.get(timeframe)
        if not isinstance(frame, dict):
            continue
        reason = str(frame.get("reason") or "")
        if _is_budget_related_reason(reason):
            return reason
    return None


def _insufficient_history(
    *,
    timeframe: str,
    payload: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
    rejection_reason: str | None,
    include_budget_exhausted: bool,
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
            if "barCount" in payload:
                available = _as_int(payload.get("barCount"))
        elif str(payload.get("executionStatus") or "") == "insufficient_history":
            reason = "insufficient_history"
            if "barCount" in payload:
                available = _as_int(payload.get("barCount"))

    if isinstance(coverage, dict):
        frame = coverage.get(timeframe)
        if isinstance(frame, dict):
            cov_reason = str(frame.get("reason") or "")
            if cov_reason in {"insufficient_history", "insufficient_monthly_history"}:
                reason = reason or cov_reason
                if available is None and "barCount" in frame:
                    available = _as_int(frame.get("barCount"))
            elif include_budget_exhausted and _is_budget_related_reason(cov_reason):
                reason = reason or cov_reason
                if available is None and "barCount" in frame:
                    available = _as_int(frame.get("barCount"))

    if include_budget_exhausted and _is_budget_related_reason(rejection_reason):
        if timeframe == "monthly":
            reason = reason or HISTORY_BUDGET_EXHAUSTED_REASON

    if reason is None:
        return None
    return {
        "reason": reason,
        "requiredBars": required,
        # Unknown bar counts stay null (never coerce to 0).
        "availableBars": available,
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
    waiting_for_history: bool,
) -> str:
    if waiting_for_history:
        return "waiting_for_history"
    if insufficient is not None:
        return "insufficient_history"
    if _is_budget_related_reason(rejection_reason):
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
    *,
    phase2_execution: dict[str, Any] | None = None,
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

    budget_signal = _is_budget_related_reason(rejection_reason) or bool(
        _coverage_budget_reason(coverage)
    )
    treat_budget_as_insufficient = _phase2_treats_budget_as_insufficient(
        phase2_execution
    )
    waiting_for_history = bool(budget_signal and not treat_budget_as_insufficient)

    insufficient = None
    if not waiting_for_history:
        insufficient = _insufficient_history(
            timeframe="monthly",
            payload=monthly,
            coverage=coverage,
            rejection_reason=rejection_reason,
            include_budget_exhausted=treat_budget_as_insufficient or not budget_signal,
        )
        if insufficient is None:
            insufficient = _insufficient_history(
                timeframe="daily",
                payload=daily_full,
                coverage=coverage,
                rejection_reason=rejection_reason,
                include_budget_exhausted=treat_budget_as_insufficient
                or not budget_signal,
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
        waiting_for_history=waiting_for_history,
    )

    return {
        "stockId": stock_id,
        "name": str(name) if name else None,
        "monthly": monthly,
        "daily": daily_out,
        "overallStageResult": overall,
        "technicalStage": technical_stage,
        "insufficientHistory": None if waiting_for_history else insufficient,
        "rejectionReason": rejection_reason,
        "dailyNotRunReason": not_run_reason,
    }


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
                    "dailyNotRunReason": None,
                }
            )
            continue
        rows.append(
            map_symbol_technical_result(record, phase2_execution=phase2_execution)
        )

    # Prefer row-derived counts; fall back to phase2Execution only when no rows.
    analyzed = 0
    insufficient_count = 0
    waiting_count = 0
    if rows:
        for row in rows:
            overall = str(row.get("overallStageResult") or "")
            if overall == "pending":
                continue
            analyzed += 1
            if overall == "waiting_for_history":
                waiting_count += 1
            elif (
                row.get("insufficientHistory") is not None
                or overall == "insufficient_history"
            ):
                insufficient_count += 1
    elif isinstance(phase2_execution, dict):
        try:
            if phase2_execution.get("analyzedCount") is not None:
                analyzed = int(phase2_execution["analyzedCount"])
        except (TypeError, ValueError):
            pass
        try:
            if phase2_execution.get("insufficientHistoryCount") is not None:
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
        "waitingForHistoryCount": waiting_count,
        "phase2Execution": phase2_execution,
        "candidates": rows,
        "requiredBars": {
            "monthly": MIN_MONTHLY_BARS_FOR_MACD_KD,
            "daily": MIN_DAILY_BARS_FOR_MACD_KD,
        },
    }
