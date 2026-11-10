"""Evaluation / unresolved counters for Taiwan research (#329).

Distinguishes ``processedCount`` (any persisted ledger row) from definitive
technical outcomes. Budget-exhausted and source-incomplete rows are never
treated as Top-N recommendations or full-coverage claims.
"""

from __future__ import annotations

from typing import Any

from app.services.tw_research.constants import HISTORY_BUDGET_EXHAUSTED_REASON


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
