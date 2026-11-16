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
# Completed numerical (or intentional not-run) indicator calculations.
_CALCULATED_INDICATOR_RESULTS = frozenset(
    {
        "pass",
        "fail",
        "monthly_pass",
        "monthly_fail",
        "daily_not_run",
    }
)
# Evidence-complete outcomes with defined pass/fail (or intentional daily skip).
_VERIFIED_INDICATOR_RESULTS = frozenset({"pass", "fail", "daily_not_run"})
_UNKNOWN_INDICATOR_RESULTS = frozenset({"monthly_unknown", "daily_unknown"})
_DEFERRED_INDICATOR_RESULTS = frozenset({"deferred"})
_PASS_RULE_META = {
    "macdHistogramConvention": "OSC = DIF − Signal (MACD histogram)",
    "macdFormula": "DIF = EMA12(close) − EMA26(close); Signal = EMA9(DIF)",
    "kdFormula": "KD(9,3,3): RSV from 9-bar high/low; K/D smoothed",
    "componentsTested": [
        {"component": "dif", "threshold": "> 0"},
        {"component": "osc", "threshold": "> 0", "note": "histogram = DIF − Signal"},
        {"component": "k", "threshold": "> 50"},
        {"component": "d", "threshold": "> 50"},
    ],
    "passRule": "DIF > 0 AND OSC > 0 AND K > 50 AND D > 50 (per timeframe)",
    "monthlyAsOfPolicy": (
        "Last fully completed calendar month only; the current incomplete "
        "month is never treated as a verified monthly bar."
    ),
    "priceBasisPolicy": "unadjusted_close",
    # New calculations persist provider + bar range; older rows may be unavailable.
    "sourceProviderPersisted": "when_present_on_snapshot",
    "historyRangePersisted": "when_present_on_snapshot",
}


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
    first_bar = raw.get("firstBarAsOf")
    last_bar = raw.get("lastBarAsOf")
    provider = raw.get("sourceProvider")
    calculated_at = raw.get("calculatedAt")
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
        "sourceProvider": str(provider) if provider else None,
        "firstBarAsOf": str(first_bar) if first_bar else None,
        "lastBarAsOf": str(last_bar) if last_bar else None,
        "calculatedAt": str(calculated_at) if calculated_at else None,
        "sourceProviderStatus": "available" if provider else "unavailable",
        "historyRangeStatus": (
            "available" if first_bar and last_bar else "unavailable"
        ),
    }


def _is_budget_related_reason(reason: str | None) -> bool:
    return bool(reason) and str(reason) in _BUDGET_RELATED_REASONS


def _is_symbol_error_reason(reason: str | None) -> bool:
    text = str(reason or "")
    return text.startswith("symbol_error:") or text == "symbol_error"


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


def _component_eval(
    *,
    name: str,
    value: float | None,
    threshold: str,
    passed: bool | None,
) -> dict[str, Any]:
    return {
        "component": name,
        "value": value,
        "threshold": threshold,
        "passed": passed,
        "missing": value is None,
    }


def _pass_evaluation(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Numerical pass/fail components from a persisted snapshot (no recompute)."""
    if not isinstance(payload, dict):
        return None
    dif = _as_float(payload.get("dif"))
    osc = _as_float(payload.get("osc"))
    k_value = _as_float(payload.get("k"))
    d_value = _as_float(payload.get("d"))
    quality = str(payload.get("quality") or "")
    if quality and quality != "ok":
        return {
            "passStatus": str(payload.get("passStatus") or "unknown"),
            "quality": quality,
            "components": [
                _component_eval(name="dif", value=dif, threshold="> 0", passed=None),
                _component_eval(name="osc", value=osc, threshold="> 0", passed=None),
                _component_eval(name="k", value=k_value, threshold="> 50", passed=None),
                _component_eval(name="d", value=d_value, threshold="> 50", passed=None),
            ],
        }
    if None in (dif, osc, k_value, d_value):
        return {
            "passStatus": str(payload.get("passStatus") or "unknown"),
            "quality": quality or None,
            "components": [
                _component_eval(name="dif", value=dif, threshold="> 0", passed=None),
                _component_eval(name="osc", value=osc, threshold="> 0", passed=None),
                _component_eval(name="k", value=k_value, threshold="> 50", passed=None),
                _component_eval(name="d", value=d_value, threshold="> 50", passed=None),
            ],
        }
    assert dif is not None and osc is not None and k_value is not None and d_value is not None
    components = [
        _component_eval(name="dif", value=dif, threshold="> 0", passed=dif > 0),
        _component_eval(name="osc", value=osc, threshold="> 0", passed=osc > 0),
        _component_eval(name="k", value=k_value, threshold="> 50", passed=k_value > 50),
        _component_eval(name="d", value=d_value, threshold="> 50", passed=d_value > 50),
    ]
    overall_pass = all(bool(item["passed"]) for item in components)
    return {
        "passStatus": str(payload.get("passStatus") or ("pass" if overall_pass else "fail")),
        "quality": quality or "ok",
        "components": components,
    }


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
    # Persisted loader/indicator failures are terminal for the symbol (no retry
    # via schedule_has_technical_work) — never label them pending.
    if _is_symbol_error_reason(rejection_reason):
        return "error"
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

    monthly_eval = _pass_evaluation(monthly) if monthly is not None else None
    daily_eval = (
        None
        if not_run_reason is not None
        else _pass_evaluation(daily_full)
        if daily_full is not None
        else None
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
        "monthlyPassEvaluation": monthly_eval,
        "dailyPassEvaluation": daily_eval,
    }


def build_technical_results_payload(
    *,
    run: Any | None,
    state: ScanState,
    phase2_execution: dict[str, Any] | None,
    viewing_historical: bool = False,
    is_current_run_scan: bool = True,
) -> dict[str, Any]:
    """Assemble the GET technical-results response body from an owned scan."""
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
                    "monthlyPassEvaluation": None,
                    "dailyPassEvaluation": None,
                }
            )
            continue
        rows.append(
            map_symbol_technical_result(record, phase2_execution=phase2_execution)
        )

    # Prefer row-derived non-overlapping counts; fall back only when no rows.
    calculated = 0
    verified = 0
    error_count = 0
    insufficient_count = 0
    waiting_count = 0
    pending_count = 0
    deferred_count = 0
    unknown_count = 0
    if rows:
        for row in rows:
            overall = str(row.get("overallStageResult") or "")
            if overall == "pending":
                pending_count += 1
            elif overall == "waiting_for_history":
                waiting_count += 1
            elif overall == "error":
                error_count += 1
            elif (
                row.get("insufficientHistory") is not None
                or overall == "insufficient_history"
            ):
                insufficient_count += 1
            elif overall in _DEFERRED_INDICATOR_RESULTS:
                deferred_count += 1
            elif overall in _UNKNOWN_INDICATOR_RESULTS:
                unknown_count += 1
            elif overall in _CALCULATED_INDICATOR_RESULTS:
                calculated += 1
                if overall in _VERIFIED_INDICATOR_RESULTS:
                    verified += 1
            else:
                # Unclassified outcomes stay pending for reconciliation honesty.
                pending_count += 1
    elif isinstance(phase2_execution, dict):
        try:
            if phase2_execution.get("analyzedCount") is not None:
                calculated = int(phase2_execution["analyzedCount"])
                verified = calculated
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

    scan_updated_at = snap.get("updatedAt") or getattr(state, "updated_at", None)
    def _row_has_provider(row: dict[str, Any]) -> bool:
        monthly = row.get("monthly")
        daily = row.get("daily")
        return bool(
            (isinstance(monthly, dict) and monthly.get("sourceProvider"))
            or (isinstance(daily, dict) and daily.get("sourceProvider"))
        )

    def _row_has_range(row: dict[str, Any]) -> bool:
        monthly = row.get("monthly")
        daily = row.get("daily")
        return bool(
            (
                isinstance(monthly, dict)
                and monthly.get("firstBarAsOf")
                and monthly.get("lastBarAsOf")
            )
            or (
                isinstance(daily, dict)
                and daily.get("firstBarAsOf")
                and daily.get("lastBarAsOf")
            )
        )

    any_provider = any(_row_has_provider(row) for row in rows)
    any_range = any(_row_has_range(row) for row in rows)
    pass_rule = dict(_PASS_RULE_META)
    pass_rule["sourceProviderPersisted"] = bool(any_provider)
    pass_rule["historyRangePersisted"] = bool(any_range)

    return {
        "researchRunId": getattr(run, "research_run_id", None) if run is not None else None,
        "technicalScanId": state.scan_id,
        "scanStatus": str(snap.get("status") or state.status),
        "tradingDay": state.trading_day,
        "calculationVersion": state.calculation_version,
        "priceBasis": price_basis,
        "scanUpdatedAt": scan_updated_at,
        "universeSize": len(state.stock_ids),
        "processedCount": int(snap.get("processedCount") or 0),
        "remainingCount": int(snap.get("remainingCount") or 0),
        # analyzedCount kept as calculatedCount alias for existing consumers.
        "analyzedCount": calculated,
        "calculatedCount": calculated,
        "verifiedCount": verified,
        "errorCount": error_count,
        "pendingCount": pending_count,
        "deferredCount": deferred_count,
        "unknownCount": unknown_count,
        "insufficientHistoryCount": insufficient_count,
        "waitingForHistoryCount": waiting_count,
        "viewingHistorical": bool(viewing_historical),
        "isCurrentRunScan": bool(is_current_run_scan),
        "phase2Execution": phase2_execution if is_current_run_scan else None,
        "passRule": pass_rule,
        "candidates": rows,
        "requiredBars": {
            "monthly": MIN_MONTHLY_BARS_FOR_MACD_KD,
            "daily": MIN_DAILY_BARS_FOR_MACD_KD,
        },
    }
