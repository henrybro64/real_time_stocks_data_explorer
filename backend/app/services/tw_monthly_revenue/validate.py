"""Dataset-level validation before publishing an accepted revenue snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from app.services.tw_monthly_revenue.parse import REQUIRED_KEYS, ParsedRevenueRow


# First-accept floors (below observed market-wide sizes from 2026-11-27 feasibility).
_MIN_ROWS_BY_BOARD = {
    "TWSE": 800,
    "TPEx": 600,
}
_PRIOR_COMPLETENESS_RATIO = 0.90
VALIDATION_ERROR_CAP = 50


@dataclass(frozen=True)
class DatasetValidation:
    ok: bool
    errors: list[str]
    reporting_period_yyyymm: str | None
    report_generated_date_iso: str | None
    row_count: int
    accepted_rows: list[ParsedRevenueRow]
    row_rejects_total: int


def cap_validation_errors(
    errors: list[str],
    *,
    total_label: str = "validation_errors_total",
    limit: int = VALIDATION_ERROR_CAP,
) -> list[str]:
    """Keep at most ``limit`` detail entries plus a total-count sentinel."""
    if not errors:
        return []
    detail = [err for err in errors if not str(err).startswith(f"{total_label}:")]
    # Prefer an explicit row_rejects_total already present in the list.
    row_total_lines = [
        err for err in detail if str(err).startswith("row_rejects_total:")
    ]
    other = [err for err in detail if not str(err).startswith("row_rejects_total:")]
    capped = other[:limit]
    result = [*capped, *row_total_lines[:1]]
    if not row_total_lines:
        result.append(f"{total_label}:{len(detail)}")
    elif len(other) > limit and not any(
        str(err).startswith(f"{total_label}:") for err in result
    ):
        result.append(f"{total_label}:{len(detail)}")
    return result


def max_tolerated_row_rejects(total_rows: int) -> int:
    """Accept sparse bad rows; reject when rejects exceed max(3, ceil(0.5% of total))."""
    if total_rows <= 0:
        return 0
    return max(3, ceil(total_rows * 0.005))


def validate_revenue_dataset(
    rows: list[ParsedRevenueRow],
    *,
    board: str,
    parse_errors: list[str] | None = None,
    row_rejects: list[str] | None = None,
    row_warnings: list[str] | None = None,
    dataset_errors: list[str] | None = None,
    prior_accepted_row_count: int | None = None,
    payload_total_rows: int | None = None,
) -> DatasetValidation:
    """Separate dataset-fatal errors from bounded per-row rejects.

    Non-fatal per-row warnings (e.g. ``invalid_report_generated_date``) never reject.
    Rows without current-month revenue are excluded upstream and never stored.
    """
    fatal: list[str] = list(dataset_errors or [])
    rejects = list(row_rejects or [])
    warnings = list(row_warnings or [])

    # Backward-compatible flat parse_errors from older call sites / tests.
    if parse_errors:
        for err in parse_errors:
            if err in {"payload_not_array", "empty_dataset"}:
                if err not in fatal:
                    fatal.append(err)
            elif err.startswith("row[") and err.endswith(":invalid_report_generated_date"):
                if err not in warnings:
                    warnings.append(err)
            elif err.startswith("row["):
                if err not in rejects:
                    rejects.append(err)
            elif "invalid_report_generated_date" in err:
                if err not in warnings:
                    warnings.append(err)
            elif err not in fatal and err not in rejects and err not in warnings:
                # Unknown flat errors from older parsers — treat as fatal only when
                # they are clearly dataset-level markers.
                if any(
                    marker in err
                    for marker in (
                        "mixed_reporting_periods",
                        "duplicate_company_codes",
                        "incomplete_dataset",
                        "incomplete_vs_prior",
                        "no_valid_rows",
                    )
                ):
                    fatal.append(err)

    if "payload_not_array" in fatal or "empty_dataset" in fatal:
        return DatasetValidation(
            ok=False,
            errors=cap_validation_errors(fatal),
            reporting_period_yyyymm=None,
            report_generated_date_iso=None,
            row_count=0,
            accepted_rows=[],
            row_rejects_total=len(rejects),
        )

    # Never store rows that lack current-month revenue.
    accepted_rows = [row for row in rows if row.revenue_nt_thousand is not None]
    if not accepted_rows:
        fatal.append("no_valid_rows")
        errors = [*fatal, *_bounded_row_reject_errors(rejects), *warnings]
        return DatasetValidation(
            ok=False,
            errors=cap_validation_errors(errors),
            reporting_period_yyyymm=None,
            report_generated_date_iso=None,
            row_count=0,
            accepted_rows=[],
            row_rejects_total=len(rejects),
        )

    periods = {row.period_yyyymm for row in accepted_rows}
    if len(periods) != 1:
        fatal.append(f"mixed_reporting_periods:{sorted(periods)}")
    period = next(iter(periods)) if len(periods) == 1 else None

    generated = {
        row.report_generated_date.isoformat()
        for row in accepted_rows
        if row.report_generated_date is not None
    }
    generated_iso = (
        next(iter(generated))
        if len(generated) == 1
        else (sorted(generated)[-1] if generated else None)
    )
    # Missing / mixed 出表日期 are non-fatal (never reject the board).
    if not generated:
        warnings.append("missing_report_generated_date")
    if len(generated) > 1:
        warnings.append(f"mixed_report_generated_dates:{sorted(generated)}")

    codes = [row.company_code for row in accepted_rows]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        fatal.append(f"duplicate_company_codes:{duplicates[:20]}")

    min_rows = _MIN_ROWS_BY_BOARD.get(board, 1)
    if prior_accepted_row_count is None:
        if len(accepted_rows) < min_rows:
            fatal.append(
                f"incomplete_dataset:row_count={len(accepted_rows)}<min={min_rows}"
            )
    else:
        floor = max(min_rows, int(prior_accepted_row_count * _PRIOR_COMPLETENESS_RATIO))
        if len(accepted_rows) < floor:
            fatal.append(
                "incomplete_vs_prior:"
                f"row_count={len(accepted_rows)}<floor={floor}"
                f"(prior={prior_accepted_row_count})"
            )

    total_rows = (
        payload_total_rows
        if payload_total_rows is not None
        else len(accepted_rows) + len(rejects)
    )
    tolerance = max_tolerated_row_rejects(total_rows)
    if len(rejects) > tolerance:
        fatal.append(
            f"excessive_row_rejects:count={len(rejects)}>tolerance={tolerance}"
            f"(total={total_rows})"
        )

    for row in accepted_rows[:5]:
        if not row.company_code or not row.period_yyyymm:
            fatal.append("row_missing_identity")
            break

    _ = REQUIRED_KEYS
    errors = [*fatal, *_bounded_row_reject_errors(rejects), *warnings]
    return DatasetValidation(
        ok=not fatal,
        errors=cap_validation_errors(errors),
        reporting_period_yyyymm=period,
        report_generated_date_iso=generated_iso,
        row_count=len(accepted_rows),
        accepted_rows=accepted_rows,
        row_rejects_total=len(rejects),
    )


def _bounded_row_reject_errors(rejects: list[str]) -> list[str]:
    if not rejects:
        return []
    return [*rejects[:VALIDATION_ERROR_CAP], f"row_rejects_total:{len(rejects)}"]
