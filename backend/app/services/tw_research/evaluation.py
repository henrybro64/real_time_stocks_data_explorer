"""Evaluation / unresolved counters for Taiwan research (#329/#336).

Distinguishes ``processedCount`` (any persisted ledger row) from definitive
technical outcomes. Budget-exhausted and source-incomplete rows are never
treated as Top-N recommendations or full-coverage claims.

Stage-pass counters are derived from persisted symbol evidence so
``dayPassCount`` always means daily MACD/KD pass, never monthly-first gates.
"""

from __future__ import annotations

from typing import Any

from app.services.tw_research.constants import (
    CALCULATION_VERSION_MONTHLY_FIRST,
    DAILY_NOT_RUN_BY_DESIGN,
    HISTORY_BUDGET_EXHAUSTED_REASON,
    STAGE_ORDER_DAILY_FIRST,
    STAGE_ORDER_MONTHLY_FIRST,
    WAITING_FOR_NEXT_WINDOW,
)

# Explicit semantics version for progress/snapshot stage counters (#336).
STAGE_PROGRESS_SEMANTICS_VERSION = "stage-progress-v1"


def _stage_order_for(calculation_version: str) -> str:
    if calculation_version == CALCULATION_VERSION_MONTHLY_FIRST or (
        "monthly_first" in (calculation_version or "")
    ):
        return STAGE_ORDER_MONTHLY_FIRST
    return STAGE_ORDER_DAILY_FIRST


def evaluation_counts(
    *,
    stock_ids: list[str] | tuple[str, ...],
    records: dict[str, Any],
    cursor: int,
) -> dict[str, int]:
    """Return additive evaluation counters for snapshots and progress."""
    from app.services.tw_research.export import has_definitive_screening_outcome

    universe = list(stock_ids)
    total = len(universe)
    processed = len(records)
    definitive = 0
    budget_exhausted = 0
    source_incomplete = 0
    unknown_or_unresolved = 0
    for stock_id in universe:
        record = records.get(stock_id)
        if record is None:
            continue
        rejection = str(getattr(record, "rejection_reason", "") or "")
        readiness = getattr(record, "readiness", None)
        if rejection == HISTORY_BUDGET_EXHAUSTED_REASON:
            budget_exhausted += 1
            continue
        if readiness == "source_incomplete":
            source_incomplete += 1
            continue
        if has_definitive_screening_outcome(record):
            definitive += 1
        else:
            unknown_or_unresolved += 1

    unprocessed = max(0, total - processed)
    # Cursor can advance past budget-exhausted rows; remaining uses watermark.
    remaining = max(0, total - min(int(cursor), total))
    return {
        "processedCount": processed,
        "definitiveEvaluatedCount": definitive,
        "budgetExhaustedCount": budget_exhausted,
        "sourceIncompleteCount": source_incomplete,
        "unresolvedCount": unknown_or_unresolved + budget_exhausted + source_incomplete,
        "unprocessedCount": unprocessed,
        "remainingCount": remaining,
    }


def _pass_status(record: Any, timeframe: str) -> str | None:
    payload = getattr(record, timeframe, None)
    if isinstance(payload, dict):
        value = payload.get("passStatus")
        return str(value) if value is not None else None
    return None


def _coverage_dict(record: Any) -> dict[str, Any]:
    coverage = getattr(record, "coverage", None)
    return coverage if isinstance(coverage, dict) else {}


def derive_stage_progress(
    *,
    stock_ids: list[str] | tuple[str, ...],
    records: dict[str, Any],
    calculation_version: str,
) -> dict[str, Any]:
    """Derive unambiguous stage counters from committed symbol evidence.

    ``dayPass*`` always means daily MACD/KD passStatus=pass.
    ``monthPass*`` always means monthly MACD/KD passStatus=pass.
    ``firstStagePass*`` follows ``stageOrder`` (daily-first vs monthly-first).
    """
    stage_order = _stage_order_for(calculation_version)
    monthly_first = stage_order == STAGE_ORDER_MONTHLY_FIRST

    day_pass_ids: list[str] = []
    month_pass_ids: list[str] = []
    dual_pass_ids: list[str] = []
    daily_deferred_ids: list[str] = []
    daily_not_run_ids: list[str] = []
    monthly_incomplete_ids: list[str] = []
    monthly_fail_watchlist_ids: list[str] = []

    for stock_id in stock_ids:
        record = records.get(stock_id)
        if record is None:
            continue
        daily_pass = _pass_status(record, "daily")
        monthly_pass = _pass_status(record, "monthly")
        coverage = _coverage_dict(record)
        daily_cov = coverage.get("daily") if isinstance(coverage.get("daily"), dict) else {}
        daily_payload = getattr(record, "daily", None)
        daily_exec = None
        if isinstance(daily_payload, dict):
            daily_exec = daily_payload.get("executionStatus") or daily_cov.get(
                "executionStatus"
            )
        readiness = getattr(record, "readiness", None)

        if daily_pass == "pass":
            day_pass_ids.append(stock_id)
        if monthly_pass == "pass":
            month_pass_ids.append(stock_id)
        if daily_pass == "pass" and monthly_pass == "pass":
            dual_pass_ids.append(stock_id)

        deferred = bool(daily_cov.get("deferred"))
        deferred_reason = str(daily_cov.get("reason") or "")
        not_run = daily_exec == DAILY_NOT_RUN_BY_DESIGN or (
            deferred and daily_pass is None
        )
        if not_run:
            daily_deferred_ids.append(stock_id)
            if daily_exec == DAILY_NOT_RUN_BY_DESIGN or deferred_reason in {
                "monthly_screen_failed",
                "monthly_indicators_incomplete",
            }:
                daily_not_run_ids.append(stock_id)
            if deferred_reason == "monthly_indicators_incomplete" or readiness == (
                "source_incomplete"
            ):
                monthly_incomplete_ids.append(stock_id)
            if monthly_pass == "fail" or deferred_reason == "monthly_screen_failed":
                monthly_fail_watchlist_ids.append(stock_id)
        elif monthly_pass == "unknown" and daily_pass is None and monthly_first:
            monthly_incomplete_ids.append(stock_id)
            if stock_id not in daily_deferred_ids:
                daily_deferred_ids.append(stock_id)
                daily_not_run_ids.append(stock_id)

    first_stage_ids = month_pass_ids if monthly_first else day_pass_ids
    return {
        "stageProgressSemanticsVersion": STAGE_PROGRESS_SEMANTICS_VERSION,
        "stageOrder": stage_order,
        "dayPassCount": len(day_pass_ids),
        "dayPassIds": list(day_pass_ids),
        "monthPassCount": len(month_pass_ids),
        "monthPassIds": list(month_pass_ids),
        "firstStagePassCount": len(first_stage_ids),
        "firstStagePassIds": list(first_stage_ids),
        "dualPassCount": len(dual_pass_ids),
        "dualPassIds": list(dual_pass_ids),
        "dailyDeferredCount": len(daily_deferred_ids),
        "dailyDeferredIds": list(daily_deferred_ids),
        "dailyNotRunByDesignCount": len(daily_not_run_ids),
        "dailyNotRunByDesignIds": list(daily_not_run_ids),
        "monthlyIncompleteCount": len(monthly_incomplete_ids),
        "monthlyIncompleteIds": list(monthly_incomplete_ids),
        "monthlyFailWatchlistCount": len(monthly_fail_watchlist_ids),
        "monthlyFailWatchlistIds": list(monthly_fail_watchlist_ids),
        # Legacy alias: dayPass remains daily-pass; UI must use stageOrder labels.
        "firstStageLabel": (
            STAGE_ORDER_MONTHLY_FIRST if monthly_first else STAGE_ORDER_DAILY_FIRST
        ),
    }


def map_recoverable_status(status: str) -> str:
    """Map legacy recoverable pauses to waiting_for_next_window (#341).

    Only explicitly recoverable pauses (``deferred``, ``waiting_for_next_window``)
    map here. Finished-but-incomplete ``partial`` stays ``partial`` so analysts
    can still gate on reviewable evidence. Hard-cap ``budget_exhausted`` stays
    distinct so operators do not confuse temporary waits with terminal caps.
    """
    if status in {"deferred", WAITING_FOR_NEXT_WINDOW}:
        return WAITING_FOR_NEXT_WINDOW
    return status
